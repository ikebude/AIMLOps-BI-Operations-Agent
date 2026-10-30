"""FastAPI service exposing NL-to-SQL, KPI Anomaly Monitor, and Weekly Digest endpoints."""

from __future__ import annotations

import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from src.bi_agent.engine import BIOperationsEngine
from src.bi_agent.monitor import KPIAnomalyMonitor
from src.bi_agent.seed import RetailDatabase, seed_all
from src.bi_agent.sql_guard import MAX_SQL_CHARS

app = FastAPI(
    title="BI Operations Agent API",
    description="Governed NL-to-SQL over a semantic layer, rolling 28-day MAD KPI anomaly detection, and automated weekly executive digests.",
    version="1.0.0",
)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: Optional[str] = Depends(_api_key_header)) -> None:
    """Fail closed: reject every request unless BI_AGENT_API_KEY is set and matches X-API-Key."""
    expected = os.getenv("BI_AGENT_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="API key not configured on server")
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


_db: Optional[RetailDatabase] = None
_engine: Optional[BIOperationsEngine] = None
_monitor: Optional[KPIAnomalyMonitor] = None


def _get_components() -> tuple[RetailDatabase, BIOperationsEngine, KPIAnomalyMonitor]:
    global _db, _engine, _monitor
    if _db is None:
        seed_all()
        _db = RetailDatabase()
        _engine = BIOperationsEngine(db=_db)
        _monitor = KPIAnomalyMonitor(db=_db)
    return _db, _engine, _monitor  # type: ignore[return-value]


class AskRequest(BaseModel):
    question: str = Field(
        ..., min_length=2, max_length=MAX_SQL_CHARS, description="Plain-English business question"
    )


class AskResponse(BaseModel):
    question: str
    sql: str
    rows: List[Dict[str, Any]]
    columns: List[str]
    summary_text: str
    chart_spec: Dict[str, Any]
    latency_ms: float
    safe: bool
    guard_reason: Optional[str] = None
    provider: str
    cost_usd: float


@app.get("/health")
def health_check() -> Dict[str, Any]:
    db, _, _ = _get_components()
    _, rows = db.execute_query("SELECT COUNT(*) AS cnt FROM daily_sales")
    return {
        "status": "ok",
        "service": "bi-operations-agent",
        "backend": db.backend_name,
        "daily_sales_rows": rows[0]["cnt"] if rows else 0,
    }


@app.post("/v1/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
def ask_question(payload: AskRequest) -> Dict[str, Any]:
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")
    _, engine, _ = _get_components()
    return engine.ask(payload.question)


@app.get("/v1/kpis/anomalies", dependencies=[Depends(require_api_key)])
def get_kpi_anomalies(
    z_threshold: float = Query(2.5, ge=1.5, le=10.0),
    lookback_days: int = Query(10, ge=1, le=28),
) -> Dict[str, Any]:
    db, _, _ = _get_components()
    mon = KPIAnomalyMonitor(db=db, window_days=28, z_threshold=z_threshold)
    anomalies = mon.detect_anomalies(lookback_alert_days=lookback_days)
    return {
        "window_days": 28,
        "z_threshold": z_threshold,
        "lookback_days": lookback_days,
        "anomalies_count": len(anomalies),
        "anomalies": [a.to_dict() for a in anomalies],
    }


@app.get("/v1/digest/weekly", dependencies=[Depends(require_api_key)])
def get_weekly_digest() -> Dict[str, Any]:
    _, _, monitor = _get_components()
    return monitor.generate_weekly_digest()
