"""Start the local API and open the web interface after it is ready.

啟動.bat 只負責找到 .venv，其餘檢查與訊息都在這裡。

為什麼：.bat 由 cmd.exe 逐位元組解讀，用的是主控台字碼頁（繁中 Windows 是
950／Big5）。檔案裡只要有 UTF-8 中文，位元組邊界就會被誤判成 Big5 雙位元組，
解析器跟著錯位——實際症狀是 `echo` 被吃成 `ho`，而 for 迴圈裡的中文檔名
（"事故索引.db" 等）永遠比對不到，於是明明存在的檔案被報成「找不到」。
Python 沒有這個問題，所以中文訊息一律留在這一支。
"""
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from urllib.error import URLError

import uvicorn

BASE = os.path.dirname(os.path.abspath(__file__))
APP_URL = "http://127.0.0.1:8000/"
META_URL = APP_URL + "api/v3/meta"

# 缺任何一個就無法啟動；地名.db 是選配，少了它只是不能打字搜尋。
REQUIRED = ["事故索引.db", "路網.npz", "市界.npz", "基準.npz"]
OPTIONAL = ["地名.db"]

BUILD_STEPS = [
    ("下載資料.py", "下載 A1／A2 事故資料"),
    ("篩選縣市.py 桃園市", "篩出桃園市"),
    ("建立索引.py", "→ 事故索引.db"),
    ("建立路網.py", "→ 路網.npz"),
    ("建立市界.py", "→ 市界.npz"),
    ("建立基準.py", "→ 基準.npz"),
    ("建立地名.py", "→ 地名.db（選配）"),
]


def check_artifacts():
    """回傳 True 表示可以啟動。缺檔時印出中文指引。"""
    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(BASE, f))]
    if not missing:
        absent = [f for f in OPTIONAL if not os.path.exists(os.path.join(BASE, f))]
        if absent:
            print("[提示] 找不到 %s，地圖與分析照常運作，只是無法用文字搜尋地址。"
                  % "、".join(absent))
        return True

    print("[錯誤] 找不到建置產物：%s" % "、".join(missing))
    print()
    print("這些檔案不進版控（約 150 MB）。兩種取得方式：")
    print("  A. 從 GitHub Release 下載後放到專案根目錄")
    print("  B. 自行重建，依序執行：")
    for script, note in BUILD_STEPS:
        print(r"       .venv\Scripts\python.exe %-24s %s" % (script, note))
    return False


def open_when_ready():
    """Wait for FastAPI startup and then open the page once."""
    for _ in range(120):
        try:
            with urllib.request.urlopen(META_URL, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(APP_URL)
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.5)


def main():
    # 主控台字碼頁是 950 時，print 中文會丟 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    if not check_artifacts():
        return 1
    print("啟動中… 服務準備完成後會自動開啟 %s" % APP_URL)
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("api:app", host="127.0.0.1", port=8000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
