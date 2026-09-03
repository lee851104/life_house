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

  3. 收縮後才存
     查詢端比的是收縮過的值，基準若存未收縮的值就是拿蘋果比橘子。

本腳本只建立 walk（走路）基準，與線上服務提供的分析範圍一致。

用法
    python -m src.models.建立基準
"""
import os
import time

import numpy as np

from src.features.risk import epanechnikov_weights
from src.models import 核心 as core
from src.paths import ROOT as BASE

OUT = os.path.join(BASE, "基準.npz")

GRID = 250.0          # walk 基準網格（公尺）


def mask_inside(ring, pts):
    from shapely import contains
    from shapely import points as mk_points
    from shapely.geometry import Polygon
    return contains(Polygon(ring), mk_points(pts))


def stratify(key, wsum, km, n_band=core.STRATA):
    """依 key 切成 n_band 層。

    key    分層鍵：500m 圈內可步行路網 km
    wsum   事故嚴重度加權總和
    km     算率與收縮用的分母：曝險 km

    分兩趟：先用原始率算各層中位數，再用同一條收縮公式把每個樣本也收縮過才
    存起來。查詢端比的是收縮後的值，基準若存未收縮的值就是拿蘋果比橘子。
    """
    k0 = core.SHRINK_KM_WALK
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
    print("  市界內 %d 格（%.0f km2）" % (len(pts), len(pts) * GRID ** 2 / 1e6))

    t0 = time.time()
    exp, wsum_l = [], []
    for i, (x, y) in enumerate(pts):
        km = D.exposure(x, y)
        if km < core.MIN_KM:
            continue
        idx = D.acc_tree.query_ball_point([x, y], core.R_WALK)
        if idx:
            idx = np.asarray(idx, dtype=np.int64)
            idx = idx[D.g_ped[idx]]
            d = np.hypot(D.g_xy[idx, 0] - x, D.g_xy[idx, 1] - y)
            w = float((D.g_sev[idx] * epanechnikov_weights(d, core.R_WALK)).sum())
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
    return stratify(exp, wsum, exp)


def flatten(bands):
    off = np.zeros(len(bands) + 1, dtype=np.int64)
    for i, b in enumerate(bands):
        off[i + 1] = off[i] + b.size
    return (np.concatenate(bands) if bands else np.empty(0)), off


if __name__ == "__main__":
    print("載入索引與路網…")
    D = core.Data(need_ref=False)
    if D.ring is None:
        raise SystemExit("找不到 市界.npz，請先執行： python -m src.data.建立市界")
    print("  事故 %s 件 ‧ 路口 %s 處 ‧ 步行網 %.0f km"
          % (D.meta["accidents"], D.meta["intersections"], D.w_len.sum() / 1000))

    print("建立 walk 基準…")
    w_edges, w_bands, w_med = build_walk(D)
    w_vals, w_off = flatten(w_bands)
    np.savez_compressed(
        OUT,
        walk_edges=w_edges, walk_vals=w_vals, walk_off=w_off,
        walk_med=np.asarray(w_med),
        grid_m=GRID, strata=core.STRATA)
    print("\n完成 %s  %.1f MB" % (os.path.relpath(OUT, BASE),
                                  os.path.getsize(OUT) / 1048576))
