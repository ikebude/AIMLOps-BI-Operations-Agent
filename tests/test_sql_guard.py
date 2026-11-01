"""Regression tests for the sqlglot AST read-only SQL guard (bypasses found in audit)."""

from __future__ import annotations

import time

import pytest

from src.bi_agent.engine import BIOperationsEngine
from src.bi_agent.seed import RetailDatabase, seed_all
from src.bi_agent.sql_guard import SQLGuard


@pytest.fixture(scope="module")
def guard() -> SQLGuard:
    return SQLGuard()


@pytest.fixture(scope="module")
def db() -> RetailDatabase:
    seed_all()
    return RetailDatabase()


REJECTED = [
    "SELECT * FROM daily_sales, secret_table",
    "SELECT * FROM stores, pragma_table_info('staff')",
    "SELECT load_extension('x') FROM stores",
    "SELECT * FROM stores CROSS JOIN secret",
    "SELECT * FROM (SELECT * FROM secret)",
    "SELECT * FROM stores WHERE store_id IN (SELECT x FROM secret)",
    "SELECT store_id FROM stores UNION SELECT name FROM secret",
    "SELECT * FROM main.stores",
    'SELECT * FROM "sqlite_master"',
    "SELECT * FROM [sqlite_master]",
    "SELECT * FROM json_each('[1]'), stores",
    "SELECT readfile('/etc/passwd') FROM stores",
    "WITH stores AS (SELECT * FROM secret) SELECT * FROM stores",
    "REPLACE INTO stores VALUES (1)",
    "VACUUM",
    "SELECT * FROM stores ORDER BY store_id LIMIT -1",
    "SELECT * FROM stores; SELECT 1",
    "SELEC * FRM",
    "SELECT * FROM stores -- trailing comment",
    "SELECT * FROM stores /* c */",
    "DROP TABLE stores",
    "",
]


@pytest.mark.parametrize("sql", REJECTED)
def test_rejects(guard: SQLGuard, sql: str) -> None:
    res = guard.validate(sql)
    assert res.safe is False, res.sanitized_sql
    assert res.reason


def test_allows_recursive_cte(guard: SQLGuard) -> None:
    res = guard.validate(
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<3) SELECT i FROM n, stores"
    )
    assert res.safe, res.reason
    assert res.sanitized_sql.endswith("LIMIT 500")
    assert res.referenced_tables == ("stores",)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM stores WHERE store_name = 'a -- b'",
        "SELECT * FROM stores WHERE store_name = 'Drop Zone'",
        "SELECT * FROM stores;",
        "SELECT julianday(sale_date) FROM daily_sales LIMIT 1",
    ],
)
def test_allows(guard: SQLGuard, sql: str) -> None:
    res = guard.validate(sql)
    assert res.safe, res.reason
    assert "LIMIT" in res.sanitized_sql


def test_literal_preserved(guard: SQLGuard) -> None:
    res = guard.validate("SELECT * FROM stores WHERE store_name = 'a -- b'")
    assert "'a -- b'" in res.sanitized_sql


def test_limit_handling(guard: SQLGuard) -> None:
    assert guard.validate("SELECT * FROM stores LIMIT 5").sanitized_sql.endswith("LIMIT 5")
    assert guard.validate("SELECT * FROM stores LIMIT 5000").sanitized_sql.endswith("LIMIT 2000")
    off = guard.validate("SELECT * FROM daily_sales LIMIT 0, 99999")
    assert off.safe and "LIMIT 2000" in off.sanitized_sql and "99999" not in off.sanitized_sql
    nested = guard.validate(
        "SELECT * FROM daily_sales WHERE store_id IN (SELECT store_id FROM stores LIMIT 10)"
    )
    assert nested.safe and nested.sanitized_sql.endswith("LIMIT 500")
    expr = guard.validate("SELECT * FROM daily_sales LIMIT 99999+1")
    assert not expr.safe
    join = guard.validate("SELECT * FROM daily_sales, stores")
    assert join.safe and set(join.referenced_tables) == {"daily_sales", "stores"}


def test_nested_limit_bypass_is_bounded_at_execution(db: RetailDatabase) -> None:
    engine = BIOperationsEngine(db=db)
    out = engine.ask(
        "SELECT * FROM daily_sales WHERE store_id IN (SELECT store_id FROM stores LIMIT 10)"
    )
    assert out["safe"] is True
    assert len(out["rows"]) <= 500


def test_runaway_recursive_query_times_out() -> None:
    seed_all()
    engine = BIOperationsEngine(db=RetailDatabase(query_timeout_ms=300))
    t0 = time.monotonic()
    out = engine.ask(
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n) SELECT COUNT(*) FROM n, stores"
    )
    assert time.monotonic() - t0 < 3
    assert out["safe"] is False
    assert "interrupted" in out["guard_reason"]


def test_cte_name_does_not_shadow_tables_outside_its_scope() -> None:
    # Tester finding: a CTE defined inside a subquery used to whitelist the same name everywhere,
    # letting the outer query read a real table that is not in the allow-list.
    guard = SQLGuard(allowed_tables=["stores"])
    res = guard.validate(
        "SELECT staff.* FROM stores, (WITH staff AS (SELECT 1) SELECT * FROM staff), staff"
    )
    assert not res.safe and "staff" in (res.reason or "")
    # In-scope CTE references (incl. forward and UNION-wide) are still fine.
    assert guard.validate("WITH s AS (SELECT * FROM stores) SELECT * FROM s UNION SELECT * FROM s").safe
    assert guard.validate("WITH a AS (SELECT * FROM b), b AS (SELECT * FROM stores) SELECT * FROM a").safe


@pytest.mark.parametrize("sql", ["SELECT 1 FROM stores;;", "SELECT 1 FROM stores; ;"])
def test_extra_semicolons_rejected(guard: SQLGuard, sql: str) -> None:
    assert not guard.validate(sql).safe


def test_single_trailing_semicolon_allowed(guard: SQLGuard) -> None:
    assert guard.validate("SELECT * FROM stores;").safe


def test_huge_or_deeply_nested_sql_rejected_fast(guard: SQLGuard) -> None:
    t0 = time.monotonic()
    assert not guard.validate("SELECT " + "1+" * 200_000 + "1 FROM stores").safe
    assert not guard.validate("SELECT " + "(" * 5000 + "1" + ")" * 5000 + " FROM stores").safe
    assert time.monotonic() - t0 < 2


def test_huge_string_values_are_capped_at_execution(db: RetailDatabase) -> None:
    # Audit 2026-11-01: printf() is an allowed function, and a huge width made SQLite build a
    # 100 MB string per row; the progress-handler timeout never fires inside one call, so ten
    # rows took ~10 s and ~1.3 GB of memory. The connection now caps string/blob length.
    engine = BIOperationsEngine(db=db)
    t0 = time.monotonic()
    out = engine.ask("SELECT printf('%100000000d', 1) AS p FROM stores")
    assert time.monotonic() - t0 < 3
    # SQLite either returns NULL for the oversized value or aborts with "string or blob too big".
    assert all(r["p"] is None for r in out["rows"])
    out = engine.ask("SELECT replace(printf('%500000d', 1), ' ', 'xxxx') AS p FROM stores")
    assert out["safe"] is False and "too big" in out["guard_reason"]
