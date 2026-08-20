# -*- coding: utf-8 -*-
"""建立桃園路網：taiwan-latest.osm.pbf → 路網.npz

兩張網
  walk  可步行路網。切成 <=20m 的小段並記錄中點與長度，
        用來算「圈內有多少路可走」（曝險）。行人到不了的國道／快速公路排除。
  ride  普通重型機車可行路網。含節點座標與有向邊（處理單行道），
        用 scipy Dijkstra 算路線。國道禁行機車故排除。

座標系一律 EPSG:3826（TWD97 TM2），單位公尺。

用法
    python 建立路網.py            # pbf 不存在會自動下載（約 326 MB）
"""
import math
import os
import sys
import time
import urllib.request

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
PBF = os.path.join(BASE, "raw", "taiwan-latest.osm.pbf")
OUT = os.path.join(BASE, "路網.npz")
PBF_URL = "https://download.geofabrik.de/asia/taiwan-latest.osm.pbf"

# 桃園市範圍，外擴 0.03 度免得邊界路段被切斷
BBOX = (24.52, 120.93, 25.17, 121.52)      # south, west, north, east
SEG = 20.0                                  # 曝險用的細切段長（公尺）

# 行人可走。trunk（多為快速公路）與 motorway 排除。
WALK_OK = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential",
    "service", "unclassified", "tertiary", "tertiary_link", "secondary",
    "secondary_link", "primary", "primary_link", "track", "cycleway", "road",
}
# 普通重型機車可行。motorway（國道）禁行；人行道類排除。
RIDE_OK = {
    "trunk", "trunk_link", "primary", "primary_link", "secondary",
    "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road",
}
NO = {"no", "private", "destination"}


def download_pbf():
    if os.path.exists(PBF) and os.path.getsize(PBF) > 10 << 20:
        print("  pbf 已存在 %.0f MB" % (os.path.getsize(PBF) / 1048576))
        return
    os.makedirs(os.path.dirname(PBF), exist_ok=True)
    print("  下載 %s …" % PBF_URL)
    t0 = time.time()
    req = urllib.request.Request(PBF_URL, headers={"User-Agent": "lifehouse/1.0"})
    with urllib.request.urlopen(req, timeout=1800) as r, open(PBF, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print("  完成 %.0f MB (%.0fs)" % (os.path.getsize(PBF) / 1048576, time.time() - t0))


def build():
    import osmium
    from pyproj import Transformer

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
    s, w, n, e = BBOX

    walk_mid, walk_len = [], []      # 曝險：細切段中點 + 長度
    node_id = {}                     # OSM node id → 內部索引
    node_xy = []
    ride_edges = []                  # (u, v, 長度)
    stat = {"ways": 0, "walk": 0, "ride": 0}

    def idx_of(nid, x, y):
        i = node_id.get(nid)
        if i is None:
            i = node_id[nid] = len(node_xy)
            node_xy.append((x, y))
        return i

    t0 = time.time()
    # 必須讀進 node 才能建座標索引（pbf 中 node 早於 way），所以不能在讀取階段
    # 就只挑 WAY；改成全部讀、用 C++ 端的 filter 只把 way 交給 Python。
    fp = (osmium.FileProcessor(PBF)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.KeyFilter("highway")))
    for way in fp:
        tags = way.tags
        hw = tags.get("highway")
        if not hw:
            continue
        # 先抓座標，順便做 bbox 與無效節點的篩選
        pts = []
        inside = False
        try:
            for nd in way.nodes:
                if not nd.location.valid():
                    pts = []
                    break
                la, lo = nd.location.lat, nd.location.lon
                if s <= la <= n and w <= lo <= e:
                    inside = True
                pts.append((nd.ref, lo, la))
        except Exception:
            continue
        if not inside or len(pts) < 2:
            continue
        stat["ways"] += 1

        xy = [(nid,) + fwd.transform(lo, la) for nid, lo, la in pts]

        blocked = (tags.get("access") in NO)
        # ── 步行網：細切成 <=20m 的段，記中點與長度 ──
        if hw in WALK_OK and tags.get("foot") not in NO and not blocked:
            for (_, x1, y1), (_, x2, y2) in zip(xy, xy[1:]):
                d = math.hypot(x2 - x1, y2 - y1)
                if d <= 0:
                    continue
                k = max(1, int(math.ceil(d / SEG)))
                for j in range(k):
                    t = (j + 0.5) / k
                    walk_mid.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
                    walk_len.append(d / k)
            stat["walk"] += 1

        # ── 機車網：節點 + 有向邊，處理單行道 ──
        if (hw in RIDE_OK and not blocked
                and tags.get("motorcycle") not in NO
                and tags.get("motor_vehicle") not in NO):
            ow = (tags.get("oneway") or "no").lower()
            f2 = ow in ("yes", "1", "true")
            b2 = ow == "-1"
            prev = None
            for nid, x, y in xy:
                cur = idx_of(nid, x, y)
                if prev is not None:
                    px, py = node_xy[prev]
                    d = math.hypot(x - px, y - py)
                    if d > 0:
                        if not b2:
                            ride_edges.append((prev, cur, d))
                        if not f2:
                            ride_edges.append((cur, prev, d))
                prev = cur
            stat["ride"] += 1

    print("  掃過 %d 條桃園道路 → 步行 %d 條、機車 %d 條 (%.0fs)"
          % (stat["ways"], stat["walk"], stat["ride"], time.time() - t0))

    walk_mid = np.asarray(walk_mid, dtype=np.float32)
    walk_len = np.asarray(walk_len, dtype=np.float32)
    nodes = np.asarray(node_xy, dtype=np.float32)
    ed = np.asarray(ride_edges, dtype=np.float64)

    print("  步行網 %.0f km，細切 %d 段" % (walk_len.sum() / 1000, len(walk_len)))
    print("  機車網 %d 節點、%d 有向邊" % (len(nodes), len(ed)))

    np.savez_compressed(
        OUT,
        walk_mid=walk_mid, walk_len=walk_len,
        nodes=nodes,
        ride_u=ed[:, 0].astype(np.int32),
        ride_v=ed[:, 1].astype(np.int32),
        ride_w=ed[:, 2].astype(np.float32),
        bbox=np.asarray(BBOX, dtype=np.float64),
        seg=np.asarray([SEG]),
    )
    print("\n完成 %s  %.0f MB" % (os.path.relpath(OUT, BASE),
                                  os.path.getsize(OUT) / 1048576))


if __name__ == "__main__":
    print("取得 OSM 資料…")
    download_pbf()
    if not os.path.exists(PBF):
        sys.exit("pbf 下載失敗")
    print("建立路網…")
    build()
