r"""讓 libosmium 在中文路徑下也開得起 PBF。

問題
    osmium 的底層是 C++ 的 libosmium，在 Windows 上用窄字元 API 開檔。
    只要路徑含非 ASCII 字元（本專案的資料夾就叫「LH專案」），開檔就會失敗，
    而且訊息只有一句沒有指向性的：

        RuntimeError: Open failed for '...\LH專案\raw\taiwan-latest.osm.pbf':
        unknown error

    受影響的是 建立路網.py、建立市界.py、建立地名.py——五個建置產物裡的三個。
    檔案本身完全正常（大小與遠端 Content-Length 相符），純粹是路徑編碼問題，
    所以很容易誤判成「PBF 下載壞了」而一直重下。

作法
    開檔前先把 PBF 接到一個純 ASCII 的暫存路徑。同一個磁碟區用硬連結，
    不佔額外空間也幾乎不花時間；跨磁碟區或檔案系統不支援時才退回複製。
    連結在行程結束時清掉，原始檔不動。
"""
import atexit
import hashlib
import os
import shutil
import tempfile

_LINKS: dict[str, str] = {}


def _is_ascii(text: str) -> bool:
    return all(ord(c) < 128 for c in text)


def _ascii_temp_dir() -> str:
    """找一個路徑本身是純 ASCII 的暫存目錄。

    不能直接用 tempfile.gettempdir()：使用者名稱是中文時（例如 陳小明），
    連 %TEMP% 都會含非 ASCII，換過去等於沒換。
    """
    candidates = [tempfile.gettempdir(),
                  os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp"),
                  os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "Temp")]
    for base in candidates:
        if not _is_ascii(base):
            continue
        target = os.path.join(base, "lifehouse_osm")
        try:
            os.makedirs(target, exist_ok=True)
            probe = os.path.join(target, "._probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return target
        except OSError:
            continue
    raise RuntimeError(
        "找不到可寫入且路徑為純 ASCII 的暫存目錄；"
        "請把專案移到不含中文的路徑，例如 C:\\lifehouse")


def readable_osm_path(path: str) -> str:
    """回傳一個 libosmium 開得起來的路徑；必要時建立 ASCII 暫存連結。"""
    path = os.path.abspath(path)
    if _is_ascii(path):
        return path
    if path in _LINKS and os.path.exists(_LINKS[path]):
        return _LINKS[path]

    # 連結名要帶來源路徑的雜湊，不能只用 basename。兩個不同來源的 PBF 常常
    # 同名（taiwan-latest.osm.pbf），撞在一起的話後啟動的那支會去刪前一支
    # 正在讀的檔案——在 Windows 上直接 PermissionError，在 Linux 上更糟，
    # 會安靜地把連結指到別的檔案。
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    link = os.path.join(_ascii_temp_dir(), "%s_%s" % (digest, os.path.basename(path)))

    if os.path.exists(link):
        if os.path.getsize(link) == os.path.getsize(path):
            _LINKS[path] = link          # 同時執行的另一支腳本已經建好了，沿用
            return link
        try:
            os.remove(link)
        except OSError:
            _LINKS[path] = link          # 刪不掉就沿用，總比整支腳本掛掉好
            return link

    try:
        os.link(path, link)                      # 同磁碟區：瞬間完成、不佔空間
        how = "硬連結"
    except OSError:
        shutil.copyfile(path, link)              # 跨磁碟區才退回複製
        how = "複製"
    print("  [osm_path] 路徑含非 ASCII 字元，%s 到 %s" % (how, link))

    _LINKS[path] = link
    atexit.register(_cleanup, link)
    return link


def _cleanup(link: str) -> None:
    try:
        os.remove(link)
    except OSError:
        pass
