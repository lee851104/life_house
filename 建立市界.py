# -*- coding: utf-8 -*-
"""從 OSM 取出桃園市界 → 市界.npz

為什麼需要
    百分位基準要在「桃園市內」取樣。若直接用經緯度 bbox 矩形，會有約四分之三
    的取樣點落在新北、新竹或海上——那裡有 OSM 道路卻沒有桃園的事故資料，
    於是被當成「零事故的安全地帶」，把整條分布往下拉，市區隨便一點都變後段班。

作法
    pbf 中 relation 排在 way 之後，一次掃描拿不到 relation 成員的座標，所以分兩趟：
      第 1 趟 只讀 relation，找到桃園市的行政邊界，記下成員 way id
      第 2 趟 只讀那些 way，取出座標，用 shapely 接成多邊形

用法
    python 建立市界.py
"""
import os

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
PBF = os.path.join(BASE, "raw", "taiwan-latest.osm.pbf")
OUT = os.path.join(BASE, "市界.npz")
TARGET = "桃園"


def find_relation():
    import osmium
    want = []
    fp = (osmium.FileProcessor(PBF)
          .with_filter(osmium.filter.EntityFilter(osmium.osm.RELATION))
          .with_filter(osmium.filter.KeyFilter("boundary")))
    for rel in fp:
        t = rel.tags
        if t.get("boundary") != "administrative":
            continue
        if t.get("admin_level") not in ("4", "5"):
            continue
        name = (t.get("name") or "") + (t.get("name:zh") or "")
        if TARGET not in name:
            continue
        ways = [m.ref for m in rel.members
                if m.type == "w" and m.role in ("outer", "")]
        want.append((t.get("admin_level"), name, ways))
    if not want:
        raise SystemExit("在 OSM 中找不到桃園市的行政邊界 relation")
    want.sort(key=lambda r: (r[0], -len(r[2])))
    lvl, name, ways = want[0]
    print("  找到 relation：%s（admin_level=%s，%d 條邊界 way）"
          % (name, lvl, len(ways)))
    return set(ways)


def collect_ways(way_ids):
    import osmium
    segs = {}
    fp = (osmium.FileProcessor(PBF)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.IdFilter(way_ids)))
    for way in fp:
        pts = []
        for nd in way.nodes:
            if nd.location.valid():
                pts.append((nd.location.lon, nd.location.lat))
        if len(pts) >= 2:
            segs[way.id] = pts
    print("  取到 %d / %d 條邊界 way 的座標" % (len(segs), len(way_ids)))
    return list(segs.values())


def build_polygon(segs):
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge, polygonize, unary_union

    merged = linemerge(MultiLineString([LineString(s) for s in segs]))
    polys = list(polygonize(merged))
    if not polys:
        # 邊界有缺口時，用微幅緩衝把接縫補起來再試一次
        merged = unary_union([LineString(s).buffer(1e-5) for s in segs])
        polys = list(polygonize(merged.boundary))
    if not polys:
        raise SystemExit("邊界 way 無法接成多邊形")
    poly = max(polys, key=lambda p: p.area)
    print("  多邊形完成，%d 個環，取面積最大者" % len(polys))
    return poly


if __name__ == "__main__":
    if not os.path.exists(PBF):
        raise SystemExit("找不到 %s，請先執行 python 建立路網.py" % PBF)

    print("第 1 趟：搜尋行政邊界 relation…")
    way_ids = find_relation()
    print("第 2 趟：取邊界 way 座標…")
    segs = collect_ways(way_ids)
    poly = build_polygon(segs)

    from pyproj import Transformer
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True)
    lon, lat = np.asarray(poly.exterior.coords).T
    x, y = fwd.transform(lon, lat)
    ring = np.column_stack([x, y])

    from shapely.geometry import Polygon
    area = Polygon(ring).area / 1e6
    print("\n面積 %.0f km²（桃園市實際約 1,221 km²）" % area)

    np.savez_compressed(OUT, ring=ring,
                        ll=np.column_stack([lon, lat]))
    print("完成 %s  %.0f KB" % (os.path.relpath(OUT, BASE),
                                os.path.getsize(OUT) / 1024))
