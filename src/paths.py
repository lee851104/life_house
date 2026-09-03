"""專案根目錄的單一定義。

搬進 src/ 之前，13 支腳本各自寫了一份

    BASE = os.path.dirname(os.path.abspath(__file__))

來定位根目錄的建置產物（事故索引.db、路網.npz、市界.npz、基準.npz、地名.db）
與 configs/、data/、raw/、縣市/、reports/。腳本原本就躺在根目錄，這樣寫是對的。

搬進 src/data、src/models、src/serving 之後 __file__ 指的是模組所在的子目錄，
那 13 份寫法會一起失效，而且失效方式是安靜的：路徑照樣算得出來，只是指到
不存在的位置，症狀變成「找不到 事故索引.db」——看不出真正的原因是層數差了兩層。

所以根目錄只在這裡算一次，其餘模組一律 `from src.paths import ROOT as BASE`，
下游的 os.path.join(BASE, ...) 全部維持原狀。

這也代表建置腳本必須以模組形式從專案根目錄執行：

    uv run python -m src.data.建立索引        ✓
    uv run python src/data/建立索引.py        ✗  sys.path[0] 會是 src/data，
                                                 import src.paths 找不到

型別是 str 而非 Path，為的是讓既有的 os.path.join / os.path.relpath 呼叫
一個字都不用改。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
