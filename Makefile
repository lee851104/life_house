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
	uv run uvicorn src.serving.api:app --host 0.0.0.0 --port 8000
