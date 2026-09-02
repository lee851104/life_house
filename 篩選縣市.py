# -*- coding: utf-8 -*-
"""從 data/ 抽出指定縣市的 A1 / A2 事故資料，各合併成一個檔。

用法:
    python 篩選縣市.py              # 預設桃園市
    python 篩選縣市.py 桃園市 新竹縣  # 可指定多個

輸出: 縣市/桃園市_A1_2022-2026.csv、縣市/桃園市_A2_2022-2026.csv

篩選依據是「發生地點」的開頭縣市，不是「處理單位名稱警局層」——
國道、機場的事故由國道公路警察局／航空警察局處理，用單位篩會漏掉。

只吃 51 欄的逐當事者格式。data/ 裡還有 2021 年的舊格式檔（A1_2021.csv、
A2_2021_07-12.csv，只有 6 欄），它們由 建立索引.py 的 LEGACY_2021 另外處理。
這裡不排除掉的話會踩到一個安靜的坑：sorted() 之後 2021 檔排在最前面，
表頭就會用它的 6 欄版本，寫出一個表頭與內容對不上的合併檔。
"""
import csv
import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
YEAR = re.compile(r"^\d{4}$")
LOC, UNIT = 6, 5          # 發生地點、處理單位名稱警局層
csv.field_size_limit(10 ** 7)


def main(cities=None):
    """篩選指定縣市。cities 為 None 時取命令列參數，預設桃園市。"""
    cities = cities or sys.argv[1:] or ["桃園市"]
    outdir = os.path.join(BASE, "縣市")
    os.makedirs(outdir, exist_ok=True)

    for city in cities:
        for kind in ("A1", "A2"):
            parts = sorted(glob.glob(os.path.join(BASE, "data", kind, f"{kind}_*.csv")))
            out = os.path.join(outdir, f"{city}_{kind}_2022-2026.csv")
            kept = scanned = 0
            units = {}
            with open(out, "w", encoding="utf-8-sig", newline="") as fo:
                w = csv.writer(fo, lineterminator="\r\n")
                wrote_header = False
                for p in parts:
                    with open(p, encoding="utf-8-sig", newline="") as fi:
                        rd = csv.reader(fi)
                        hdr = next(rd)
                        if len(hdr) < 51:          # 2021 舊格式，交給 建立索引.py
                            print(f"      略過 {os.path.basename(p)}（{len(hdr)} 欄，非 51 欄格式）")
                            continue
                        if not wrote_header:
                            w.writerow(hdr)
                            wrote_header = True
                        for r in rd:
                            if not r or not YEAR.match(r[0]):   # 跳過檔尾註記列
                                continue
                            scanned += 1
                            if r[LOC].startswith(city):
                                w.writerow(r); kept += 1
                                units[r[UNIT]] = units.get(r[UNIT], 0) + 1
            mb = os.path.getsize(out) / 1048576
            pct = (kept / scanned * 100) if scanned else 0.0
            print(f"{city} {kind}: {kept:,} / {scanned:,} 筆 ({pct:.1f}%)  {mb:.0f} MB  -> {os.path.relpath(out, BASE)}")
            for u, c in sorted(units.items(), key=lambda x: -x[1]):
                print(f"      處理單位 {u}: {c:,}")


# 沒有這個保護的話，只要有人 import 篩選縣市（寫測試、或在別的腳本裡重用
# 它的常數），就會整段重跑一次 290 MB 的篩選並覆寫 縣市/*.csv。
# 其他八支建置腳本本來就都有，只有這支漏掉。
if __name__ == "__main__":
    main()
