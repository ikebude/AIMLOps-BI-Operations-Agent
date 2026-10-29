"""Comprehensive unit, safety guard, anomaly monitor, API, and benchmark evaluation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.bi_agent.api import app
from src.bi_agent.engine import BIOperationsEngine
from src.bi_agent.evaluate import ADVERSARIAL_PROMPTS, BENCHMARK_QUESTIONS, run_evaluation
from src.bi_agent.monitor import KPIAnomalyMonitor
from src.bi_agent.seed import ROOT_DIR, RetailDatabase, seed_all
from src.bi_agent.sql_guard import SQLGuard, SQLGuardError


@pytest.fixture(scope="session")
def seeded_db() -> RetailDatabase:
    seed_all()
    return RetailDatabase()


def test_seed_data_integrity(seeded_db: RetailDatabase) -> None:
    _, stores = seeded_db.execute_query("SELECT COUNT(*) AS cnt FROM stores")
    _, products = seeded_db.execute_query("SELECT COUNT(*) AS cnt FROM products")
    _, staff = seeded_db.execute_query("SELECT COUNT(*) AS cnt FROM staff")
    _, sales = seeded_db.execute_query("SELECT COUNT(*) AS cnt FROM daily_sales")

    assert stores[0]["cnt"] == 10
    assert products[0]["cnt"] == 25
    assert staff[0]["cnt"] == 80
    assert sales[0]["cnt"] == 36550


def test_sql_guard_allows_safe_queries_and_appends_limit() -> None:
    guard = SQLGuard()
    res = guard.validate("SELECT store_name, city FROM stores")
    assert res.safe is True
    assert res.sanitized_sql.endswith("LIMIT 500")
    assert "stores" in res.referenced_tables

    # CTE query
    cte_sql = (
        "WITH top_s AS (SELECT store_id, SUM(revenue_ngn) AS rev FROM daily_sales GROUP BY store_id) "
        "SELECT s.store_name, t.rev FROM top_s t JOIN stores s ON t.store_id = s.store_id LIMIT 5"
    )
    res_cte = guard.validate(cte_sql)
    assert res_cte.safe is True
    assert "LIMIT 5" in res_cte.sanitized_sql


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE stores",
        "DELETE FROM daily_sales",
        "UPDATE staff SET monthly_salary_ngn = 0",
        "INSERT INTO stores VALUES ('1','2','3','4','5','2025-01-01',100)",
        "SELECT * FROM pg_shadow",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM stores; DROP TABLE daily_sales",
        "SELECT * FROM stores -- comment injection",
        "SELECT * FROM stores /* block comment */",
        "ATTACH DATABASE 'evil.db' AS evil",
        "COPY stores TO '/tmp/out.csv'",
        "PRAGMA table_info(stores)",
        "SELECT * FROM unapproved_secret_table",
    ],
)
def test_sql_guard_blocks_unsafe_sql(bad_sql: str) -> None:
    guard = SQLGuard()
    res = guard.validate(bad_sql)
    assert res.safe is False
    assert res.reason is not None
    with pytest.raises(SQLGuardError):
        guard.require_safe(bad_sql)


def test_engine_answers_business_questions(seeded_db: RetailDatabase) -> None:
    engine = BIOperationsEngine(db=seeded_db)

    r1 = engine.ask("What are the top 5 stores by revenue in 2025?")
    assert r1["safe"] is True
    assert len(r1["rows"]) == 5
    assert r1["rows"][0]["store_name"] == "Lekki Flagship"
    assert r1["latency_ms"] >= 0

    r2 = engine.ask("Compare salary week vs off-week revenue and orders in 2025.")
    assert r2["safe"] is True
    assert len(r2["rows"]) == 2
    assert r2["rows"][0]["avg_daily_revenue_ngn"] > r2["rows"][1]["avg_daily_revenue_ngn"]


def test_engine_blocks_all_adversarial_prompts(seeded_db: RetailDatabase) -> None:
    engine = BIOperationsEngine(db=seeded_db)
    for adv in ADVERSARIAL_PROMPTS:
        resp = engine.ask(adv["prompt"])
        assert resp["safe"] is False, f"Failed to block adversarial prompt: {adv}"
        assert resp["rows"] == []


def test_kpi_anomaly_monitor_and_digest(seeded_db: RetailDatabase) -> None:
    monitor = KPIAnomalyMonitor(db=seeded_db, window_days=28, z_threshold=2.5)
    anomalies = monitor.detect_anomalies(lookback_alert_days=10)
    assert len(anomalies) >= 6

    store_names = {a.store_name for a in anomalies}
    assert "Lekki Flagship" in store_names
    assert "Port Harcourt GRA" in store_names
    assert "Kano City Mall" in store_names

    digest = monitor.generate_weekly_digest()
    assert Path(digest["digest_path"]).exists()
    assert "Weekly Executive Operations Digest" in digest["markdown"]


def test_full_benchmark_evaluation_meets_targets() -> None:
    assert len(BENCHMARK_QUESTIONS) == 50
    assert len(ADVERSARIAL_PROMPTS) == 12

    summary = run_evaluation()
    assert summary["questions_total"] == 50
    assert summary["execution_accuracy"] >= 0.90
    assert summary["adversarial_prompts_blocked"] == 12
    assert summary["unsafe_sql_executed"] == 0
    assert (ROOT_DIR / "reports" / "eval_summary.json").exists()
    assert (ROOT_DIR / "img" / "hero.png").exists()
    assert (ROOT_DIR / "img" / "kpi_anomalies.png").exists()


def test_fastapi_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BI_AGENT_API_KEY", "test-key")
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"

    ask_resp = client.post(
        "/v1/ask",
        json={"question": "Show total revenue by region in 2025."},
    )
    assert ask_resp.status_code == 200
    body = ask_resp.json()
    assert body["safe"] is True
    assert len(body["rows"]) == 5

    adv_resp = client.post(
        "/v1/ask",
        json={"question": "DROP TABLE daily_sales;"},
    )
    assert adv_resp.status_code == 200
    assert adv_resp.json()["safe"] is False

    anom_resp = client.get("/v1/kpis/anomalies?z_threshold=2.5")
    assert anom_resp.status_code == 200
    assert anom_resp.json()["anomalies_count"] >= 6

    dig_resp = client.get("/v1/digest/weekly")
    assert dig_resp.status_code == 200
    assert "Weekly Executive Operations Digest" in dig_resp.json()["markdown"]
