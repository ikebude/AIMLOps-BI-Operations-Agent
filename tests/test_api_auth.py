"""API key authentication: fail closed when unconfigured, 401 on bad key, /health public."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.bi_agent.api import app

PROTECTED = [
    ("post", "/v1/ask", {"json": {"question": "Show total revenue by region in 2025."}}),
    ("get", "/v1/kpis/anomalies", {}),
    ("get", "/v1/digest/weekly", {}),
]


@pytest.mark.parametrize("method,path,kw", PROTECTED)
def test_unconfigured_key_fails_closed(monkeypatch, method, path, kw) -> None:
    monkeypatch.delenv("BI_AGENT_API_KEY", raising=False)
    r = getattr(TestClient(app), method)(path, headers={"X-API-Key": "anything"}, **kw)
    assert r.status_code == 503
    monkeypatch.setenv("BI_AGENT_API_KEY", "")
    assert getattr(TestClient(app), method)(path, **kw).status_code == 503


@pytest.mark.parametrize("method,path,kw", PROTECTED)
def test_missing_or_wrong_key_is_401(monkeypatch, method, path, kw) -> None:
    monkeypatch.setenv("BI_AGENT_API_KEY", "s3cret")
    client = TestClient(app)
    assert getattr(client, method)(path, **kw).status_code == 401
    assert getattr(client, method)(path, headers={"X-API-Key": "wrong"}, **kw).status_code == 401


@pytest.mark.parametrize("method,path,kw", PROTECTED)
def test_correct_key_is_200(monkeypatch, method, path, kw) -> None:
    monkeypatch.setenv("BI_AGENT_API_KEY", "s3cret")
    r = getattr(TestClient(app), method)(path, headers={"X-API-Key": "s3cret"}, **kw)
    assert r.status_code == 200


def test_health_is_public(monkeypatch) -> None:
    monkeypatch.delenv("BI_AGENT_API_KEY", raising=False)
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "service", "backend", "daily_sales_rows"}


def test_oversized_question_is_rejected_before_work(monkeypatch) -> None:
    # Tester finding: a 5 MB question took ~6.5 s and was echoed back in full.
    monkeypatch.setenv("BI_AGENT_API_KEY", "s3cret")
    r = TestClient(app).post(
        "/v1/ask", headers={"X-API-Key": "s3cret"}, json={"question": "revenue " * 5000}
    )
    assert r.status_code == 422


def test_query_param_key_is_not_accepted(monkeypatch) -> None:
    monkeypatch.setenv("BI_AGENT_API_KEY", "s3cret")
    assert TestClient(app).get("/v1/digest/weekly?api_key=s3cret").status_code == 401
