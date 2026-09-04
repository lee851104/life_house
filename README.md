# Life House 安居指數

在桃園市地圖上選一個地點，看周邊 500 公尺走路生活圈的歷史交通事故風險。

**線上 Demo：** <https://p215-2203-nb01.tail177cc6.ts.net> — 每日 08:00–20:00（台北時間）

> 展示服務跑在一台每晚斷電的實體機器上，時段外連線失敗是預期行為，不是服務故障。
> 想隨時查看請改用本機執行，見 [2. 快速開始](#2-快速開始)。

---

## 1. 這是什麼

找房時查得到價格、格局和通勤時間，卻很難知道住家周邊步行範圍過去是不是事故熱點。

原始資料是公開的，但不好用：政府資料開放平臺的 A1／A2 事故 CSV 有 51 欄、數百萬列，
**每一列是一名當事人而不是一件事故**，直接 count 會嚴重高估，檔尾還夾著非資料的註記列。
而且就算算出「附近有 N 件事故」也沒有意義——市區路多人多，事故自然多；山區路少，看起來永遠安全。

Life House 把這些資料轉成走路生活圈分析：在選定位置的 500 公尺範圍內，顯示事故集中路口、
**經曝險校正的相對安全百分位**、風險構成與每月趨勢，並可依時段（上班、下班、深夜）篩選。

https://github.com/user-attachments/assets/a27441d5-9210-4edc-8b5a-41411687d1a7

操作演示：搜尋地點後，左側是 500 公尺生活圈，底色為每 200m 一格的局部安全百分位，
圓圈數字是該路口的行人事故件數；右側是相對安全分數（例如 24% 代表比附近 24% 的區域安全）、
資料充足度與 A1／A2 事故組成。上方可切換全天／上班／下班／深夜。

> 分數是桃園市同類地區之間的相對百分位，**不是官方安全評等**，不能用來預測未來事故，
> 也不構成不動產或保險決策建議。詳見 [MODEL_CARD.md](MODEL_CARD.md)。

### 資料概況

資料範圍為桃園市、2021-07-01 至 2026-06-30。下表為 **2026-09-02** 重建的實測值；
OSM 圖資會更新、2026 年事故屬即時檔會補登，重建的數字會有小幅差異（例如步行路網 ±數 km）。

| 項目 | 數值 |
|---|---|
| 事故資料 | 231,449 件（A1 802 / A2 230,647），死亡 828 人、受傷 302,897 人 |
| 行人事故 | 11,726 件；實際計分 11,711 件（排除國道／高架／隧道 15 件），其中 A1 死亡事故 128 件 |
| 事故聚合位置 | 29,370 處（25m 網格 → 45m 貪婪聚合） |
| 步行路網 | 28,719 km |
| 比較基準 | 市界內 16,313 個取樣格、8 個曝險分層 |
| 地名搜尋 | 44,385 筆路段／POI／路口，另有 1,055,977 筆門牌 |
| 啟動載入 | 冷啟約 11 秒、熱啟約 4 秒（一次載入後查詢走記憶體） |

## 2. 快速開始

### 兩個地點比較與評分說明

搜尋或點選第一個位置，分析完成後可自訂名稱並按「設為地點 A」；
再選另一個位置按「設為地點 B」。下方比較表會並排顯示行人事故、夜間事故件數與占比、
資料充足度，以及各自在桃園同類路網分層中的安全百分位。可更新、移除地點或返回地圖查看。
比較地點暫存於本次頁面，重新整理後清空。

上方時段會同時套用兩地；更新期間隱藏舊結果，失敗可個別重試。
夜間指 18:00–翌日 06:00，僅計入與所選時段重疊的事故，占比以所選時段的行人事故為分母。
兩地可能屬於不同路網分層，百分位不是兩地風險倍數，也不是安全機率。

展開分數卡的「這個百分位怎麼算？與哪些地方比較？」可查看本次比較格數、路網分層、
嚴重度與時間權重、小樣本調整、資料充足度門檻和限制。附近百分位和桃園同類地區百分位
使用不同母體；大型車與夜間指標只呈現實際件數、占比，不再換算成額外安全分數。

建置產物（索引、路網、基準、地名共約 153 MB）**不進版控**，改以
[GitHub Release](https://github.com/ruru0109lee-cpu/lifehouse/releases/tag/v1.0.0) 提供，打包成單一 ZIP（壓縮後 71 MB）。
所以有兩條路：下載現成產物（數分鐘），或從政府開放資料自行重建（約一小時）。

### 路徑 A：下載建置產物（最快）

1. 下載 [lifehouse-data-v1.0.0.zip](https://github.com/ruru0109lee-cpu/lifehouse/releases/download/v1.0.0/lifehouse-data-v1.0.0.zip)，解壓後把裡面
   5 個檔案全部放進專案根目錄（與 `啟動.bat` 同一層）。
2. 建立環境：

   ```bash
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -r requirements.txt
   ```

3. Windows 直接雙擊 `啟動.bat`，服務就緒後會自動開啟 <http://127.0.0.1:8000>。
   其他平台：

   ```bash
   .venv/Scripts/python.exe -m uvicorn src.serving.api:app --host 127.0.0.1 --port 8000
   ```

<details>
<summary>為什麼是一個 ZIP，而不是五個附件</summary>

GitHub 上傳 Release 附件時會把非 ASCII 檔名整個丟棄——`事故索引.db` 會變成 `default.db`，
`路網.npz`／`市界.npz`／`基準.npz` 全部變成 `default.npz` 而互相衝突。ZIP 內部的檔名不受
這層處理，中文檔名得以保留，下載端也只需要抓一個檔案。
</details>

### 路徑 B：從零重建

需要 Python 3.13 以上。安裝 [uv](https://docs.astral.sh/uv/) 後：

```bash
make setup      # uv sync --all-groups
make data       # 下載 A1/A2 並篩出桃園市（下載＋解壓約 1.4 GB）
make features   # 建立索引、路網、市界、基準、地名
make test
make serve
```

Windows 沒有內建 `make`，改用等價的 `uv run`（順序不可調換，`建立路網.py` 會先下載 PBF
供後續腳本使用）。一律以 `-m` 模組形式在**專案根目錄**執行，原因見 [src/paths.py](src/paths.py)：

```bash
uv sync --all-groups
uv run python -m src.data.下載資料
uv run python -m src.data.篩選縣市 桃園市
uv run python -m src.data.建立索引
uv run python -m src.data.建立路網
uv run python -m src.data.建立市界
uv run python -m src.models.建立基準
uv run python -m src.data.建立地名   # 選配，約 10 分鐘、需約 2 GB 記憶體
uv run pytest
uv run uvicorn src.serving.api:app --host 127.0.0.1 --port 8000
```

不使用 uv 時，把 `uv run python` 換成 `.venv/Scripts/python.exe`，依賴改用
`pip install -r requirements.txt`。不建立 `地名.db` 仍可在地圖上點選並分析，
只是無法用文字搜尋地址或地標。

### 建置產物

以下皆不進版控（見 [.gitignore](.gitignore)），由上述腳本產生，或包含在 Release 的 ZIP 中：

| 檔案 | 大小 | 內容 |
|---|---|---|
| `事故索引.db` | 72 MB | 逐件事故、聚合位置、R-tree 索引、meta |
| `路網.npz` | 23 MB | 步行路網（查詢使用）＋機車路網（僅建置保留） |
| `市界.npz` | 120 KB | 桃園市界多邊形 |
| `基準.npz` | 86 KB | 曝險分層的風險分布 |
| `地名.db` | 57 MB | FTS5 地名索引 + 門牌表（選配） |

## 3. 技術與架構

| 技術 | 用途 | 為甚麼選它 |
|---|---|---|
| **Python 3.13** | 資料管線與後端 | 管線與查詢共用同一套環境與型別，不必跨語言搬資料。 |
| **FastAPI + Uvicorn** | 本機 API | 前端與 API 同源（`GET /` 直接回 `index.html`），不必處理 CORS。 |
| **NumPy + SciPy** | 空間查詢與風險計算 | `cKDTree` 半徑查詢、向量化算距離核與百分位；啟動載入一次後不再碰磁碟。 |
| **SQLite（內建）** | 事故與地名索引 | 零安裝、單檔可攜。R-tree 免全表掃描；FTS5 + trigram 讓中文子字串搜尋可用。 |
| **osmium + Shapely + PyProj** | 解析 PBF、市界、投影 | osmium 是唯一能在合理記憶體內串流掃描全台 PBF 的選擇；座標一律轉 EPSG:3826，單位公尺。 |
| **Leaflet 1.9.4** | 前端地圖 | 單一 `index.html`、**沒有 build step**，改一行存檔重整就看得到。 |
| **國土測繪中心 WMTS** | 底圖 | 臺灣通用電子地圖的門牌與巷弄細節比國際圖磚完整，且為官方公開服務。 |
| **uv + pytest + GitHub Actions** | 環境與 CI | `uv.lock` 鎖定可重現環境；每次 push 跑 `uv sync` + `pytest`。 |

> OSM 只用於**路網與地名**（離線 PBF 解析），**底圖不是 OSM**，是國土測繪中心的 WMTS 服務。

```text
政府資料開放平臺 A1/A2 事故 CSV          OpenStreetMap taiwan-latest.osm.pbf
              │                                        │
              ▼                                        │
      下載資料.py（raw/ → data/）                       │
              ▼                                        │
      篩選縣市.py 桃園市（依「發生地點」開頭篩選）        │
              ▼                                        ▼
      建立索引.py                    建立路網.py ─── 建立市界.py
              │                            │              │
              ▼                            ▼              ▼
        事故索引.db  72 MB          路網.npz 23 MB   市界.npz 120 KB
        逐件事故 + R-tree           步行網（查詢用）     桃園多邊形
              │                            │              │
              └──────────────┬─────────────┴──────────────┘
                             ▼
                     建立基準.py → 基準.npz 86 KB
                     市界內取樣、8 個曝險分層的風險分布
                             │
              建立地名.py → 地名.db 57 MB（選配，文字搜尋）
                             │
                             ▼
        ┌────────────────────────────────────────────┐
        │  api.py（FastAPI）啟動時一次載入上述全部      │
        │    核心.py        空間查詢、曝險校正、風險計算 │
        │    地名查詢.py    FTS5 中文地名排序           │
        └────────────────────────────────────────────┘
                             │  同源提供
                             ▼
              index.html（Leaflet 單頁，無框架無 build）
```

| 端點 | 用途 |
|---|---|
| `POST /api/v3/analyze` | 走路生活圈分析（座標 + 時段 → 分數、熱點、風險構成、趨勢） |
| `GET /api/v3/intersection` | 單一聚合位置底下的個別事故點 |
| `GET /api/v3/geocode` | 地址／地標／路口文字搜尋 |
| `GET /api/v3/meta` | 資料期間與筆數 |
| `GET /` | `src/serving/static/index.html` |

後端啟動時把事故索引、步行路網與比較基準全部讀進記憶體，之後每次查詢都是 NumPy 陣列運算。

## 4. 工程細節

### 4.1 先把「當事人」還原成「事故」

原始 CSV 一列是一名當事人。`建立索引.py` 以「當事者順位 = 1」的列作為事故層級屬性來 group，
並在 `篩選縣市.py` 用 `^\d{4}$` 檢查首欄以跳過檔尾註記列。少了任一步，件數就會被高估或混入註記。

### 4.2 分數必須經曝險校正，否則沒有鑑別力

```text
風險 = Σ(行人事故嚴重度 × 時間權重 × K(d)) ÷ Σ(步行路網長度 × K(d))

K(d) = 1 - (d / 500m)²          Epanechnikov 距離核
嚴重度 = 1 + 4 × 死亡人數        A2 = 1；A1 通常死亡 1 人 → 5
時間權重 = 1.0, 0.8, 0.6, 0.4, 0.2   依距資料截止日的年數
分數 = 同曝險層中「比此處更危險」的比例
```

分子與分母使用**同一個距離核**，得到的是「每公里有效路網的事故負擔」而不是原始件數。
再依 500m 圈內可步行路網長度切 8 個曝險分層比較，市區與山區才不會被放在同一把尺上。
小樣本另有收縮先驗（`walk_shrinkage_km`），避免路網極短的格點名次亂跳。

### 4.3 用市界裁切取樣，避免製造出「假的零風險」

基準若用經緯度 bbox 矩形取樣，約有**四分之三**的點會落在新北、新竹或海上——那裡有 OSM 道路
卻沒有桃園的事故資料，會被當成零事故的安全地帶，把整條分布往下拉，結果市區隨便一點都變後段班。
所以 `建立市界.py` 從 PBF 取出桃園市行政邊界（PBF 中 relation 排在 way 之後，需**分兩趟掃描**
才拿得到成員座標），基準只在市界內取樣。

### 4.4 參數是量測出來的，不是猜的

從 2 年窗改成 5 年窗，是因為 2 年資料太稀疏。以 200m 網格（25,471 個有效格）實測：

| 指標 | 2 年窗 | 5 年窗 |
|---|---|---|
| 零事故格比例 | 58.9% | **46.3%** |
| ≤2 件的格比例 | 78.2% | **66.5%** |
| 中位數件數 | 0 | **1** |

近六成格子是 0 件時，百分位排不出有意義的名次。完整結果在
[reports/五年行人事故評估.json](reports/五年行人事故評估.json)，重跑需依序執行兩步
（第二步讀取第一步產生的 `reports/基準_200m_評估.npz`）：

```bash
.venv/Scripts/python.exe -m src.models.評估步行網格      # → reports/基準_200m_評估.npz
.venv/Scripts/python.exe -m src.models.評估五年行人事故  # → reports/五年行人事故評估.json
```

### 4.5 路網與座標符合使用情境

走路分析的曝險路網排除 `motorway`（國道）與 `trunk`（多為快速公路）——行人到不了的路，
不該算進「圈內有多少路可走」，對應的事故也不該計入生活圈。

個別事故點直接使用政府公開、已去識別化資料中的**原始座標**，不另加隨機或固定偏移
（`jitter_m = 0`）；偏移會讓使用者誤以為事故發生在別的路口。

### 4.6 自建中文地理編碼，而不是接現成服務

- **為甚麼不接**：Google Places Autocomplete 的 ToS 要求結果顯示在 Google 地圖上，本專案用
  Leaflet；Nominatim 公共服務的政策明文禁止 autocomplete 這類高頻查詢；自架 Nominatim／Photon
  需要 PostGIS 或 Elasticsearch，對一個單檔 FastAPI 太重。需要的資料本來就在硬碟上。
- **索引單位選「路段」不選「門牌」**：桃園市界內光 OSM 就有 100 萬個門牌點，全丟進 FTS 會讓索引
  膨脹數百 MB，且搜「中山路」會被「中山路1號、3號…」淹沒。所以可搜尋的只有路段／POI／路口
  （44,385 筆），門牌另存在非 FTS 的 `addr` 表，用 (路段, 號) 精確查——這是真實地理編碼器的做法。
- **中文分詞**：FTS5 預設 tokenizer 不切中文，「中壢環北路」會變成單一 token 而搜不到。改用
  trigram tokenizer（字元三連組，等同子字串比對）即可，Python 3.13 內建的 SQLite 3.51 已含此
  tokenizer，無需外掛。
- **記憶體**：掃 way 需要 `flex_mem` 載入全台節點座標（約 2 GB），門牌改成邊掃邊分批寫入暫存表，
  記憶體維持平坦。

### 4.7 可重建、可測試

從下載到基準全程腳本化，資料更新後可完整重建。29 個測試涵蓋風險基本運算、行人與平交道判準、
API 參數契約，以及一個**防未來資料洩漏**的時間切分測試（`tests/test_no_leakage.py`）。

風險公式只有 [src/features/risk.py](src/features/risk.py) 一份，查詢、基準與建庫都呼叫它，
測試也刻意驗證「正式路徑真的有呼叫共用函式」而不只驗證函式本身。公式抄成兩份的代價這個專案
付過：死亡權重從 20 改成 4 時，`核心.py` 那份說明沒跟著改，說明與行為差了 5 倍，全綠的測試
一個都測不到。

```text
$ uv run ruff check .
All checks passed!

$ uv run pytest
29 passed
```

<details>
<summary><strong>4.8 Windows 上踩過的兩個坑</strong></summary>

**libosmium 開不了含中文的路徑。** 本專案的資料夾就叫「LH專案」，`建立路網.py`／`建立市界.py`／
`建立地名.py` 會全部失敗，訊息只有一句沒有指向性的 `Open failed ... unknown error`，很容易誤判
成 PBF 下載壞掉而一直重下（檔案其實是好的）。[src/data/osm_path.py](src/data/osm_path.py) 會在
開檔前把 PBF 接到一個純 ASCII 的暫存硬連結（同磁碟區，不佔空間），所以不需要把專案搬家。

**.bat 必須是純 ASCII 且 CRLF。** cmd.exe 以主控台字碼頁（繁中 Windows 為 950／Big5）逐位元組
解讀批次檔，註解裡只要出現一個 UTF-8 中文字就會讓解析器錯位，連 `rem` 都不再被認得，畫面上會
冒出看不出原因的「不是內部或外部命令」訊息。所以 `啟動.bat` 與 `開機自動啟動.bat` 都只是薄殼，
中文訊息全部由 [src/serving/launcher.py](src/serving/launcher.py) 印出；
[.gitattributes](.gitattributes) 也把 `*.bat`／`*.cmd` 釘為 `eol=crlf`，避免 clone 時被全域的
`eol=lf` 改回去而再次失效。
</details>

## 5. 線上 Demo 的部署方式

線上 Demo 沒有另建雲端環境，就是「路徑 A／B」跑起來的同一個本機服務，透過
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) 對外提供：

```text
瀏覽器 ──HTTPS──> Tailscale Funnel ──> 127.0.0.1:8000（uvicorn）
```

重點是 **uvicorn 仍然只綁 `127.0.0.1`**。Funnel 從本機環回介面取用服務，區網上的其他裝置一樣連不到
——不必為了對外展示，把一個沒有認證的 API 綁到 `0.0.0.0`（理由見 [Makefile](Makefile) 末尾註解）。

```bash
winget install Tailscale.Tailscale
tailscale login
tailscale funnel --bg 8000
```

首次啟用 Funnel 需要在 Tailscale 管理主控台按同意，CLI 會直接印出該連結。

開機後由啟動資料夾的捷徑執行 [開機自動啟動.bat](開機自動啟動.bat)，**在一個看得見的視窗裡**
把服務帶起來：依序檢查建置產物、8000 埠、Tailscale，順手重新發布一次 Funnel（刻意的冗餘，
避免 serve 設定沒撐過重開機），就緒後分別對本機與公開網址各打一次健檢——只有本機 200、
公開連不上，問題就在 Funnel 而不在服務本身，省掉一輪瞎猜。公開網址取自
`tailscale status --json`，沒有寫死；視窗訊息同時寫進 `logs/serve.log`。

安裝時請**放捷徑，不要放副本**（`Win+R` → `shell:startup`）：腳本用 `%~dp0` 定位專案，
複製一份過去會讓它在啟動資料夾裡找 `.venv` 而失敗。代價是啟動資料夾只在**登入後**執行，
展示機停在鎖定畫面時公開網址就是死的。

> **已知限制**：服務公開後隨即會收到網際網路的例行漏洞掃描（`/v2/_catalog`、`/login.action`
> 之類），這些路徑不存在，一律回 404；但 `POST /api/v3/analyze` 是公開且運算密集的端點，
> 本專案未實作速率限制，不適合長期公開曝露。

## 6. 限制與資料來源

- 來源為 [政府資料開放平臺](https://data.gov.tw/)，提供機關內政部警政署。**A1** 為當場或 24 小時
  內死亡的事故；**A2** 為受傷或超過 24 小時死亡的事故。
- 2026 年屬當年度即時檔，會隨時間往後補登，**不可直接與完整年度比較**。
- 事故資料未含車流、人流與道路設計等曝險因子。密度偏低可能只反映使用量低，不等於絕對安全。
- 目前僅支援桃園市。市界邊緣仍會受界外路網與事故資料截斷影響。
- OSM 標註可能遺漏新路或步行路網資訊。

延伸閱讀：[MODEL_CARD.md](MODEL_CARD.md)（用途、限制、已知偏誤、不適用情境、隱私）、
[configs/analysis.yaml](configs/analysis.yaml)（所有可調參數，程式中不硬編碼）。

## 7. 專案結構

依可重現 ML 專案的標準骨架擺放：設定與程式碼分離、特徵工程是可測試的純函式、
訓練（建基準）與推論（計分）同一份數學、服務層只有 FastAPI。

```text
life_house/
├── configs/analysis.yaml   所有可調參數（半徑、網格、權重、分層、收縮先驗）
├── src/
│   ├── paths.py            專案根目錄的單一定義（建置產物都放根目錄）
│   ├── data/               下載資料、篩選縣市、建立索引／路網／市界／地名
│   │                       + osm_path.py（繞過中文路徑）、validation.py（防洩漏切分）
│   ├── features/           純函式：risk.py（距離核、嚴重度、判準）、地名正規化.py
│   ├── models/             建立基準、核心.py（計分）、地名查詢、兩支評估腳本
│   └── serving/            api.py、launcher.py（.bat 的進入點）、static/index.html
├── tests/                  pytest 29 項，含 test_no_leakage.py
├── reports/                評估結果、年度彙總、資料清單、圖表
├── notebooks/              EDA 專用，不放訓練邏輯
├── Makefile                setup / data / features / train / eval / serve
├── 啟動.bat                雙擊啟動
├── 開機自動啟動.bat        登入時在可見視窗帶起線上 Demo
└── .github/workflows/ci.yml
```

> 建置腳本一律以模組形式、在**專案根目錄**執行：`python -m src.data.建立索引`。
> 直接跑 `python src/data/建立索引.py` 會失敗——`sys.path[0]` 會變成 `src/data`，
> `import src.paths` 找不到。理由見 [src/paths.py](src/paths.py)。

## 8. 授權

**程式碼**依 [MIT License](LICENSE) 授權。第三方資料與圖資各自沿用原授權，不因此改變：

| 對象 | 授權 | 說明 |
|---|---|---|
| 本專案原始碼 | [MIT](LICENSE) | 保留著作權聲明即可自由使用、修改與散布。 |
| 交通事故資料 A1／A2 | [政府資料開放授權條款－第 1 版](https://data.gov.tw/licenses) | 提供機關：內政部警政署。再利用請依該條款標示來源。 |
| 路網與地名（OSM） | [ODbL 1.0](https://www.openstreetmap.org/copyright) | 散布衍生資料庫須遵循相同方式分享（share-alike）義務。 |
| 地圖底圖 | [內政部國土測繪中心](https://maps.nlsc.gov.tw/) | 臺灣通用電子地圖／正射影像 WMTS，依該中心服務條款使用。 |
| Leaflet 1.9.4 | BSD 2-Clause | 前端地圖套件，自 CDN 載入。 |

各項授權的完整說明與免責聲明見 [LICENSE](LICENSE) 檔案末段。
