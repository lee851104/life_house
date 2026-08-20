# -*- coding: utf-8 -*-
"""建立百分位基準：事故索引.db + 路網.npz + 市界.npz → 基準.npz

分數是「比桃園同類地區 X% 的地方安全」，所以需要一份「桃園各地風險」的分布
才有東西可比。這支腳本就是在造那份分布。

三件事一定要做對，否則分數沒有鑑別力：

  1. 只在市界內取樣
     用經緯度 bbox 矩形會有約四分之三的點落在新北、新竹或海上——那裡有 OSM
     道路卻沒有桃園的事故資料，變成假的零風險，把整條分布往下拉。

  2. 依曝險分層
     桃園約三分之一是山地與農地，兩年零事故。和市區放在同一條分布裡比，
     市區任何一點都會落到後段，五個等第只剩兩個用得到。

  3. ride 基準要取「真實最短路徑」
     試過兩種錯的做法：
       ‧ 取 500m 圓內整團路網 —— 那些 40m 緩衝彼此大量重疊，事故去重後除以
         總路長，每公里事故率被嚴重低估。
       ‧ 隨機遊走 —— 會一直鑽進巷弄，但查詢端的 Dijkstra 走的是主幹道，
         而事故幾乎都集中在主幹道上，兩者差一個數量級。
     兩種都會讓市區路線一律掉到 1 分。所以基準改用與查詢端同一支
     core.shortest_path 取樣，取樣方式完全對稱。

用法
    python 建立基準.py
"""
import os
import time

import numpy as np

import 核心 as core

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "基準.npz")

GRID = 250.0          # walk 基準網格（公尺）
RIDE_SAMPLES = 2400   # ride 基準取樣路徑數（每條要跑一次 Dijkstra）
PAIR_KM = (1.5, 12.0)  # 起訖點直線距離範圍，貼近使用者在地圖上點兩點的情境
STEP = 20.0           # 沿線取樣間距（公尺）
SEED = 20260820       # 固定種子，重跑結果一致


def mask_inside(ring, pts):
    from shapely.geometry import Polygon
    from shapely import points as mk_points, contains
    return contains(Polygon(ring), mk_points(pts))


def stratify(key, wsum, km, kind, n_band=core.STRATA):
    """依 key 切成 n_band 層。

    key    分層鍵（walk：500m 圈內可步行路網 km；ride：沿線平均路網密度 km）
    wsum   事故嚴重度加權總和
    km     算率與收縮用的分母（walk：曝險 km；ride：路徑長度 km）

    分兩趟：先用原始率算各層中位數，再用同一條收縮公式把每個樣本也收縮過才
    存起來。查詢端比的是收縮後的值，基準若存未收縮的值就是拿蘋果比橘子。
    """
    k0 = core.SHRINK_KM_WALK if kind == "walk" else core.SHRINK_KM_RIDE
    edges = np.percentile(key, np.linspace(0, 100, n_band + 1))
    edges[0], edges[-1] = -np.inf, np.inf

    raw = wsum / np.maximum(km, 0.15)
    med = []
    for i in range(n_band):
        m = (key >= edges[i]) & (key < edges[i + 1])
        med.append(float(np.median(raw[m])) if m.any() else 0.0)

    bands = []
    for i in range(n_band):
        m = (key >= edges[i]) & (key < edges[i + 1])
        adj = np.sort((wsum[m] + k0 * med[i]) / (km[m] + k0))
        bands.append(adj)
        print("    層 %d  分層鍵 %5.1f–%5.1f km  n=%5d  原始率中位 %7.2f  "
              "收縮後中位 %7.2f  90分位 %7.2f"
              % (i + 1, max(edges[i], 0), min(edges[i + 1], 999),
                 adj.size, med[i],
                 np.median(adj) if adj.size else 0.0,
                 np.percentile(adj, 90) if adj.size else 0.0))
    return edges, bands, med


# ------------------------------------------------------------------ walk
def build_walk(D):
    pts = core.grid_points(np.load(core.NET_PATH)["bbox"], GRID, D.fwd)
    pts = pts[mask_inside(D.ring, pts)]
    print("  市界內 %d 格（%.0f km²）" % (len(pts), len(pts) * GRID ** 2 / 1e6))

    t0 = time.time()
    exp, wsum_l = [], []
    for i, (x, y) in enumerate(pts):
        km = D.exposure(x, y)
        if km < core.MIN_KM:
            continue
        idx = D.acc_tree.query_ball_point([x, y], core.R_WALK)
        if idx:
            idx = np.asarray(idx, dtype=np.int64)
            d = np.hypot(D.g_xy[idx, 0] - x, D.g_xy[idx, 1] - y)
            w = float((D.g_sev[idx] * (1.0 - (d / core.R_WALK) ** 2)).sum())
        else:
            w = 0.0
        exp.append(km)
        wsum_l.append(w)
        if i % 5000 == 0 and i:
            print("    %d/%d  (%.0fs)" % (i, len(pts), time.time() - t0))

    exp = np.asarray(exp)
    wsum = np.asarray(wsum_l)
    print("  有效 %d 格（曝險 >= %.1f km），零事故 %.0f%%  (%.0fs)"
          % (len(exp), core.MIN_KM, (wsum == 0).mean() * 100, time.time() - t0))
    return stratify(exp, wsum, exp, "walk")


# ------------------------------------------------------------------ ride
def build_ride(D):
    """取樣真實最短路徑，與查詢端用同一支 core.shortest_path。

    起訖點取市界內、直線距離 PAIR_KM 範圍內的隨機節點對，貼近使用者實際
    在地圖上點兩個點的情境。
    """
    rng = np.random.default_rng(SEED)
    keep = np.flatnonzero(mask_inside(D.ring, D.n_xy))
    xy = D.n_xy[keep]
    print("  市界內節點 %d 個，目標 %d 條最短路徑（直線 %.1f–%.1f km）…"
          % (len(keep), RIDE_SAMPLES, PAIR_KM[0], PAIR_KM[1]))

    t0 = time.time()
    dens_l, wsum_l, km_l = [], [], []
    tries = 0
    while len(km_l) < RIDE_SAMPLES and tries < RIDE_SAMPLES * 6:
        tries += 1
        i, j = rng.integers(0, len(keep), 2)
        d = float(np.hypot(*(xy[i] - xy[j])))
        if not (PAIR_KM[0] * 1000 <= d <= PAIR_KM[1] * 1000):
            continue
        r = D.shortest_path(xy[i], xy[j])
        if r is None:
            continue
        seq, length_m = r
        if length_m < 800:
            continue
        pts = core.sample_line(D.n_xy[seq], STEP)
        hits = D.acc_tree.query_ball_point(pts, core.R_ROUTE, workers=-1)
        flat = [np.asarray(h, dtype=np.int64) for h in hits if len(h)]
        idx = np.unique(np.concatenate(flat)) if flat else np.empty(0, dtype=np.int64)
        # 分層鍵的算法與 api.analyze_route 完全一致
        probe = pts[::10] if len(pts) > 10 else pts
        dens_l.append(float(np.mean([D.ride_km(px, py) for px, py in probe])))
        wsum_l.append(float(D.g_sev[idx].sum()))
        km_l.append(length_m / 1000.0)
        if len(km_l) % 250 == 0:
            print("    %d/%d  (%.0fs)" % (len(km_l), RIDE_SAMPLES, time.time() - t0))

    dens = np.asarray(dens_l)
    wsum = np.asarray(wsum_l)
    km = np.asarray(km_l)
    print("  有效 %d 條路徑（嘗試 %d 次），平均 %.1f km，零事故 %.0f%%  (%.0fs)"
          % (len(km), tries, km.mean(), (wsum == 0).mean() * 100, time.time() - t0))
    return stratify(dens, wsum, km, "ride")


def flatten(bands):
    off = np.zeros(len(bands) + 1, dtype=np.int64)
    for i, b in enumerate(bands):
        off[i + 1] = off[i] + b.size
    return (np.concatenate(bands) if bands else np.empty(0)), off


if __name__ == "__main__":
    print("載入索引與路網…")
    D = core.Data(need_ref=False)
    if D.ring is None:
        raise SystemExit("找不到 市界.npz，請先執行： python 建立市界.py")
    print("  事故 %s 件 ‧ 路口 %s 處 ‧ 步行網 %.0f km"
          % (D.meta["accidents"], D.meta["intersections"], D.w_len.sum() / 1000))

    print("建立 walk 基準…")
    w_edges, w_bands, w_med = build_walk(D)
    print("建立 ride 基準…")
    r_edges, r_bands, r_med = build_ride(D)

    w_vals, w_off = flatten(w_bands)
    r_vals, r_off = flatten(r_bands)
    np.savez_compressed(
        OUT,
        walk_edges=w_edges, walk_vals=w_vals, walk_off=w_off,
        walk_med=np.asarray(w_med),
        ride_edges=r_edges, ride_vals=r_vals, ride_off=r_off,
        ride_med=np.asarray(r_med),
        grid_m=GRID, pair_km=np.asarray(PAIR_KM), strata=core.STRATA)
    print("\n完成 %s  %.1f MB" % (os.path.relpath(OUT, BASE),
                                  os.path.getsize(OUT) / 1048576))
