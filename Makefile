.PHONY: seed evaluate test api app demo

seed:
	python -m src.bi_agent.seed

evaluate:
	python -m src.bi_agent.evaluate

test:
	pytest -q

api:
	uvicorn src.bi_agent.api:app --host 0.0.0.0 --port 8000 --reload

app:
	streamlit run app.py

demo: evaluate app
