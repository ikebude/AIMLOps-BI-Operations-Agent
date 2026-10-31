"""The agent says it doesn't understand instead of answering a different question."""

from __future__ import annotations

import pytest

from src.bi_agent.engine import NOT_UNDERSTOOD, BIOperationsEngine, SemanticPatternCompiler
from src.bi_agent.seed import RetailDatabase, seed_all


@pytest.fixture(scope="module")
def engine() -> BIOperationsEngine:
    seed_all()
    return BIOperationsEngine(db=RetailDatabase())


@pytest.mark.parametrize(
    "question",
    [
        "What's the weather in Abuja tomorrow?",
        "Tell me a joke",
        "Who is the CEO?",
        "Which colour should the logo be in 2025?",
    ],
)
def test_unknown_questions_are_not_guessed(engine: BIOperationsEngine, question: str) -> None:
    assert SemanticPatternCompiler().compile(question) == NOT_UNDERSTOOD
    res = engine.ask(question)
    assert res["understood"] is False
    assert res["sql"] is None
    assert res["rows"] == []
    assert "don't understand" in res["summary_text"]


@pytest.mark.parametrize(
    "question",
    [
        "How much did the whole chain sell in 2024, in naira?",
        "What did Abuja stores bring in during 2024?",
        "Total takings for Personal Care last year (2025)?",
        "Which store sold the least in 2025?",
        "What was the total revenue in Lagos in 2025?",
        "How many orders by channel in 2025?",
    ],
)
def test_business_questions_still_answered(engine: BIOperationsEngine, question: str) -> None:
    res = engine.ask(question)
    assert res["understood"] is True
    assert res["safe"] is True
    assert res["rows"], question


def test_api_returns_not_understood(engine: BIOperationsEngine, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from src.bi_agent import api

    monkeypatch.setenv("BI_AGENT_API_KEY", "k")
    client = TestClient(api.app)
    r = client.post("/v1/ask", json={"question": "Tell me a joke"}, headers={"X-API-Key": "k"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["understood"] is False and body["sql"] is None
