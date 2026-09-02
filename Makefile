.PHONY: setup data features train eval serve test

PYTHON = uv run python

setup:
	uv sync --all-groups

data:
	$(PYTHON) 下載資料.py
	$(PYTHON) 篩選縣市.py 桃園市

features:
	$(PYTHON) 建立索引.py
	$(PYTHON) 建立路網.py
	$(PYTHON) 建立市界.py
	$(PYTHON) 建立基準.py
	$(PYTHON) 建立地名.py

train:
	@echo "Life House is a deterministic risk-index system; no machine-learning training step is required."

eval: test

test:
	uv run pytest

serve:
	uv run uvicorn src.serving.api:app --host 127.0.0.1 --port 8000

# 綁 127.0.0.1 與 啟動.bat、README 一致。原本綁 0.0.0.0 會把一個沒有認證、
# 單次查詢要跑數十次 KD-tree 的 API 開放給整個區域網路——在會場 Wi-Fi 上
# 示範時不是好主意。要對外展示請改用有反向代理的部署。
