# -*- coding: utf-8 -*-
"""比較兩年與五年行人事故樣本，評估 500 公尺步行安全分數的穩定性。

五年窗固定為 2021-07-01～2026-06-30，共 60 個完整月份。只納入涉及行人的
A1 死亡事故與 A2 受傷事故；車與車、車輛本身不進入這份評估。

前置：需先執行 python -m src.models.評估步行網格 產生 reports/基準_200m_評估.npz，
      本腳本的網格比較段落會讀取它。

輸出：reports/五年行人事故評估.json
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import date, datetime

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

from src.data import 建立索引 as indexer
from src.features.risk import epanechnikov_weights
from src.paths import ROOT as BASE

OUT = os.path.join(BASE, "reports", "五年行人事故評估.json")
FROM_5Y, FROM_2Y, TO = "20210701", "20240701", "20260630"
RADIUS_M = 500.0
FATAL_MULTIPLIER = 5.0

DETAILED = [
    os.path.join(BASE, "縣市", "桃園市_A1_2022-2026.csv"),
    os.path.join(BASE, "縣市", "桃園市_A2_2022-2026.csv"),
]
LEGACY = [
    ("A1", os.path.join(BASE, "data", "A1", "A1_2021.csv")),
    ("A2", os.path.join(BASE, "data", "A2", "A2_2021_07-12.csv")),
]

CENTERS = {
    "桃園車站": (24.9892, 121.3143),
    "中壢車站": (24.9536, 121.2258),
    "中原大學": (24.9576, 121.2406),
    "青埔": (25.0130, 121.2149),
    "南崁": (25.0530, 121.2870),
    "八德": (24.9296, 121.2830),
}


def parse_roc_time(value: str) -> tuple[str, str]:
    m = re.match(r"(\d+)年(\d+)月(\d+)日\s+(\d+)時(\d+)分", value.strip())
    if not m:
        return "", ""
    y, mo, day, hour, minute = map(int, m.groups())
    return f"{y + 1911:04d}{mo:02d}{day:02d}", f"{hour:02d}{minute:02d}00"


def legacy_accidents() -> list[dict]:
    rows = []
    for category, path in LEGACY:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.reader(f)
            next(rd)
            for row in rd:
                if len(row) < 6 or not row[1].startswith("桃園市") or "行人" not in row[3]:
                    continue
                ymd, hms = parse_roc_time(row[0])
                if not (FROM_5Y <= ymd <= TO) or "國道" in row[1]:
                    continue
                try:
                    lon, lat = float(row[4]), float(row[5])
                except ValueError:
                    continue
                dead, hurt = indexer.parse_casualty(row[2])
                rows.append({
                    "ymd": ymd, "hms": hms, "cat": category,
                    "lat": lat, "lon": lon, "dead": dead, "hurt": hurt,
                    "atype": "人與車", "veh": [row[3]], "loc": row[1],
                })
    return rows


def detailed_accidents() -> list[dict]:
    indexer.SRC = DETAILED
    indexer.FROM, indexer.TO = "20220101", TO
    return [
        a for a in indexer.read_accidents()
        if not indexer.is_motorway(a)
        and indexer.is_pedestrian_accident(a)
    ]


def recency_weight(ymd: str) -> float:
    occurred = datetime.strptime(ymd, "%Y%m%d").date()
    end = date(2026, 6, 30)
    age_years = max(0.0, (end - occurred).days / 365.25)
    band = min(4, int(age_years))
    return (1.0, 0.8, 0.6, 0.4, 0.2)[band]


def summarize_years(accidents: list[dict]) -> dict:
    result = {}
    for year in range(2021, 2027):
        items = [a for a in accidents if a["ymd"].startswith(str(year))]
        a1 = sum(str(a["cat"]).startswith("A1") for a in items)
        result[str(year)] = {
            "period": "07-12" if year == 2021 else ("01-06" if year == 2026 else "01-12"),
            "pedestrian_accidents": len(items),
            "A1_fatal": int(a1),
            "A2_injury": int(len(items) - a1),
        }
    return result


def grid_stats(accidents: list[dict], grid: np.lib.npyio.NpzFile) -> tuple[dict, dict]:
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
    lon = np.asarray([a["lon"] for a in accidents], dtype=np.float64)
    lat = np.asarray([a["lat"] for a in accidents], dtype=np.float64)
    ax, ay = fwd.transform(lon, lat)
    points = np.column_stack([ax, ay])
    tree = cKDTree(points)
    ymd = np.asarray([a["ymd"] for a in accidents], dtype=object)
    recent = ymd >= FROM_2Y
    time_w = np.asarray([recency_weight(a["ymd"]) for a in accidents])
    severity = np.asarray([
        FATAL_MULTIPLIER if str(a["cat"]).startswith("A1") else 1.0 for a in accidents
    ])

    grid_xy = np.column_stack([grid["walk_x"], grid["walk_y"]])
    raw_5y, raw_2y, burden_5y, burden_2y = [], [], [], []
    for gx, gy in grid_xy:
        idx = np.asarray(tree.query_ball_point([gx, gy], RADIUS_M), dtype=np.int64)
        if not len(idx):
            raw_5y.append(0); raw_2y.append(0); burden_5y.append(0.0); burden_2y.append(0.0)
            continue
        distance = np.hypot(points[idx, 0] - gx, points[idx, 1] - gy)
        spatial = epanechnikov_weights(distance, RADIUS_M)
        raw_5y.append(len(idx))
        raw_2y.append(int(recent[idx].sum()))
        burden_5y.append(float(np.sum(spatial * time_w[idx] * severity[idx])))
        burden_2y.append(float(np.sum(spatial[recent[idx]] * severity[idx][recent[idx]])))

    raw_5y = np.asarray(raw_5y, dtype=np.int32)
    raw_2y = np.asarray(raw_2y, dtype=np.int32)
    burden_5y = np.asarray(burden_5y, dtype=np.float64)
    burden_2y = np.asarray(burden_2y, dtype=np.float64)

    def describe(values: np.ndarray) -> dict:
        return {
            "zero_percent": round(float(np.mean(values == 0) * 100), 1),
            "at_most_2_percent": round(float(np.mean(values <= 2) * 100), 1),
            "q10_q25_q50_q75_q90_q95": np.percentile(values, [10, 25, 50, 75, 90, 95]).round(1).tolist(),
            "max": round(float(values.max()), 1),
        }

    center_rows = {}
    for name, (clat, clon) in CENTERS.items():
        cx, cy = fwd.transform(clon, clat)
        i = int(np.argmin(np.hypot(grid_xy[:, 0] - cx, grid_xy[:, 1] - cy)))
        local = np.hypot(grid_xy[:, 0] - cx, grid_xy[:, 1] - cy) <= RADIUS_M

        def local_safety_percentile(values: np.ndarray,
                                    neighbourhood=local, center=i) -> int:
            peers = values[neighbourhood]
            value = values[center]
            safer_than = np.sum(peers > value) + 0.5 * np.sum(peers == value)
            return int(round(100.0 * safer_than / len(peers)))

        center_rows[name] = {
            "two_year_count": int(raw_2y[i]),
            "five_year_count": int(raw_5y[i]),
            "two_year_weighted_burden": round(float(burden_2y[i]), 1),
            "five_year_weighted_burden": round(float(burden_5y[i]), 1),
            "two_year_local_safety_percentile": local_safety_percentile(burden_2y),
            "five_year_local_safety_percentile": local_safety_percentile(burden_5y),
            "local_candidate_cells": int(local.sum()),
        }
    return {
        "valid_200m_cells": int(len(grid_xy)),
        "two_year_raw_count": describe(raw_2y),
        "five_year_raw_count": describe(raw_5y),
        "five_year_recency_severity_burden": describe(burden_5y),
    }, center_rows


def main() -> None:
    accidents = legacy_accidents() + detailed_accidents()
    accidents = [a for a in accidents if FROM_5Y <= a["ymd"] <= TO]
    accidents.sort(key=lambda a: (a["ymd"], a["hms"], a["lat"], a["lon"]))
    grid = np.load(os.path.join(BASE, "reports", "基準_200m_評估.npz"))
    grid_summary, centers = grid_stats(accidents, grid)
    two_year = [a for a in accidents if a["ymd"] >= FROM_2Y]
    payload = {
        "period": {"from": "2021-07-01", "to": "2026-06-30", "months": 60},
        "method": {
            "scope": "桃園市、涉及行人的 A1/A2 事故",
            "radius_m": 500,
            "grid_m": 200,
            "recency_weights_by_age_year": [1.0, 0.8, 0.6, 0.4, 0.2],
            "A1_fatal_multiplier": FATAL_MULTIPLIER,
            "A2_injury_multiplier": 1.0,
        },
        "events": {
            "five_year": len(accidents),
            "two_year": len(two_year),
            "unique_public_coordinates_five_year": len({(a["lat"], a["lon"]) for a in accidents}),
            "by_year": summarize_years(accidents),
        },
        "grid_comparison": grid_summary,
        "sample_locations": centers,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n輸出：" + OUT)


if __name__ == "__main__":
    main()
