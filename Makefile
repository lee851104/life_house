.PHONY: setup data features train eval serve test

PYTHON = uv run python

# 建置腳本一律以模組形式從專案根目錄執行。搬進 src/ 之後不能再直接跑檔案
# （sys.path[0] 會變成 src/data，import src.paths 會失敗），原因見 src/paths.py。

setup:
	uv sync --all-groups

data:
	$(PYTHON) -m src.data.下載資料
	$(PYTHON) -m src.data.篩選縣市 桃園市

features:
	$(PYTHON) -m src.data.建立索引
	$(PYTHON) -m src.data.建立路網
	$(PYTHON) -m src.data.建立市界
	$(PYTHON) -m src.models.建立基準
	$(PYTHON) -m src.data.建立地名

train:
	@echo "Life House is a deterministic risk-index system; no machine-learning training step is required."
	@echo "The closest equivalent is the percentile baseline: make features (src/models/建立基準.py)."

eval:
	$(PYTHON) -m src.models.評估步行網格
	$(PYTHON) -m src.models.評估五年行人事故
	uv run pytest

test:
	uv run pytest

serve:
	uv run uvicorn src.serving.api:app --host 127.0.0.1 --port 8000

# 綁 127.0.0.1 與 啟動.bat、README 一致。原本綁 0.0.0.0 會把一個沒有認證、
# 單次查詢要跑數十次 KD-tree 的 API 開放給整個區域網路——在會場 Wi-Fi 上
# 示範時不是好主意。要對外展示請改用有反向代理的部署（README「遠端展示」）。
