"""Start the local API, and (with --boot) bring the public demo up at logon.

兩種模式
    python -m src.serving.launcher            本機使用：起服務並自動開瀏覽器
                                              （啟動.bat）
    python -m src.serving.launcher --boot     公開展示：發布 Tailscale Funnel、
                                              起服務，就緒後印出本機與公開網址
                                              的健檢結果（開機自動啟動.bat）

兩種模式共用同一套建置產物檢查與 uvicorn 啟動，不另寫一份——這個專案在
「同一件事寫兩份」上付過代價，見 src/features/risk.py 的說明。

為什麼中文訊息全在這裡、.bat 只剩一層殼：
    .bat 由 cmd.exe 逐位元組解讀，用的是主控台字碼頁（繁中 Windows 是
    950／Big5）。檔案裡只要有 UTF-8 中文，位元組邊界就會被誤判成 Big5 雙位元組，
    解析器跟著錯位——實際症狀是 `echo` 被吃成 `ho`，而 for 迴圈裡的中文檔名
    （"事故索引.db" 等）永遠比對不到，於是明明存在的檔案被報成「找不到」。
    Python 沒有這個問題。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from urllib.error import HTTPError, URLError

import uvicorn

from src.paths import ROOT as BASE

PORT = 8000
APP_URL = "http://127.0.0.1:%d/" % PORT
META_URL = APP_URL + "api/v3/meta"
LOG_PATH = os.path.join(BASE, "logs", "serve.log")
LINE = "=" * 64

TAILSCALE = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "Tailscale", "tailscale.exe")

# 缺任何一個就無法啟動；地名.db 是選配，少了它只是不能打字搜尋。
REQUIRED = ["事故索引.db", "路網.npz", "市界.npz", "基準.npz"]
OPTIONAL = ["地名.db"]

BUILD_STEPS = [
    ("-m src.data.下載資料", "下載 A1／A2 事故資料"),
    ("-m src.data.篩選縣市 桃園市", "篩出桃園市"),
    ("-m src.data.建立索引", "→ 事故索引.db"),
    ("-m src.data.建立路網", "→ 路網.npz"),
    ("-m src.data.建立市界", "→ 市界.npz"),
    ("-m src.models.建立基準", "→ 基準.npz"),
    ("-m src.data.建立地名", "→ 地名.db（選配）"),
]


def log(msg=""):
    """印到視窗，同時留一份到 logs/serve.log。

    視窗關掉之後就查不到剛才發生什麼事，所以狀態訊息另外落檔；uvicorn 自己的
    請求日誌仍然只在視窗裡。
    """
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass          # 落檔失敗不該擋住服務啟動


def check_artifacts():
    """回傳 True 表示可以啟動。缺檔時印出中文指引。"""
    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(BASE, f))]
    if not missing:
        absent = [f for f in OPTIONAL if not os.path.exists(os.path.join(BASE, f))]
        if absent:
            log("      [提示] 找不到 %s，地圖與分析照常運作，只是無法用文字搜尋地址。"
                % "、".join(absent))
        return True

    log("      [錯誤] 找不到建置產物：%s" % "、".join(missing))
    log()
    log("      這些檔案不進版控（約 150 MB）。兩種取得方式：")
    log("        A. 從 GitHub Release 下載後放到專案根目錄")
    log("        B. 自行重建，依序執行：")
    for script, note in BUILD_STEPS:
        log(r"             .venv\Scripts\python.exe %-28s %s" % (script, note))
    return False


# --------------------------------------------------------------- Tailscale
def _tailscale(args, timeout=15):
    """跑一次 tailscale CLI。回傳 (returncode, stdout)；沒安裝則回 (None, "")。"""
    if not os.path.exists(TAILSCALE):
        return None, ""
    try:
        done = subprocess.run([TAILSCALE] + args, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
        return done.returncode, done.stdout
    except (OSError, subprocess.SubprocessError):
        return None, ""


def wait_for_tailscale(timeout_s=60):
    """開機後 tailscaled 要一段時間才連得上，等它。回傳 True 表示已連線。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        code, _ = _tailscale(["status"], timeout=5)
        if code is None:
            return False              # 沒裝，不必等
        if code == 0:
            return True
        time.sleep(3)
    return False


def public_url():
    """從 tailscale status --json 取本機 DNS 名稱，不把網址寫死在程式裡。"""
    code, out = _tailscale(["status", "--json"], timeout=10)
    if code != 0 or not out:
        return None
    try:
        name = json.loads(out).get("Self", {}).get("DNSName", "").rstrip(".")
    except ValueError:
        return None
    return "https://%s" % name if name else None


def publish_funnel():
    """冪等：Funnel 設定撐過重開機就等於沒事，沒撐過就把公開網址放回去。"""
    code, _ = _tailscale(["funnel", "--bg", str(PORT)], timeout=30)
    return code == 0


# ------------------------------------------------------------- 健康檢查
def port_busy():
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def http_status(url, timeout=10):
    """回傳 HTTP 狀態碼；連不上回 None。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except HTTPError as err:
        return err.code               # 500 也是資訊，不能吞掉
    except (OSError, URLError):
        return None


def _verdict(code):
    if code == 200:
        return "200 OK"
    return "連不上" if code is None else "異常（HTTP %s）" % code


def wait_until_ready(timeout_s=120):
    """等 FastAPI 載入完 420 MB 的索引與路網。回傳 True 表示就緒。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if http_status(META_URL, timeout=2) == 200:
            return True
        time.sleep(0.5)
    return False


def report_when_ready(url, owns_service=True):
    """服務就緒後印出健檢結果——展示前最想確認的就是這幾行。

    owns_service 為 False 表示 8000 埠上跑的是別人（例如殘留的舊排程），
    這個視窗只是回報，關掉它不會影響公開網址。
    """
    if not wait_until_ready():
        log()
        log("[警告] 兩分鐘內沒等到服務就緒，請看上面 uvicorn 的訊息。")
        return
    log()
    log(LINE)
    log(" 服務就緒" if owns_service else " 目前狀態")
    log("   本機   %-42s %s" % (APP_URL, _verdict(http_status(APP_URL))))
    if url:
        log("   公開   %-42s %s" % (url, _verdict(http_status(url, timeout=20))))
    else:
        log("   公開   未發布（找不到 tailscale.exe 或尚未登入 Tailscale）")
    log()
    if owns_service:
        log(" 這個視窗就是服務本體，關掉視窗公開網址就會停。")
    else:
        log(" 服務是別的行程起的，關掉這個視窗不會影響它。")
    log(LINE)


def open_when_ready():
    """本機模式：服務就緒後開一次瀏覽器。"""
    if wait_until_ready():
        webbrowser.open(APP_URL)


# ------------------------------------------------------------------ 進入點
def boot():
    """開機自動啟動.bat 走的路徑：可見視窗 + 四段檢查 + 健檢回報。"""
    log()
    log(LINE)
    log(" Life House 安居指數 — 開機啟動   %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log(LINE)

    log("[1/4] 建置產物")
    if not check_artifacts():
        log()
        log("      服務沒有啟動。")
        return 1
    log("      必要檔案齊全。")

    log("[2/4] %d 埠" % PORT)
    already = port_busy()
    if already:
        log("      已經有服務在 %d 埠，這個視窗不會再起一份。" % PORT)
    else:
        log("      可用。")

    log("[3/4] Tailscale")
    url = None
    if wait_for_tailscale():
        url = public_url()
        ok = publish_funnel()
        log("      已連線；公開網址 %s%s"
            % (url or "（取不到名稱）", "" if ok else "（Funnel 發布失敗）"))
    else:
        log("      未連線或未安裝，只提供本機服務。")

    if already:
        # 另一份服務在跑（例如殘留的舊排程）。不搶埠，但把健檢結果印出來，
        # 至少讓人知道網址現在到底能不能用。
        log("[4/4] 健檢")
        report_when_ready(url, owns_service=False)
        return 0

    log("[4/4] 啟動服務")
    log("      載入索引與路網約需 10 秒，就緒後會印出健檢結果。")
    log()
    threading.Thread(target=report_when_ready, args=(url,), daemon=True).start()
    uvicorn.run("src.serving.api:app", host="127.0.0.1", port=PORT)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # 主控台字碼頁是 950 時，print 中文會丟 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    if "--boot" in argv:
        return boot()

    if not check_artifacts():
        return 1
    print("啟動中… 服務準備完成後會自動開啟 %s" % APP_URL)
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("src.serving.api:app", host="127.0.0.1", port=PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
