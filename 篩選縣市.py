# -*- coding: utf-8 -*-
"""從 data/ 抽出指定縣市的 A1 / A2 事故資料，各合併成一個檔。

用法:
    python 篩選縣市.py              # 預設桃園市
    python 篩選縣市.py 桃園市 新竹縣  # 可指定多個

輸出: 縣市/桃園市_A1_2024-2026.csv、縣市/桃園市_A2_2024-2026.csv

篩選依據是「發生地點」的開頭縣市，不是「處理單位名稱警局層」——
國道、機場的事故由國道公路警察局／航空警察局處理，用單位篩會漏掉。
"""
import csv, glob, os, re, sys

BASE = os.path.dirname(os.path.abspath(__file__))
YEAR = re.compile(r"^\d{4}$")
LOC, UNIT = 6, 5          # 發生地點、處理單位名稱警局層
csv.field_size_limit(10 ** 7)

cities = sys.argv[1:] or ["桃園市"]
outdir = os.path.join(BASE, "縣市")
os.makedirs(outdir, exist_ok=True)

for city in cities:
    for kind in ("A1", "A2"):
        parts = sorted(glob.glob(os.path.join(BASE, "data", kind, f"{kind}_*.csv")))
        out = os.path.join(outdir, f"{city}_{kind}_2024-2026.csv")
        kept = scanned = 0
        units = {}
        with open(out, "w", encoding="utf-8-sig", newline="") as fo:
            w = csv.writer(fo, lineterminator="\r\n")
            for i, p in enumerate(parts):
                with open(p, encoding="utf-8-sig", newline="") as fi:
                    rd = csv.reader(fi)
                    hdr = next(rd)
                    if i == 0:
                        w.writerow(hdr)
                    for r in rd:
                        if not r or not YEAR.match(r[0]):   # 跳過檔尾註記列
                            continue
                        scanned += 1
                        if r[LOC].startswith(city):
                            w.writerow(r); kept += 1
                            units[r[UNIT]] = units.get(r[UNIT], 0) + 1
        mb = os.path.getsize(out) / 1048576
        print(f"{city} {kind}: {kept:,} / {scanned:,} 筆 ({kept/scanned*100:.1f}%)  {mb:.0f} MB  -> {os.path.relpath(out, BASE)}")
        for u, c in sorted(units.items(), key=lambda x: -x[1]):
            print(f"      處理單位 {u}: {c:,}")
