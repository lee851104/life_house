# -*- coding: utf-8 -*-
"""將 data/A2 內的分月 CSV 合併成單一年度檔（檔案很大，視需要才執行）。

用法:
    python 合併A2.py 2024          # 產生 A2_2024_合併.csv
    python 合併A2.py 2024 2025 2026
"""
import csv, glob, os, re, sys

BASE = os.path.dirname(os.path.abspath(__file__))
A2D = os.path.join(BASE, "data", "A2")
YEAR = re.compile(r"^\d{4}$")
csv.field_size_limit(10 ** 7)

for year in (sys.argv[1:] or ["2024", "2025", "2026"]):
    parts = sorted(glob.glob(os.path.join(A2D, f"A2_{year}_*.csv")))
    if not parts:
        print(f"找不到 {year} 年的分月檔"); continue
    out = os.path.join(BASE, f"A2_{year}_合併.csv")
    n = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as fo:
        w = csv.writer(fo, lineterminator="\r\n")
        for i, p in enumerate(parts):
            with open(p, encoding="utf-8-sig", newline="") as fi:
                rd = csv.reader(fi)
                hdr = next(rd)
                if i == 0:
                    w.writerow(hdr)
                for r in rd:
                    if r and YEAR.match(r[0]):   # 跳過檔尾的「資料提供日期／事故類別」註記列
                        w.writerow(r); n += 1
            print(f"  + {os.path.basename(p)}")
    print(f"{out}  共 {n:,} 筆  {os.path.getsize(out)/1048576:.0f} MB")
