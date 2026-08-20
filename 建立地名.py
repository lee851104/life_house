# -*- coding: utf-8 -*-
"""建立地名索引：OSM PBF + 事故索引.db → 地名.db

讓使用者可以打字找地點，不必在地圖上瞎點。

為什麼自己建，不接現成服務
    Google Places Autocomplete 的 ToS 要求結果顯示在 Google 地圖上，本專案
    用的是 Leaflet + OSM 底圖；Nominatim 公共服務的使用政策明文禁止
    autocomplete 這種高頻查詢。自架 Nominatim / Photon 要 PostGIS 或
    Elasticsearch，對一個 420 MB 常駐的單檔 FastAPI 太重。
    需要的資料（OSM PBF、事故發生地點）本來就在硬碟上，自己建最省。

索引的單位是「路段」而不是「門牌」
    桃園市界內光 OSM 就有 100 萬個門牌點。全部丟進 FTS 索引，光索引就好幾
    百 MB，而且排序會被門牌雜訊淹沒——使用者打「中山路」會拿到一整排
    「中山路1號」「中山路3號」而不是中山路本身。
    所以可搜尋的單位只有三種：路段、POI、路口（數萬筆），門牌另外放在非
    FTS 的 addr 表，用 (路段, 號) 精確查。這是真實地理編碼器的做法。

中文模糊搜尋靠 FTS5 的 trigram tokenizer
    FTS5 預設的 tokenizer 不切中文，「中壢環北路」會被當成單一 token，
    搜不到東西。trigram 改成字元三連組，等於子字串比對，中文就通了。
    Python 3.13 內建的 SQLite 3.51 已含此 tokenizer，不必外掛。

記憶體
    掃 way 需要 flex_mem 載入全台節點座標（約 2 GB）。門牌若同時堆在 Python
    list 會再吃幾百 MB，所以邊掃邊分批寫進 addr_raw 暫存表，記憶體維持平坦。

用法
    python 建立地名.py            # 約 10 分鐘，需要 raw/taiwan-latest.osm.pbf
"""
import os
import re
import sqlite3
import time
from collections import defaultdict

import numpy as np

import 地名正規化 as N

BASE = os.path.dirname(os.path.abspath(__file__))
PBF = os.path.join(BASE, "raw", "taiwan-latest.osm.pbf")
DB_IN = os.path.join(BASE, "事故索引.db")
BND = os.path.join(BASE, "市界.npz")
NET = os.path.join(BASE, "路網.npz")
OUT = os.path.join(BASE, "地名.db")

# 這些 OSM 標籤代表「人會拿來當地標的東西」。權重是搜尋排序的先驗：
# 使用者打「桃園」時，火車站要排在某家叫桃園的小吃店前面。
POI_W = {
    "railway:station": 100, "aeroway:aerodrome": 100, "amenity:university": 90,
    "amenity:hospital": 90, "railway:halt": 80, "amenity:college": 80,
    "shop:mall": 80, "amenity:townhall": 80, "shop:department_store": 75,
    "amenity:school": 70, "tourism:theme_park": 70, "tourism:zoo": 60,
    "leisure:water_park": 55, "tourism:attraction": 60, "amenity:library": 55,
    "amenity:police": 55, "amenity:fire_station": 50, "amenity:post_office": 50,
    "tourism:museum": 50, "amenity:kindergarten": 40, "amenity:clinic": 35,
    "shop:supermarket": 35, "amenity:pharmacy": 30, "amenity:bank": 30,
    "amenity:place_of_worship": 30, "leisure:park": 30, "amenity:fuel": 25,
    "amenity:community_centre": 25, "shop:convenience": 20,
    "amenity:restaurant": 15, "amenity:parking": 12,
}
POI_KEYS = ("railway", "aeroway", "amenity", "shop", "tourism", "leisure")
# 「小人國主題樂園」這種招牌名稱在 OSM 裡可能掛在 tourism=theme_park 的面上，
# 也可能只有一個沒有分類標籤的 name。上面的白名單擋掉後者是刻意的：不設門檻
# 的話，桃園上千個埤塘與測量控制點會把搜尋結果淹掉。
# 公車站牌與測量控制點在 OSM 裡數以千計，沒有人會拿它們當「我家在哪」的
# 定位參考，全部擋在索引外。
POI_MIN_W = 12

MW_RE = re.compile(r"國道|快速|交流道|匝道|公里")
BATCH = 50_000


# 正式名稱裡會被口語省略的成分。台灣的機構名幾乎都有這層落差：
#   臺灣桃園國際機場 → 桃園機場      林口長庚紀念醫院 → 林口長庚醫院
#   國立中央大學     → 中央大學      私立萬能科技大學 → 萬能科技大學
# 不補別名的話，「桃園機場」會輸給「桃園機場消防隊北站」——後者的名字剛好
# 以查詢字串開頭，前綴命中拿的分數比非連續命中高得多。
FORMAL = ("國立", "市立", "縣立", "私立", "臺灣", "台灣", "國際", "紀念",
          "股份有限公司", "有限公司")


def aliases(name, kind):
    """使用者會怎麼叫這個地方，但 OSM 沒有那樣寫。"""
    out = []
    # 台鐵車站在 OSM 裡的 name 就是「桃園」「中壢」「內壢」「楊梅」——沒有人
    # 會這樣搜。不補的話，打「桃園車站」只會撈到一家叫「車站腿庫飯」的小吃店。
    if kind in ("station", "halt") and not name.endswith(("站", "車站")):
        out += [name + "站", name + "車站", name + "火車站"]
    # 逐一拿掉正式成分，再拿掉全部——「臺灣桃園國際機場」要同時產生
    # 「桃園國際機場」「臺灣桃園機場」和「桃園機場」三種說法。
    whole = name
    for f in FORMAL:
        if f in name:
            one = name.replace(f, "")
            if len(one) >= 3:
                out.append(one)
            whole = whole.replace(f, "")
    if len(whole) >= 3 and whole != name:
        out.append(whole)
    return [a for a in dict.fromkeys(out) if a != name]

# 桃園 13 個行政區。掃描時只能用 bbox 濾（逐點做多邊形判定太慢），那個框把
# 林口、樹林、鶯歌、新竹湖口都包進來了——2,222,807 筆門牌裡只有約 100 萬
# 在桃園。留著不只是浪費：它們會混進「這個座標屬於哪一區」的參考點，讓
# 市界邊緣的桃園道路被標成林口區。
TAOYUAN = ("桃園區", "中壢區", "平鎮區", "八德區", "楊梅區", "蘆竹區",
           "龜山區", "大溪區", "大園區", "觀音區", "新屋區", "龍潭區", "復興區")


# ------------------------------------------------------------------ 幾何
def load_geo():
    from shapely.geometry import Polygon
    from shapely.prepared import prep
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    poly = Polygon(np.load(BND)["ring"])
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
    inv = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    node_tree = cKDTree(np.load(NET)["nodes"].astype(np.float64))
    return poly, prep(poly), fwd, inv, node_tree


def open_out():
    if os.path.exists(OUT):
        os.remove(OUT)
    db = sqlite3.connect(OUT)
    db.executescript("""
      PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-200000;
      CREATE TABLE addr_raw(
        district TEXT, road TEXT, seg TEXT, lane TEXT, alley TEXT, no TEXT,
        x REAL, y REAL);
    """)
    return db


# ------------------------------------------------------------------ OSM
def scan_osm(db, poly, fwd):
    """掃 PBF：門牌串流寫進 addr_raw，路段與 POI 留在記憶體（數萬筆而已）。"""
    import osmium

    x0, y0, x1, y1 = poly.bounds
    roads, pois, buf = defaultdict(list), [], []
    t0 = time.time()

    def flush():
        if buf:
            db.executemany("INSERT INTO addr_raw VALUES(?,?,?,?,?,?,?,?)", buf)
            buf.clear()

    def xy_of(lon, lat):
        x, y = fwd.transform(lon, lat)
        return (x, y) if (x0 <= x <= x1 and y0 <= y <= y1) else None

    def poi_kind(t):
        best = None
        for k in POI_KEYS:
            v = t.get(k)
            key = "%s:%s" % (k, v) if v else None
            if key in POI_W and (best is None or POI_W[key] > best[1]):
                best = (v, POI_W[key])
        return best

    def take_addr(t, x, y):
        """addr:full 最完整（含里鄰樓層，正規化模組都會處理掉）；
        沒有 full 就用 district + street + housenumber 湊。"""
        hn = t.get("addr:housenumber")
        if not hn:
            return
        s = t.get("addr:full")
        if not s:
            s = (t.get("addr:district") or "") + (t.get("addr:street") or "") + hn
            if not s.endswith("號"):
                s += "號"
        p = N.parse(s)
        if p and p["road"] and p["no"]:
            d = p["district"] or t.get("addr:district")
            buf.append((d, p["road"], p["seg"], p["lane"], p["alley"], p["no"], x, y))
            if len(buf) >= BATCH:
                flush()

    print("  掃 node…")
    fp = (osmium.FileProcessor(PBF)
          .with_filter(osmium.filter.EntityFilter(osmium.osm.NODE)))
    n = 0
    for o in fp:
        n += 1
        if n % 40_000_000 == 0:
            print("    node %dM (%.0fs)" % (n // 1_000_000, time.time() - t0))
        t = o.tags
        nm = t.get("name")
        if not nm and "addr:housenumber" not in t:
            continue
        loc = o.location
        if not loc.valid():
            continue
        xy = xy_of(loc.lon, loc.lat)
        if xy is None:
            continue
        take_addr(t, *xy)
        if nm:
            k = poi_kind(t)
            if k and k[1] >= POI_MIN_W:
                pois.append((nm, k[0], k[1], xy[0], xy[1]))
    flush()
    print("    node %d 筆 ｜ POI %d (%.0fs)" % (n, len(pois), time.time() - t0))

    print("  掃 way…（載入節點座標，約 2 GB）")
    fp = (osmium.FileProcessor(PBF)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY)))
    w = 0
    for o in fp:
        w += 1
        t = o.tags
        nm = t.get("name")
        if not nm and "addr:housenumber" not in t:
            continue
        try:
            locs = [nd.location for nd in o.nodes if nd.location.valid()]
        except Exception:
            continue
        if not locs:
            continue
        mid = locs[len(locs) // 2]
        xy = xy_of(mid.lon, mid.lat)
        if xy is None:
            continue
        take_addr(t, *xy)
        if not nm:
            continue
        if "highway" not in t:
            continue                    # 面狀 POI 交給 scan_areas
        if MW_RE.search(nm):            # 國道匝道不是使用者會搜的目的地
            continue
        for one in N.split_locs(nm):
            p = N.parse(one)
            if p and p["road"]:
                # 一條路在 OSM 裡被切成幾十段 way。全部收進來再取中位數，
                # 代表點才會落在路的中段，而不是某一小段的端點。
                # 巷弄（國際路一段133巷）會被 parse 收斂回母路段。
                roads[(p["road"], p["seg"])].append(xy)
    flush()
    db.commit()
    n_addr = db.execute("SELECT COUNT(*) FROM addr_raw").fetchone()[0]
    print("    way %d 筆 ｜ 路段 %d ‧ 門牌 %d (%.0fs)"
          % (w, len(roads), n_addr, time.time() - t0))

    # 面狀 POI 必須走 with_areas，不能只看 way：大學校園、醫院園區、購物中心
    # 在 OSM 裡常常是 multipolygon relation。中原大學、中央警察大學都是
    # relation，只掃 way 的話搜「中原大學」只會撈到校門口的 YouBike 站。
    # with_areas 會把 way 與 relation 都組成面，整份台灣只多 16 秒。
    print("  掃 area（面狀 POI，含 relation）…")
    fp = (osmium.FileProcessor(PBF)
          .with_areas()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)))
    a = 0
    for o in fp:
        t = o.tags
        nm = t.get("name")
        if not nm:
            continue
        k = poi_kind(t)
        if not k or k[1] < POI_MIN_W:
            continue
        try:
            pts = [(n.lon, n.lat) for r in o.outer_rings()
                   for n in r if n.location.valid()]
        except Exception:
            continue
        if not pts:
            continue
        xy = xy_of(sum(p[0] for p in pts) / len(pts),
                   sum(p[1] for p in pts) / len(pts))
        if xy is None:
            continue
        a += 1
        pois.append((nm, k[0], k[1], xy[0], xy[1]))
    print("    面 %d 個 ｜ POI 累計 %d (%.0fs)" % (a, len(pois), time.time() - t0))
    return roads, pois


# ------------------------------------------------------------------ 事故索引
def scan_accidents(db, fwd):
    """從發生地點補路段與門牌，並取出路口。

    OSM 的門牌是一次性匯入，新開發區可能還沒有；警政署的發生地點則是逐年
    更新且每一筆都有座標，兩邊互補。同一條路兩邊都有取樣點時，代表點更準。
    """
    src = sqlite3.connect(DB_IN)
    rows = src.execute(
        "SELECT loc, lat, lon FROM accidents WHERE loc IS NOT NULL").fetchall()
    xs, ys = fwd.transform(np.array([r[2] for r in rows]),
                           np.array([r[1] for r in rows]))
    roads, buf = defaultdict(list), []
    for (loc, _, _), x, y in zip(rows, xs, ys):
        for part in N.split_locs(loc):
            if MW_RE.search(part):
                continue
            p = N.parse(part)
            if not p or not p["road"]:
                continue
            roads[(p["district"], p["road"], p["seg"])].append((x, y))
            if p["no"]:
                buf.append((p["district"], p["road"], p["seg"], p["lane"],
                            p["alley"], p["no"], x, y))
    db.executemany("INSERT INTO addr_raw VALUES(?,?,?,?,?,?,?,?)", buf)
    db.commit()

    # 只要有地面事故的路口，跟 核心.py 的 x_keep 用同一個定義
    xrows = src.execute(
        "SELECT x.name, x.x, x.y, COUNT(*) FROM intersections x "
        "JOIN accidents a ON a.xid = x.xid AND a.motorway = 0 "
        "GROUP BY x.xid").fetchall()
    src.close()
    print("  發生地點 → 路段 %d ‧ 門牌 %d ｜ 路口 %d"
          % (len(roads), len(buf), len(xrows)))
    return roads, xrows


# ------------------------------------------------------------------ 組裝
def build():
    t0 = time.time()
    poly, pp, fwd, inv, node_tree = load_geo()
    db = open_out()

    print("掃 OSM…")
    o_roads, pois = scan_osm(db, poly, fwd)
    print("讀 事故索引.db…")
    a_roads, xrows = scan_accidents(db, fwd)

    # 掃描只用 bbox 濾，這裡把界外的門牌清掉，後面的參考點與 join 才乾淨
    n0 = db.execute("SELECT COUNT(*) FROM addr_raw").fetchone()[0]
    db.execute("DELETE FROM addr_raw WHERE district IS NULL OR district NOT IN (%s)"
               % ",".join("?" * len(TAOYUAN)), TAOYUAN)
    db.commit()
    n1 = db.execute("SELECT COUNT(*) FROM addr_raw").fetchone()[0]
    print("  門牌 %d → %d（去掉新北、新竹等界外地址）" % (n0, n1))

    # 用門牌點當「這個座標屬於哪一區」的參考。OSM 的道路沒有 addr:district，
    # 但桃園 13 個區裡有 9 個都有中山路——不分區就會被併成同一筆。
    print("建立區判定參考…")
    ref = db.execute("SELECT x, y, district FROM addr_raw WHERE rowid % 5 = 0").fetchall()
    from scipy.spatial import cKDTree
    dtree = cKDTree(np.array([[r[0], r[1]] for r in ref]))
    dname = [r[2] for r in ref]
    print("  參考點 %d 個" % len(ref))

    def districts_of(pts):
        """一次查一批點的區。逐點呼叫 KD-tree 在 Python 迴圈裡太慢。"""
        d, i = dtree.query(np.asarray(pts))
        return [dname[int(k)] if dd < 3000 else None for dd, k in zip(d, i)]

    # ---- 路段：兩邊合併 ----
    print("合併路段…")
    roads = defaultdict(list)
    for (d, r, s), pts in a_roads.items():
        if d in TAOYUAN:
            roads[(d, r, s)].extend(pts)
    for (r, s), pts in o_roads.items():
        for d, xy in zip(districts_of(pts), pts):
            if d:
                roads[(d, r, s)].append(xy)

    places, road_id = [], {}
    for (d, r, s), pts in roads.items():
        arr = np.asarray(pts)
        x, y = float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))
        if not pp.contains(_pt(x, y)):
            continue
        road_id[(d, r, s)] = len(places)
        places.append(dict(kind="road", name=r + ("%s段" % s if s else ""),
                           district=d, detail="道路", x=x, y=y,
                           weight=12 + min(8, len(pts) / 40.0)))
    n_road = len(places)

    # ---- POI ----
    seen, keep = set(), []
    for nm, kind, w, x, y in pois:
        if not pp.contains(_pt(x, y)):
            continue
        key = (N.norm(nm), round(x / 100), round(y / 100))   # 同名同地只留一個
        if key in seen:
            continue
        seen.add(key)
        keep.append((nm, kind, w, x, y))
    for (nm, kind, w, x, y), d in zip(keep, districts_of([(k[3], k[4]) for k in keep])):
        places.append(dict(kind="poi", name=nm, district=d,
                           detail=kind, x=x, y=y, weight=w))
    n_poi = len(places) - n_road

    # ---- 路口 ----
    for (nm, x, y, cnt), d in zip(xrows, districts_of([(r[1], r[2]) for r in xrows])):
        places.append(dict(kind="x", name=nm, district=d,
                           detail="%d 件事故" % cnt, x=x, y=y,
                           weight=8 + min(10, cnt / 6.0)))

    # ---- 服務範圍與吸附距離：搜尋階段就要知道，不要等使用者選了才擋 ----
    print("計算吸附距離…")
    xy = np.array([[p["x"], p["y"]] for p in places])
    snap, _ = node_tree.query(xy)
    lon, lat = inv.transform(xy[:, 0], xy[:, 1])
    for p, s, la, lo in zip(places, snap, lat, lon):
        p["snap"], p["lat"], p["lon"] = float(s), float(la), float(lo)

    write(db, places, road_id, inv)
    print("總耗時 %.0f 秒" % (time.time() - t0))


def _pt(x, y):
    from shapely.geometry import Point
    return Point(x, y)


def write(db, places, road_id, inv):
    print("寫入…")
    db.executescript("""
      CREATE TABLE place(
        id INTEGER PRIMARY KEY, kind TEXT, name TEXT, district TEXT,
        -- txt 是正規化後的正式名稱，用來評分（完全相同／前綴／包含）。
        -- ftxt 是丟進 FTS 的檢索文字，額外含別名（桃園→桃園車站）。
        -- 兩者分開，別名才不會讓「完全相同」的判斷永遠不成立。
        detail TEXT, txt TEXT, ftxt TEXT, lat REAL, lon REAL, x REAL, y REAL,
        weight REAL, snap REAL, n_addr INTEGER DEFAULT 0);
      CREATE TABLE addr(
        road_id INTEGER, no INTEGER, lane INTEGER, alley INTEGER,
        lat REAL, lon REAL);
      CREATE TABLE road_key(k TEXT PRIMARY KEY, id INTEGER);
    """)
    rows = []
    for i, p in enumerate(places):
        txt = N.norm(p["name"])
        al = aliases(p["name"], p["detail"] or "") if p["kind"] == "poi" else []
        ftxt = " ".join([txt] + [N.norm(a) for a in al])
        rows.append((i, p["kind"], p["name"], p["district"], p["detail"], txt, ftxt,
                     p["lat"], p["lon"], p["x"], p["y"], p["weight"], p["snap"]))
    db.executemany(
        "INSERT INTO place(id,kind,name,district,detail,txt,ftxt,lat,lon,x,y,weight,snap)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO road_key VALUES(?,?)",
                   [(N.road_key(d, r, s), i) for (d, r, s), i in road_id.items()])

    # addr_raw → addr：用路段鍵接回 place.id。接不上的門牌（路段落在市界外、
    # 或 OSM 與警政署的路名寫法對不起來）直接丟掉，不留孤兒。
    db.create_function("rkey", 3, N.road_key)
    joined = db.execute("""
      SELECT k.id, CAST(a.no AS INTEGER),
             CAST(COALESCE(a.lane, 0) AS INTEGER),
             CAST(COALESCE(a.alley, 0) AS INTEGER), a.x, a.y
      FROM addr_raw a JOIN road_key k ON k.k = rkey(a.district, a.road, a.seg)
    """).fetchall()
    if joined:
        lon, lat = inv.transform(np.array([r[4] for r in joined]),
                                 np.array([r[5] for r in joined]))
        db.executemany(
            "INSERT INTO addr(road_id, no, lane, alley, lat, lon) VALUES(?,?,?,?,?,?)",
            [(r[0], r[1], r[2], r[3], float(la), float(lo))
             for r, la, lo in zip(joined, lat, lon)])

    db.executescript("""
      DROP TABLE addr_raw;
      -- 索引一定要在 UPDATE 之前建。順序顛倒的話那句相關子查詢會讓一萬多個
      -- 路段各自全表掃 92 萬筆門牌（上百億次列讀取），單這一步就要跑十幾分鐘。
      CREATE INDEX i_addr ON addr(road_id, no);
      UPDATE place SET n_addr =
        (SELECT COUNT(*) FROM addr WHERE addr.road_id = place.id);
      CREATE INDEX i_place_d ON place(district, kind);
      CREATE INDEX i_place_txt ON place(txt);
      CREATE VIRTUAL TABLE place_fts USING fts5(
        ftxt, content='place', content_rowid='id', tokenize='trigram');
      INSERT INTO place_fts(rowid, ftxt) SELECT id, ftxt FROM place;
      VACUUM;
    """)
    db.commit()
    q = lambda s: db.execute(s).fetchone()[0]
    print("\n完成 %s  %.1f MB" % (os.path.relpath(OUT, BASE),
                                  os.path.getsize(OUT) / 1048576))
    print("  可搜尋：路段 %d ‧ POI %d ‧ 路口 %d ＝ %d 筆"
          % (q("SELECT COUNT(*) FROM place WHERE kind='road'"),
             q("SELECT COUNT(*) FROM place WHERE kind='poi'"),
             q("SELECT COUNT(*) FROM place WHERE kind='x'"),
             q("SELECT COUNT(*) FROM place")))
    print("  門牌（非 FTS，用 路段+號 查）：%d 筆" % q("SELECT COUNT(*) FROM addr"))
    db.close()


if __name__ == "__main__":
    build()
