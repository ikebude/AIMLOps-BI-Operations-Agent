HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: seed evaluate test api app demo

seed:
	python -m src.bi_agent.seed

evaluate:
	python -m src.bi_agent.evaluate

test:
	pytest -q

api:
	uvicorn src.bi_agent.api:app --host $(HOST) --port $(PORT) --reload

app:
	streamlit run app.py --server.address $(HOST)

demo: evaluate app
