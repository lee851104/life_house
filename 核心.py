# -*- coding: utf-8 -*-
"""共用核心：資料載入 + 風險與分數計算。

由 建立基準.py 與 api.py 共同引用，確保「建基準」與「查分數」用的是
同一套數學——不然百分位會對不上，分數就沒有意義。

風險定義
    risk(P) = Σ 事故嚴重度 × K(d) / Σ 路網長度 × K(d)
    K(d)    = 1 - (d/R)²        Epanechnikov 核，R = 500m
    嚴重度  = 1 + 20 × 死亡人數

    分子分母用同一個核加權，risk 才是真正的「每公里有效路網的事故密度」，
    而不是「圈內事故數 ÷ 圈內路長」這種分子分母尺度不一致的比值。

分數
    score = 「桃園市內有多少比例的同類地區比這裡更危險」的百分位。

    同類 = 曝險（路網密度）相近。分層是必要的，不是修飾：桃園有三分之一是
    山地與農地，那些地方兩年零事故。若把它們和市區放在同一條分布裡比，
    市區任何一點都會落到後段，等第全部變成「較高風險」，分數就沒有鑑別力。
    前端本來就寫「與桃園市都市化程度相近的地區比較」，這裡讓那句話成立。

    另外先做小樣本收縮（往同層中位數拉），避免圈內只有 2 件事故就給出極端分數。
"""
import math
import os
import sqlite3

import numpy as np
import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "configs", "analysis.yaml"), encoding="utf-8") as f:
    ANALYSIS_CONFIG = yaml.safe_load(f)["analysis"]
DB_PATH = os.path.join(BASE, "事故索引.db")
NET_PATH = os.path.join(BASE, "路網.npz")
REF_PATH = os.path.join(BASE, "基準.npz")
BND_PATH = os.path.join(BASE, "市界.npz")

R_WALK = float(ANALYSIS_CONFIG["walk_radius_m"])
R_ROUTE = float(ANALYSIS_CONFIG["route_buffer_m"])
R_DENS = R_WALK
FATAL_W = float(ANALYSIS_CONFIG["fatality_weight"])
MONTHS = 24
STRATA = int(ANALYSIS_CONFIG["exposure_strata"])

# Dijkstra 搜尋上限（公尺）。桃園東西寬約 50 km，山路繞行後最長的市內路線
# 實測 63.5 km（南崁→巴陵），舊值 25 km 會把它判成「找不到可行路線」。
# 放寬幾乎不花錢：25 km→100 km 的單次查詢是 0.045s→0.063s。留一個上限只是
# 當作失控保險。
ROUTE_LIMIT = float(ANALYSIS_CONFIG["route_limit_m"])
ROUTE_ALTERNATIVES = int(ANALYSIS_CONFIG["route_alternatives"])
# 起訖點離最近機車路網節點的上限（公尺）。山區有大片沒有 OSM 道路的區域，
# 不設上限的話使用者在復興區點兩個相距 2 km 的點，會被吸到 2–3 km 外的
# 公路上，回傳一條跟兩支圖釘完全對不上的路線——而且畫在地圖上像真的。
# 400 m 是距離分布的斷層：市界內隨機點 p75=172 m、p90=1082 m。
SNAP_MAX = float(ANALYSIS_CONFIG["snap_max_m"])

# 收縮的「虛擬觀測量」，單位 km。
#   adj = (事故加權 + K0 × 同層中位風險) / (路網km + K0)
# 用路網長度而不是事故件數當證據量：件數當權重時，risk=0 也會被硬拉離 0
# （adj = K0·med/(n+K0) 恆大於 0），於是「真的很安全」的地方永遠拿不到高分，
# 80 分以上的等第形同虛設。
SHRINK_KM_WALK = float(ANALYSIS_CONFIG["walk_shrinkage_km"])
SHRINK_KM_RIDE = float(ANALYSIS_CONFIG["ride_shrinkage_km"])
MIN_KM = float(ANALYSIS_CONFIG["minimum_walk_network_km"])


def sev(fat):
    return 1.0 + FATAL_W * np.asarray(fat, dtype=np.float64)


# ------------------------------------------------------------------ 載入
class Data:
    """把索引與路網一次載入記憶體，之後所有查詢都在 numpy / KD-tree 上做。"""

    def __init__(self, need_ref=True):
        from pyproj import Transformer
        from scipy.spatial import cKDTree

        self.fwd = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
        self.inv = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)

        # ---- 事故索引 ----
        db = sqlite3.connect(DB_PATH)
        self.meta = dict(db.execute("SELECT key, value FROM meta"))
        rows = db.execute(
            "SELECT lat, lon, jlat, jlon, fatalities, injuries, hour, ym, "
            "       motorway, xid, category, road_type, main_cause, parties, ymd, hms "
            "FROM accidents").fetchall()
        xrows = db.execute(
            "SELECT xid, lat, lon, x, y, count, fatalities, injuries, name, cls, motorway "
            "FROM intersections").fetchall()
        db.close()

        lat = np.array([r[0] for r in rows])
        lon = np.array([r[1] for r in rows])
        ax, ay = self.fwd.transform(lon, lat)
        self.a_xy = np.column_stack([ax, ay])
        self.a_jlat = np.array([r[2] for r in rows])
        self.a_jlon = np.array([r[3] for r in rows])
        self.a_fat = np.array([r[4] for r in rows], dtype=np.int32)
        self.a_inj = np.array([r[5] for r in rows], dtype=np.int32)
        self.a_hour = np.array([r[6] for r in rows], dtype=np.int8)
        self.a_mw = np.array([r[8] for r in rows], dtype=bool)
        self.a_xid = np.array([r[9] for r in rows], dtype=np.int32)
        self.a_sev = sev(self.a_fat)
        # 文字欄位查詢頻率低，留 list 就好，不必進 numpy
        self.a_txt = [(r[10], r[11], r[12], r[13], r[14], r[15]) for r in rows]

        # 月份 → 0..23
        ym0 = self.meta["range_from"][:7]
        y0, m0 = int(ym0[:4]), int(ym0[5:7])
        self.ym_labels = []
        for i in range(MONTHS):
            y, m = y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1
            self.ym_labels.append("%04d-%02d" % (y, m))
        lut = {s: i for i, s in enumerate(self.ym_labels)}
        self.a_mi = np.array([lut.get(r[7], -1) for r in rows], dtype=np.int8)

        # xid 就是 intersections 的 rowid（INTEGER PRIMARY KEY），上面那句
        # 全表掃描保證回傳順序 == xid。這裡明確存下來，之後才有辦法驗；
        # 只靠「列序剛好等於 xid」的隱性假設，哪天加個 ORDER BY 或讓查詢走到
        # i_x_mw 索引（實測順序就變了），所有 popup 的事故點會靜靜地張冠李戴。
        self.x_id = np.array([r[0] for r in xrows], dtype=np.int64)
        if not np.array_equal(self.x_id, np.arange(len(xrows))):
            raise RuntimeError("intersections 的回傳順序與 xid 不一致，索引會錯位")

        self.x_xy = np.array([[r[3], r[4]] for r in xrows])
        self.x_fat = np.array([r[6] for r in xrows], dtype=np.int32)
        self.x_mw = np.array([r[10] for r in xrows], dtype=bool)
        self.x_info = [(r[1], r[2], r[8], r[9]) for r in xrows]   # lat, lon, name, cls
        self.x_cls = np.array([r[9] for r in xrows], dtype=object)

        # 地面事故（行人與機車都到得了的）才進風險計算
        self.ground = ~self.a_mw

        # 路口的顯示件數一律由「地面事故」現算，不用 DB 的 count 欄位。
        #
        # 建立索引.py 給路口的 motorway 旗標只看群集的眾數道路類別，給事故的
        # 卻是逐筆判斷、還多認高架與隧道。兩套判準不一致，於是：
        #   ‧ 99 個地面路口的 count 含了國道／高架事故，popup 標題寫「展開
        #     10 個事故點」卻只列得出 8 個；
        #   ‧ 43 個路口因為眾數被判成國道而整個從清單消失，裡面卻有 87 筆
        #     實實在在的地面事故（那些事故本來就有進風險分數，只是路口看不到）。
        # 以「這個路口有幾件地面事故」當唯一定義，件數、事故點清單、風險分子
        # 三者就永遠對得上，也不必為此重建 事故索引.db。
        self.x_cnt = np.bincount(self.a_xid[self.ground],
                                 minlength=len(xrows)).astype(np.int32)

        # 只把有地面事故的路口拿來顯示與排名。純國道匝道群集的地面件數是 0，
        # 會自動被這個條件擋掉，不需要再靠那個不可靠的眾數旗標。
        self.x_keep = np.flatnonzero(self.x_cnt > 0)

        # 「前 N%」的比較基準。前端文案寫「與相同道路等級路口比較」，所以按
        # 道路類別分開排序。
        self.x_cnt_sorted = np.sort(self.x_cnt[self.x_keep])
        self.x_cnt_by_cls = {}
        keep_cls = self.x_cls[self.x_keep]
        for c in set(keep_cls):
            self.x_cnt_by_cls[c] = np.sort(self.x_cnt[self.x_keep[keep_cls == c]])

        self.g_ix = np.flatnonzero(self.ground)      # → 回查 a_txt 用
        self.g_xy = self.a_xy[self.ground]
        self.g_sev = self.a_sev[self.ground]
        self.g_fat = self.a_fat[self.ground]
        self.g_inj = self.a_inj[self.ground]
        self.g_hour = self.a_hour[self.ground]
        self.g_mi = self.a_mi[self.ground]
        self.g_xid = self.a_xid[self.ground]
        self.g_jlat = self.a_jlat[self.ground]
        self.g_jlon = self.a_jlon[self.ground]
        self.acc_tree = cKDTree(self.g_xy)

        self.x_tree = cKDTree(self.x_xy[self.x_keep])

        # ---- 路網 ----
        net = np.load(NET_PATH)
        self.w_mid = net["walk_mid"].astype(np.float64)
        self.w_len = net["walk_len"].astype(np.float64)
        self.walk_tree = cKDTree(self.w_mid)

        self.n_xy = net["nodes"].astype(np.float64)
        self.r_u = net["ride_u"]
        self.r_v = net["ride_v"]
        self.r_w = net["ride_w"].astype(np.float64)
        self.node_tree = cKDTree(self.n_xy)

        from scipy.sparse import csr_matrix
        n = len(self.n_xy)
        self.graph = csr_matrix((self.r_w, (self.r_u, self.r_v)), shape=(n, n))

        # 機車有向邊 → 無向去重，用來量路網密度（同一段路不能算兩次）
        uv = np.sort(np.column_stack([self.r_u, self.r_v]), axis=1)
        _, uq = np.unique(uv, axis=0, return_index=True)
        self.e_u = self.r_u[uq]
        self.e_v = self.r_v[uq]
        self.e_mid = (self.n_xy[self.e_u] + self.n_xy[self.e_v]) / 2.0
        self.e_len = self.r_w[uq]
        self.edge_tree = cKDTree(self.e_mid)

        # 無向鄰接表（CSR 形式），供基準取樣時做隨機路徑遊走
        n2 = len(self.n_xy)
        src = np.concatenate([self.e_u, self.e_v])
        dst = np.concatenate([self.e_v, self.e_u])
        wgt = np.concatenate([self.e_len, self.e_len])
        order = np.argsort(src, kind="stable")
        self.adj_dst = dst[order]
        self.adj_w = wgt[order]
        self.adj_ptr = np.zeros(n2 + 1, dtype=np.int64)
        np.cumsum(np.bincount(src, minlength=n2), out=self.adj_ptr[1:])

        # ---- 市界（基準取樣範圍 + 服務範圍檢核）----
        self.ring = None
        self.poly = None
        if os.path.exists(BND_PATH):
            self.ring = np.load(BND_PATH)["ring"]
            from shapely.geometry import Polygon
            from shapely.prepared import prep
            self.poly = prep(Polygon(self.ring))

        # ---- 百分位基準（分層）----
        self.ref = None
        if need_ref and os.path.exists(REF_PATH):
            r = np.load(REF_PATH)
            self.ref = {}
            for kind in ("walk", "ride"):
                off = r["%s_off" % kind]
                vals = r["%s_vals" % kind]
                self.ref[kind] = {
                    "edges": r["%s_edges" % kind],
                    "bands": [vals[off[i]:off[i + 1]] for i in range(len(off) - 1)],
                    "med": r["%s_med" % kind],
                }

    # -------------------------------------------------------------- 幾何
    def to_xy(self, lat, lon):
        return self.fwd.transform(lon, lat)

    def to_ll(self, x, y):
        lon, lat = self.inv.transform(x, y)
        return lat, lon

    def inside(self, lat, lon):
        """是否在桃園市界內。

        不能只用經緯度矩形：前端 CFG.bbox 那個框把林口、鶯歌、樹林等新北轄區
        也包進來了。那些地方有 OSM 道路但沒有桃園的事故資料，算出來會是
        「零事故 → 非常安全」，等於拿沒有資料當成安全。
        """
        if self.poly is None:
            return True
        from shapely.geometry import Point
        return self.poly.contains(Point(*self.to_xy(lat, lon)))

    # -------------------------------------------------------------- 風險
    def exposure(self, x, y, r=R_WALK):
        """核加權有效路網長度（km）。"""
        idx = self.walk_tree.query_ball_point([x, y], r)
        if not idx:
            return 0.0
        idx = np.asarray(idx)
        d = np.hypot(self.w_mid[idx, 0] - x, self.w_mid[idx, 1] - y)
        k = 1.0 - (d / r) ** 2
        return float((self.w_len[idx] * k).sum() / 1000.0)

    def walk_risk(self, x, y, r=R_WALK):
        """回傳 (risk, 圈內事故數, 核加權嚴重度, 有效路網km, 事故索引)。"""
        idx = self.acc_tree.query_ball_point([x, y], r)
        idx = np.asarray(idx, dtype=np.int64)
        if idx.size:
            d = np.hypot(self.g_xy[idx, 0] - x, self.g_xy[idx, 1] - y)
            k = 1.0 - (d / r) ** 2
            wsum = float((self.g_sev[idx] * k).sum())
        else:
            wsum = 0.0
        km = self.exposure(x, y, r)
        risk = wsum / max(km, 0.15)
        return risk, int(idx.size), wsum, km, idx

    def ride_km(self, x, y, r=R_DENS):
        """半徑內的機車路網長度（km），用來判斷路線經過的是市區還是鄉道。"""
        idx = self.edge_tree.query_ball_point([x, y], r)
        return float(self.e_len[np.asarray(idx, dtype=np.int64)].sum() / 1000.0) \
            if idx else 0.0

    # -------------------------------------------------------------- 路徑
    def snap(self, p, limit=SNAP_MAX):
        """把一個座標吸到最近的機車路網節點。

        回傳 (節點編號, 距離公尺)；超過 limit 時節點編號給 -1，讓呼叫端能分辨
        「這裡根本沒有路」和「有路但走不到」——這兩件事對使用者是完全不同的
        訊息，全都回「找不到可行路線」等於把系統限制講成使用者選錯地方。
        """
        d, i = self.node_tree.query([p])
        d, i = float(d[0]), int(i[0])
        return (i if limit is None or d <= limit else -1), d

    def shortest_path(self, a, b, limit=ROUTE_LIMIT, penalties=None):
        """機車路網最短路徑。回傳 (節點序列, 長度公尺)，走不通回傳 None。

        基準與查詢一定要共用這支：基準若改用隨機遊走取樣，走出來的是巷弄，
        而 Dijkstra 走的是主幹道——事故幾乎都在主幹道上，兩者每公里事故率
        差一個數量級，所有真實路線都會掉到 1 分。
        """
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra

        s = int(self.node_tree.query([a])[1][0])
        t = int(self.node_tree.query([b])[1][0])
        if s == t:
            return None
        # 候選路線會暫時提高已選道路的成本，以找出真正不同的替代走法；回傳的
        # 長度仍以原始邊長計算，不能把「避開成本」誤顯示成實際公里數。
        graph = self.graph
        if penalties:
            w = self.r_w.copy()
            mask = np.isin(self.r_u.astype(np.int64) * len(self.n_xy) + self.r_v,
                           np.fromiter(penalties, dtype=np.int64))
            w[mask] *= 3.0
            graph = csr_matrix((w, (self.r_u, self.r_v)), shape=self.graph.shape)

        dist, pred = dijkstra(graph, directed=True, indices=s,
                             return_predecessors=True, limit=limit)
        if not np.isfinite(dist[t]):
            return None
        seq, cur = [t], t
        while cur != s:
            cur = int(pred[cur])
            if cur < 0:
                return None
            seq.append(cur)
        seq.reverse()
        # scipy 的 sparse advanced indexing 會回傳 1×n matrix；轉成一維後加總。
        actual = float(np.asarray(self.graph[seq[:-1], seq[1:]]).ravel().sum())
        return seq, actual

    def alternative_paths(self, a, b, count=ROUTE_ALTERNATIVES):
        """回傳最短路徑及至多兩條明顯不同、合理繞行的候選路線。

        這不是導航引擎的 k-shortest paths 完整實作：本產品目的是比較安全歷史，
        所以以已選路段的成本懲罰來產生可讀的替代走法，並剔除幾乎重疊或過度繞行
        的候選，避免把同一條路換個巷口就當成「替代道路」。
        """
        first = self.shortest_path(a, b)
        if first is None:
            return []
        paths = [first]
        n = len(self.n_xy)
        base_len = first[1]

        for _ in range(max(0, count - 1)):
            used = set()
            for seq, _length in paths:
                used.update(int(u) * n + int(v) for u, v in zip(seq[:-1], seq[1:]))
            candidate = self.shortest_path(a, b, penalties=used)
            if candidate is None or candidate[1] > base_len * 1.65:
                break
            cedges = set(int(u) * n + int(v) for u, v in zip(candidate[0][:-1], candidate[0][1:]))
            # 與任一既有候選有超過 72% 的路段相同，對使用者沒有比較價值。
            if any(len(cedges & set(int(u) * n + int(v) for u, v in zip(seq[:-1], seq[1:]))) /
                   max(1, len(cedges)) > .72 for seq, _length in paths):
                break
            paths.append(candidate)
        return paths

    def band_of(self, km, kind="walk"):
        """曝險落在哪一層。"""
        if self.ref is None:
            return 0
        e = self.ref[kind]["edges"]
        return int(min(len(e) - 2, max(0, np.searchsorted(e, km, side="right") - 1)))

    def shrink(self, wsum, km, kind="walk", band_km=None):
        """把「事故加權 ÷ 路網km」往同層中位數收縮。

        km        證據量。路網短（郊區小圈、短路線）時往中位數拉，長時信自己算的值。
                  wsum=0 且 km 夠大時 adj 趨近 0，「很安全」才反映得出高分。
        band_km   決定要拿哪一層的中位數。walk 兩者同值；route 的證據量是路線
                  長度，但分層鍵是沿線路網密度，不分開會抓錯層的中位數。
        """
        if self.ref is None:
            return wsum / max(km, 0.15)
        k0 = SHRINK_KM_WALK if kind == "walk" else SHRINK_KM_RIDE
        med = float(self.ref[kind]["med"][self.band_of(
            km if band_km is None else band_km, kind)])
        return (wsum + k0 * med) / (km + k0)

    def percentile(self, risk, km, kind="walk"):
        """同層中有多少比例的地方比這裡更危險 → 就是安全百分位。"""
        if self.ref is None:
            return 50
        arr = self.ref[kind]["bands"][self.band_of(km, kind)]
        if len(arr) == 0:
            return 50
        # 同分視為同樣安全：取 left/right 的中點，避免大量 0 風險全部擠到 99
        lo = float(np.searchsorted(arr, risk, side="left"))
        hi = float(np.searchsorted(arr, risk, side="right"))
        safer_than = (lo + hi) / 2.0
        pct = 100.0 * (1.0 - safer_than / len(arr))
        return int(max(1, min(99, round(pct))))

    def count_rank(self, count, cls=None):
        """事故件數在「同道路等級」路口中的位置。

        件數分布極度右偏（中位數 2 件），整數百分比會讓 30～40 件的路口全部
        顯示「前 1%」，同一份清單五列一模一樣，等於沒有資訊。低於 10% 時
        補一位小數才分得出高下。
        """
        arr = self.x_cnt_by_cls.get(cls) if cls else None
        # 樣本太少就退回全市。省道只有 97 個路口、專用道路 17 個，拿它們自己
        # 當分母做百分位沒有意義。但這時候比較基準已經不是「同級」了，要一起
        # 回報給前端改標籤——否則畫面會宣稱一個它其實沒做的比較。
        basis = "同級路口"
        if arr is None or len(arr) < 200:
            arr = self.x_cnt_sorted
            basis = "全市路口"
        above = len(arr) - int(np.searchsorted(arr, count, side="left"))
        pct = 100.0 * above / len(arr)
        if pct < 10:
            text = "前 %.1f%%" % max(pct, 0.1)
        else:
            text = "前 %d%%" % int(round(pct))
        if pct <= 35:
            return {"text": text, "tone": "bad", "basis": basis}
        if pct <= 62:
            return {"text": "約中段", "tone": "mid", "basis": basis}
        return {"text": "偏少", "tone": "ok", "basis": basis}


# ------------------------------------------------------------------ 取樣
def sample_line(xy, step=20.0):
    """沿折線每 step 公尺取一個點。"""
    pts = []
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        d = float(math.hypot(x2 - x1, y2 - y1))
        k = max(1, int(d // step))
        for j in range(k):
            t = (j + 0.5) / k
            pts.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    return np.asarray(pts) if pts else np.asarray([xy[0]])


def grid_points(bbox_ll, step, fwd):
    """在 bbox 內以 step 公尺產生取樣點（EPSG:3826）。"""
    s, w, n, e = bbox_ll
    x0, y0 = fwd.transform(w, s)
    x1, y1 = fwd.transform(e, n)
    xs = np.arange(math.floor(x0 / step) * step, x1 + step, step)
    ys = np.arange(math.floor(y0 / step) * step, y1 + step, step)
    gx, gy = np.meshgrid(xs, ys)
    return np.column_stack([gx.ravel(), gy.ravel()])
