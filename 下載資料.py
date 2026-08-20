# -*- coding: utf-8 -*-
"""從 data.gov.tw 下載 2024–2026 (民國113–115) 年 A1 / A2 道路交通事故資料。

用法:
    python 下載資料.py

會建立:
    raw/          原始下載檔（ZIP / CSV）
    data/A1/      A1_2024.csv、A1_2025.csv、A1_2026.csv
    data/A2/      A2_YYYY_MM.csv（分月）

資料來源（政府資料開放平臺，提供機關：內政部警政署）
    113年傷亡道路交通事故資料  https://data.gov.tw/dataset/172969
    114年傷亡道路交通事故資料  https://data.gov.tw/dataset/177136
    即時交通事故資料(A1類)     https://data.gov.tw/dataset/12818
    即時交通事故資料(A2類)     https://data.gov.tw/dataset/13139

注意：即時檔（2026）只有當年度資料，且會隨時間往後補；年度檔（2024/2025）內容固定。
      A2 資料量大，完整下載＋解壓後約 1.4 GB。
"""
import os, re, shutil, time, urllib.request, zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "raw")
A1D = os.path.join(BASE, "data", "A1")
A2D = os.path.join(BASE, "data", "A2")
API = "https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}

# (檔名, dataset GUID, resource GUID)
YEAR_ZIPS = [
    ("113_traffic_accident.zip", "CCAD7AA8-5139-4066-8BE3-D6CC3154C137", "0A260239-9958-4046-8834-E68E5EC38406"),
    ("114_traffic_accident.zip", "FCD9C2D4-CB71-4EAA-AA4C-B088E5FE3157", "EF9B9192-7E4D-4E48-BF8A-AA3D4B298788"),
]
A1_LIVE = ("115_NPA_TMA1.csv", "02D40248-7CAA-4354-82EA-E27AB8DCAB39", "82C57731-C48D-47B1-88B8-D0A169C8347E")
A2_LIVE_DS = "986931B3-0E46-4F94-BF52-A2911499301F"
A2_LIVE = {                     # 民國115年 分月 ZIP
    "01": "FC0DAB8E-D7A2-41A6-8020-091F256603BD", "02": "3DD905E3-D105-472B-A7A8-9A06C64AE432",
    "03": "93B4A362-08D1-4EAC-BF05-272F93C248B0", "04": "2998BAD1-F389-4DA0-8D82-B43CDCFCBB3E",
    "05": "B42BC1F9-4628-44EC-8DBB-9ADB252A8B90", "06": "D21D335D-3B93-4A03-BE08-AC1F86A185AF",
    "07": "FDCEB5F4-C80A-4337-977F-4AF8395590BB", "08": "E8F49F1E-D599-4029-A2AC-862086A7E1ED",
}
SKIP = {"file.csv", "manifest.csv", "schema-file.csv"}


def download(name, ds, res):
    dest = os.path.join(RAW, name)
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        print(f"SKIP {name}"); return dest
    url = f"{API}{ds}/resource/{res}/download"
    for attempt in (1, 2, 3):
        try:
            t0 = time.time()
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=900) as r:
                data = r.read()
            with open(dest, "wb") as f:
                f.write(data)
            print(f"OK   {name} {len(data)/1048576:.1f} MB in {time.time()-t0:.0f}s")
            return dest
        except Exception as e:
            print(f"RETRY{attempt} {name}: {e}"); time.sleep(5)
    raise RuntimeError(f"下載失敗: {name}")


def real_name(info):
    """ZIP 內的中文檔名多半沒設 UTF-8 flag，需要從 cp437 還原。"""
    if info.flag_bits & 0x800:
        return info.filename
    for enc in ("utf-8", "cp950"):
        try:
            return info.filename.encode("cp437").decode(enc)
        except Exception:
            pass
    return info.filename


def extract(zpath, ad_year, force_month=None):
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            base = os.path.basename(real_name(info))
            if not base.lower().endswith(".csv") or base.lower() in SKIP:
                continue
            if force_month:                                  # 即時 A2 月檔
                dest = os.path.join(A2D, f"A2_{ad_year}_{force_month}.csv")
            elif "A1" in base:
                dest = os.path.join(A1D, f"A1_{ad_year}.csv")
            elif "A2" in base:
                m = re.search(r"_(\d{1,2})\.csv$", base)
                dest = os.path.join(A2D, f"A2_{ad_year}_{int(m.group(1)):02d}.csv" if m
                                    else f"A2_{ad_year}_00.csv")
            else:
                continue
            with z.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            print(f"     {base} -> {os.path.relpath(dest, BASE)}")


if __name__ == "__main__":
    for d in (RAW, A1D, A2D):
        os.makedirs(d, exist_ok=True)

    for name, ds, res in YEAR_ZIPS:                          # 2024 / 2025 全年 A1+A2
        extract(download(name, ds, res), 1911 + int(name[:3]))

    name, ds, res = A1_LIVE                                  # 2026 A1
    shutil.copyfile(download(name, ds, res), os.path.join(A1D, "A1_2026.csv"))
    print(f"     {name} -> data/A1/A1_2026.csv")

    for mon, res in sorted(A2_LIVE.items()):                 # 2026 A2 分月
        extract(download(f"115_NPA_TMA2_{mon}.zip", A2_LIVE_DS, res), 2026, force_month=mon)

    print("\n完成。接著可執行： python 篩選縣市.py 桃園市")
