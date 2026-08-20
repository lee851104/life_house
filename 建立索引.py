# -*- coding: utf-8 -*-
"""建立事故索引：桃園市 A1/A2 CSV → 事故索引.db

產出（SQLite）
  accidents      逐件事故。座標同時存真實值與 ±25m 決定性偏移值
  intersections  事故聚合成的「路口」（25m 網格 → 45m 貪婪聚合）
  acc_rt / x_rt  R-tree 空間索引，讓半徑查詢不用全表掃描
  meta           期間、筆數、資料來源

用法
    python 建立索引.py
"""
import csv
import hashlib
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = [os.path.join(BASE, "縣市", "桃園市_%s_2024-2026.csv" % k) for k in ("A1", "A2")]
DB = os.path.join(BASE, "事故索引.db")

# 期間必須與 index.html 的 CFG.range 一致
FROM, TO = "20240701", "20260630"

# 欄位索引（51 欄固定格式）
C_YMD, C_HMS, C_CAT, C_LOC, C_CLS = 2, 3, 4, 6, 9
C_RTYPE, C_CAUSE, C_DU, C_SEQ, C_VEH = 12, 31, 32, 33, 35
C_LON, C_LAT = 49, 50

GRID = 25.0        # 網格邊長（公尺）
MERGE = 45.0       # 聚合半徑（公尺）——約一個路口的尺度
JITTER = 25.0      # 個別事故點的隱私偏移上限（公尺）

# 行人與普通機車都到不了的路段。不排除的話，桃園事故最多的兩個「路口」
# 會是國道南向的匝道，走路安全分數就被高速公路的事故拉低，講不通。
MOTORWAY_CLS = {"國道", "快速(公)道"}
MOTORWAY_TYPE = {"高架道路", "隧道"}


def is_motorway(m):
    """單筆事故是不是發生在行人／機車到不了的路段。

    事故與路口一律共用這一支，兩邊的判準才不會分岔。
    """
    return m["cls"] in MOTORWAY_CLS or m["rtype"] in MOTORWAY_TYPE


csv.field_size_limit(10 ** 7)
YEAR = re.compile(r"^\d{4}$")


# ---------------------------------------------------------------- 投影
def make_proj():
    """TWD97 / TM2 zone 121（EPSG:3826）——台灣官方投影，單位公尺。"""
    from pyproj import Transformer
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
    inv = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    return (lambda lon, lat: fwd.transform(lon, lat)), (lambda x, y: inv.transform(x, y))


# ---------------------------------------------------------------- 讀 CSV
def parse_casualty(s):
    """'死亡1;受傷0' → (1, 0)"""
    try:
        a, b = s.split(";")
        return int(re.sub(r"\D", "", a) or 0), int(re.sub(r"\D", "", b) or 0)
    except Exception:
        return 0, 0


def read_accidents():
    """把逐「當事者」的列 group 成逐「事故」。

    原始資料沒有案件編號，用（日期,時間,經度,緯度）當複合鍵。
    """
    acc = {}
    rows = 0
    for path in SRC:
        if not os.path.exists(path):
            sys.exit("找不到 %s\n請先執行： python 篩選縣市.py 桃園市" % path)
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.reader(f)
            next(rd)
            for r in rd:
                if not r or not YEAR.match(r[0]) or len(r) < 51:
                    continue
                if not (FROM <= r[C_YMD] <= TO):
                    continue
                try:
                    lon, lat = float(r[C_LON]), float(r[C_LAT])
                except ValueError:
                    continue
                if not (120.9 < lon < 121.6 and 24.5 < lat < 25.2):
                    continue
                rows += 1
                key = (r[C_YMD], r[C_HMS], r[C_LON], r[C_LAT])
                a = acc.get(key)
                if a is None:
                    dead, hurt = parse_casualty(r[C_DU])
                    a = acc[key] = {
                        "ymd": r[C_YMD], "hms": r[C_HMS], "cat": r[C_CAT],
                        "lat": lat, "lon": lon, "loc": r[C_LOC],
                        "cls": r[C_CLS], "rtype": r[C_RTYPE], "cause": r[C_CAUSE],
                        "dead": dead, "hurt": hurt, "veh": [],
                    }
                if r[C_SEQ] == "1":       # 第1當事者的列才帶事故層級屬性
                    dead, hurt = parse_casualty(r[C_DU])
                    a.update(cat=r[C_CAT], loc=r[C_LOC], cls=r[C_CLS],
                             rtype=r[C_RTYPE], cause=r[C_CAUSE],
                             dead=dead, hurt=hurt)
                v = r[C_VEH].strip()
                if v and v not in a["veh"]:
                    a["veh"].append(v)
    print("  讀入 {:,} 列當事者 → {:,} 件事故".format(rows, len(acc)))
    return list(acc.values())


# ---------------------------------------------------------------- 隱私偏移
def jitter(a):
    """決定性偏移：同一件事故每次都得到同一個偏移，但無法反推真實位置。

    前端 popup 寫明「座標已做 ±25m 偏移以保護當事人隱私」，這裡讓那句話成立。
    """
    h = hashlib.md5(("%s%s%s%s" % (a["ymd"], a["hms"], a["lon"], a["lat"])).encode()).digest()
    ang = int.from_bytes(h[:4], "big") / 2 ** 32 * 2 * math.pi
    rad = math.sqrt(int.from_bytes(h[4:8], "big") / 2 ** 32) * JITTER
    return (a["lat"] + rad * math.sin(ang) / 110540.0,
            a["lon"] + rad * math.cos(ang) / (111320.0 * math.cos(math.radians(a["lat"]))))


# ---------------------------------------------------------------- 路口命名
DIST = re.compile(r"^桃園市[^市區鄉鎮]{1,3}[區鄉鎮市]")     # 去掉「桃園市中壢區」
HOUSE = re.compile(r"(\d+(?:-\d+)?號|\d+巷|\d+弄|"
                   r"\d+公里\d*(?:\.\d+)?公尺處?|前\d+(?:\.\d+)?公尺)")
TAIL = re.compile(r"(附近|口|北側|南側|東側|西側|上|下|前|旁|處)+$")


def clean_road(s):
    s = DIST.sub("", s.strip())
    s = HOUSE.sub("", s)
    s = TAIL.sub("", s.strip())
    return s.strip(" 　-")


def name_cluster(locs):
    """優先用「A路 / B路」的交叉路口寫法；沒有就退回單一路名。"""
    pairs, singles = Counter(), Counter()
    for s in locs:
        if " / " in s:
            a, b = (clean_road(p) for p in s.split(" / ", 1))
            if a and b and a != b:
                pairs[tuple(sorted((a, b)))] += 1
                singles[a] += 1
                singles[b] += 1
                continue
        r = clean_road(s)
        if r:
            singles[r] += 1
    if pairs:
        a, b = pairs.most_common(1)[0][0]
        return "%s / %s" % (a, b)
    if singles:
        return singles.most_common(1)[0][0] + "一帶"
    return "未命名路口"


# ---------------------------------------------------------------- 聚合
def cluster(accs, fwd):
    """25m 網格 → 由多到少貪婪吸收 45m 內的事故。

    不用 union-find 連鎖合併：市區幹道上格子彼此相鄰，連鎖會把整條街併成
    一個巨大群集。貪婪法以熱點為種子，群集尺度自然停在路口大小。
    """
    for a in accs:
        a["x"], a["y"] = fwd(a["lon"], a["lat"])

    cells = defaultdict(list)
    for i, a in enumerate(accs):
        cells[(int(a["x"] // GRID), int(a["y"] // GRID))].append(i)

    centroid = {}
    for c, idx in cells.items():
        centroid[c] = (sum(accs[i]["x"] for i in idx) / len(idx),
                       sum(accs[i]["y"] for i in idx) / len(idx))

    order = sorted(cells, key=lambda c: -len(cells[c]))
    span = int(MERGE // GRID) + 1
    used = set()
    clusters = []
    for c in order:
        if c in used:
            continue
        sx, sy = centroid[c]
        members, cover = [], []
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                n = (c[0] + dx, c[1] + dy)
                if n in used or n not in cells:
                    continue
                nx, ny = centroid[n]
                if math.hypot(nx - sx, ny - sy) <= MERGE:
                    cover.append(n)
                    members.extend(cells[n])
        used.update(cover)
        clusters.append(members)
    print("  {:,} 個 {:.0f}m 網格 → {:,} 個路口群集".format(len(cells), GRID, len(clusters)))
    return clusters


# ---------------------------------------------------------------- 主流程
def main():
    t0 = time.time()
    fwd, inv = make_proj()

    print("讀取事故資料…")
    accs = read_accidents()

    print("聚合路口…")
    clusters = cluster(accs, fwd)

    if os.path.exists(DB):
        os.remove(DB)
    db = sqlite3.connect(DB)
    db.executescript("""
      PRAGMA journal_mode=OFF;
      CREATE TABLE accidents(
        id INTEGER PRIMARY KEY, xid INTEGER, ymd TEXT, hms TEXT, hour INTEGER,
        ym TEXT, category TEXT, lat REAL, lon REAL, jlat REAL, jlon REAL,
        fatalities INTEGER, injuries INTEGER, road_type TEXT, main_cause TEXT,
        parties TEXT, loc TEXT, cls TEXT, motorway INTEGER);
      CREATE TABLE intersections(
        xid INTEGER PRIMARY KEY, lat REAL, lon REAL, x REAL, y REAL,
        count INTEGER, fatalities INTEGER, injuries INTEGER, name TEXT, cls TEXT,
        motorway INTEGER);
      CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
      CREATE VIRTUAL TABLE acc_rt USING rtree(id, minx, maxx, miny, maxy);
      CREATE VIRTUAL TABLE x_rt   USING rtree(xid, minx, maxx, miny, maxy);
    """)

    arows, xrows, art, xrt = [], [], [], []
    for xid, members in enumerate(clusters):
        ms = [accs[i] for i in members]
        cx = sum(m["x"] for m in ms) / len(ms)
        cy = sum(m["y"] for m in ms) / len(ms)
        clon, clat = inv(cx, cy)
        cls_mode = Counter(m["cls"] for m in ms).most_common(1)[0][0]
        # 路口的旗標必須跟事故的旗標用同一個判準，而且是「全部成員都是國道／
        # 高架」才算國道路口。舊版只看眾數道路類別、也不認 MOTORWAY_TYPE，
        # 結果兩套判準對 142 個路口的結論不一樣：99 個地面路口的 count 混進
        # 國道事故（popup 標題與事故點清單對不上），另外 43 個明明有地面事故
        # 的路口被眾數判成國道而整個消失。
        xmw = 1 if all(is_motorway(m) for m in ms) else 0
        xrows.append((xid, clat, clon, cx, cy, len(ms),
                      sum(m["dead"] for m in ms), sum(m["hurt"] for m in ms),
                      name_cluster([m["loc"] for m in ms]), cls_mode, xmw))
        xrt.append((xid, cx, cx, cy, cy))
        for m in ms:
            jlat, jlon = jitter(m)
            aid = len(arows)
            mw = 1 if is_motorway(m) else 0
            arows.append((aid, xid, m["ymd"], m["hms"], int(m["hms"][:2] or 0),
                          "%s-%s" % (m["ymd"][:4], m["ymd"][4:6]), m["cat"],
                          m["lat"], m["lon"], jlat, jlon, m["dead"], m["hurt"],
                          m["rtype"], m["cause"], "、".join(m["veh"][:4]), m["loc"],
                          m["cls"], mw))
            art.append((aid, m["x"], m["x"], m["y"], m["y"]))

    db.executemany("INSERT INTO accidents VALUES(" + ",".join("?" * 19) + ")", arows)
    db.executemany("INSERT INTO intersections VALUES(" + ",".join("?" * 11) + ")", xrows)
    db.executemany("INSERT INTO acc_rt VALUES(?,?,?,?,?)", art)
    db.executemany("INSERT INTO x_rt VALUES(?,?,?,?,?)", xrt)
    db.executescript("""
      CREATE INDEX i_acc_x  ON accidents(xid);
      CREATE INDEX i_acc_ym ON accidents(ym);
      CREATE INDEX i_acc_mw ON accidents(motorway);
      CREATE INDEX i_x_mw   ON intersections(motorway);
    """)
    db.executemany("INSERT INTO meta VALUES(?,?)", [
        ("range_from", "2024-07-01"), ("range_to", "2026-06-30"),
        ("accidents", str(len(arows))), ("intersections", str(len(xrows))),
        ("fatalities", str(sum(r[11] for r in arows))),
        ("injuries", str(sum(r[12] for r in arows))),
        ("grid_m", str(GRID)), ("merge_m", str(MERGE)), ("jitter_m", str(JITTER)),
        ("source", "內政部警政署 A1/A2 交通事故資料（data.gov.tw）"),
        ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
    ])
    db.commit()
    db.close()

    print("\n完成 {}  {:.0f} MB  ({:.0f}s)".format(
        os.path.relpath(DB, BASE), os.path.getsize(DB) / 1048576, time.time() - t0))
    print("  事故 {:,} 件 ‧ 路口 {:,} 處 ‧ 死亡 {:,} ‧ 受傷 {:,}".format(
        len(arows), len(xrows),
        sum(r[11] for r in arows), sum(r[12] for r in arows)))


if __name__ == "__main__":
    main()
