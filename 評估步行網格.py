# -*- coding: utf-8 -*-
r"""評估 500 公尺步行安全的網格解析度，並建立不覆蓋正式檔的 200m 基準。

執行：
    .venv\Scripts\python.exe 評估步行網格.py

輸出：
    reports/基準_200m_評估.npz

評估檔除了正式基準需要的分層分布，也保留每個格點的座標、曝險、事故加權值
與安全分數，供評估「預先算好安全面，查詢時只取附近格點」的做法。
"""
from __future__ import annotations

import math
import os
import time

import numpy as np

import 建立基準 as baseline
import 核心 as core


BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "基準_200m_評估.npz")
RADIUS = 500.0
BASELINE_GRID = 200.0
LOCAL_STEPS = (100.0, 150.0, 200.0, 250.0)

# 同時涵蓋市中心、重劃區、郊區與山區。座標只用於離線評估，不會進產品。
CENTERS = {
    "Taoyuan Station": (24.9892, 121.3143),
    "Zhongli Station": (24.9536, 121.2258),
    "Qingpu": (25.0130, 121.2149),
    "Nankan": (25.0530, 121.2870),
    "Bade": (24.9296, 121.2830),
    "Longtan": (24.8640, 121.2160),
    "Daxi": (24.8830, 121.2860),
    "Yangmei": (24.9120, 121.1460),
    "Fuxing": (24.8150, 121.3510),
}


def local_grid(D: core.Data, lat: float, lon: float, step: float) -> np.ndarray:
    """取固定於 TWD97 全市座標系的格點，避免使用者移動 1m 就整批位移。"""
    cx, cy = D.to_xy(lat, lon)
    xs = np.arange(
        math.ceil((cx - RADIUS) / step) * step,
        math.floor((cx + RADIUS) / step) * step + step,
        step,
    )
    ys = np.arange(
        math.ceil((cy - RADIUS) / step) * step,
        math.floor((cy + RADIUS) / step) * step + step,
        step,
    )
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    pts = pts[np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) <= RADIUS]
    return np.asarray([
        p for p in pts
        if D.poly is None or D.poly.contains(__import__("shapely").geometry.Point(*p))
    ])


def score_points(D: core.Data, pts: np.ndarray) -> tuple[np.ndarray, float]:
    t0 = time.perf_counter()
    scores = []
    for x, y in pts:
        _risk, _count, wsum, km, _idx = D.walk_risk(x, y)
        if km >= core.MIN_KM:
            scores.append(D.percentile(D.shrink(wsum, km, "walk"), km, "walk"))
    return np.asarray(scores, dtype=np.int16), time.perf_counter() - t0


def benchmark_local(D: core.Data, title: str) -> None:
    print("\n[%s] local candidate benchmark" % title)
    print("step_m centers candidates median_ms total_ms score_span unique_scores")
    for step in LOCAL_STEPS:
        counts, elapsed, spans, uniques = [], [], [], []
        for lat, lon in CENTERS.values():
            pts = local_grid(D, lat, lon, step)
            scores, seconds = score_points(D, pts)
            if not len(scores):
                continue
            counts.append(len(scores))
            elapsed.append(seconds)
            spans.append(int(scores.max() - scores.min()))
            uniques.append(len(np.unique(scores)))
        total_candidates = int(np.sum(counts))
        per_candidate_ms = 1000.0 * np.sum(elapsed) / max(1, total_candidates)
        print(
            "%6.0f %7d %10.1f %9.2f %8.1f %10.1f %13.1f"
            % (
                step,
                len(counts),
                np.median(counts),
                per_candidate_ms,
                1000.0 * np.sum(elapsed),
                np.median(spans),
                np.median(uniques),
            )
        )


def show_200m_neighborhoods(D: core.Data) -> None:
    print("\n[200m local neighborhoods] scores are citywide safety percentiles")
    print("center candidates min p25 median p75 max span bands top5_agree")
    for name, (lat, lon) in CENTERS.items():
        pts = local_grid(D, lat, lon, 200.0)
        scores, local_risks, bands = [], [], []
        cx, cy = D.to_xy(lat, lon)
        center_km = D.exposure(cx, cy)
        common_med = float(D.ref["walk"]["med"][D.band_of(center_km, "walk")])
        for x, y in pts:
            _risk, _count, wsum, km, _idx = D.walk_risk(x, y)
            if km < core.MIN_KM:
                continue
            scores.append(D.percentile(D.shrink(wsum, km, "walk"), km, "walk"))
            # 局部排序全部使用「點選中心所屬層」的同一個先驗，避免跨分層邊界
            # 造成名次跳動；數值越低代表歷史加權事故風險越低。
            local_risks.append((wsum + core.SHRINK_KM_WALK * common_med) /
                               (km + core.SHRINK_KM_WALK))
            bands.append(D.band_of(km, "walk") + 1)
        scores = np.asarray(scores, dtype=np.int16)
        local_risks = np.asarray(local_risks, dtype=np.float64)
        if not len(scores):
            continue
        q25, median, q75 = np.percentile(scores, [25, 50, 75])
        top_n = min(5, len(scores))
        city_top = set(np.argsort(-scores, kind="stable")[:top_n])
        local_top = set(np.argsort(local_risks, kind="stable")[:top_n])
        top_agree = round(100.0 * len(city_top & local_top) / top_n)
        print(
            "%-18s %10d %3d %3.0f %6.0f %3.0f %3d %4d %5d %10d%%"
            % (
                name,
                len(scores),
                int(scores.min()),
                q25,
                median,
                q75,
                int(scores.max()),
                int(scores.max() - scores.min()),
                len(set(bands)),
                top_agree,
            )
        )


def surface_score(
    exp: np.ndarray,
    wsum: np.ndarray,
    edges: np.ndarray,
    bands: list[np.ndarray],
    med: np.ndarray,
) -> np.ndarray:
    scores = np.empty(len(exp), dtype=np.int16)
    for j, (km, weight) in enumerate(zip(exp, wsum)):
        band = int(min(len(edges) - 2, max(0, np.searchsorted(edges, km, side="right") - 1)))
        adjusted = (weight + core.SHRINK_KM_WALK * med[band]) / (km + core.SHRINK_KM_WALK)
        arr = bands[band]
        lo = float(np.searchsorted(arr, adjusted, side="left"))
        hi = float(np.searchsorted(arr, adjusted, side="right"))
        pct = 100.0 * (1.0 - (lo + hi) / (2.0 * len(arr)))
        scores[j] = int(max(1, min(99, round(pct))))
    return scores


def build_200m(D: core.Data) -> dict[str, object]:
    all_pts = core.grid_points(np.load(core.NET_PATH)["bbox"], BASELINE_GRID, D.fwd)
    all_pts = all_pts[baseline.mask_inside(D.ring, all_pts)]
    print("\n[200m baseline] city cells=%d" % len(all_pts))

    kept, exp, wsum = [], [], []
    t0 = time.perf_counter()
    for i, (x, y) in enumerate(all_pts):
        km = D.exposure(x, y)
        if km < core.MIN_KM:
            continue
        idx = D.acc_tree.query_ball_point([x, y], core.R_WALK)
        if idx:
            idx = np.asarray(idx, dtype=np.int64)
            distance = np.hypot(D.g_xy[idx, 0] - x, D.g_xy[idx, 1] - y)
            weight = float((D.g_sev[idx] * (1.0 - (distance / core.R_WALK) ** 2)).sum())
        else:
            weight = 0.0
        kept.append((x, y))
        exp.append(km)
        wsum.append(weight)
        if i and i % 10000 == 0:
            print("  progress=%d/%d elapsed_s=%.1f" % (i, len(all_pts), time.perf_counter() - t0))

    pts = np.asarray(kept, dtype=np.float64)
    exp_a = np.asarray(exp, dtype=np.float64)
    wsum_a = np.asarray(wsum, dtype=np.float64)
    edges, bands, med_l = baseline.stratify(exp_a, wsum_a, exp_a, "walk")
    med = np.asarray(med_l, dtype=np.float64)
    vals, off = baseline.flatten(bands)
    scores = surface_score(exp_a, wsum_a, edges, bands, med)
    seconds = time.perf_counter() - t0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    np.savez_compressed(
        OUT,
        walk_edges=edges,
        walk_vals=vals,
        walk_off=off,
        walk_med=med,
        walk_x=pts[:, 0],
        walk_y=pts[:, 1],
        walk_km=exp_a,
        walk_wsum=wsum_a,
        walk_score=scores,
        grid_m=BASELINE_GRID,
        radius_m=core.R_WALK,
        strata=core.STRATA,
    )
    print(
        "  valid=%d zero_accident=%.1f%% build_s=%.1f file_mb=%.2f"
        % (len(pts), 100.0 * np.mean(wsum_a == 0), seconds, os.path.getsize(OUT) / 1048576.0)
    )
    print(
        "  score q05/q25/q50/q75/q95 = %s"
        % np.percentile(scores, [5, 25, 50, 75, 95]).round(1).tolist()
    )
    return {"edges": edges, "bands": bands, "med": med}


def compare_centers(D: core.Data, alt_ref: dict[str, object]) -> None:
    old_ref = D.ref
    print("\n[250m official vs 200m evaluation baseline] exact center score")
    print("center official_250 evaluation_200 difference")
    for name, (lat, lon) in CENTERS.items():
        x, y = D.to_xy(lat, lon)
        _risk, _count, wsum, km, _idx = D.walk_risk(x, y)
        official = D.percentile(D.shrink(wsum, km, "walk"), km, "walk")
        D.ref = {"walk": alt_ref}
        evaluation = D.percentile(D.shrink(wsum, km, "walk"), km, "walk")
        D.ref = old_ref
        print("%-18s %12d %14d %+10d" % (name, official, evaluation, evaluation - official))


def main() -> None:
    print("Loading walking data...")
    D = core.Data(load_routes=False)
    benchmark_local(D, "official 250m baseline")
    alt_ref = build_200m(D)
    compare_centers(D, alt_ref)
    old_ref = D.ref
    D.ref = {"walk": alt_ref}
    benchmark_local(D, "evaluation 200m baseline")
    show_200m_neighborhoods(D)
    D.ref = old_ref
    print("\nEvaluation file: %s" % OUT)


if __name__ == "__main__":
    main()
