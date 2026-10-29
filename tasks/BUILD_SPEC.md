# BUILD_SPEC — bi workstream (AIMLOps-BI-Operations-Agent)

Branch: `ccr-7d21c86c-pnbh8t`. Owner: Precious "Chidi" Ikebude (they/them).
Read the scratchpad `RULES.md` first. This repo has no CLAUDE.md, so RULES.md is the only rulebook.

## 1. Context and goal

This repo is a synthetic-data NL-to-SQL "BI agent": a deterministic regex compiler (`engine.py`), a SQL guard, a MAD anomaly
monitor and a 50-question benchmark. The README sells an **AST** SQL guard, but `sql_guard.py` is regex and keyword
matching. Real bypasses exist (reproduced below). The goal is to make the README true: replace the guard with a
sqlglot-based one, measure accuracy honestly (including a new held-out set), have every number come from one evaluation
run, remove claims we can't support, and lock down the API.

### Baseline (planner ran this on 2026-10-29 in a clean clone, Python 3.13 venv at
`/tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/venv`)

```
$V/python -m pytest -q            -> 20 passed, 1 warning in 8.36s
$V/python -m src.bi_agent.evaluate -> Execution Accuracy 48/50 (96.0%); Adversarial Blocked 12/12 (unsafe_sql_executed=0);
                                      Latency median=16.375 ms, p95=37.478 ms; Anomalies Flagged 7
```
`V=/tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/venv/bin`. Use this venv; it already has
`sqlglot 30.25.0`, fastapi 0.162.0, streamlit 1.66.1, pandas 3.1.0 and pytest 9.2.0. System python3 has none of these.

Latency depends on the machine (README says 48.4/202.2 ms, the dirty JSON says 23.99/42.8, `img/hero.png` says 50.35,
this run says 16.4/37.5). That is why every latency claim must come from one regenerated JSON.

**Working tree state:** 6 tracked files are modified and should not be committed as they are: `docs/img/hero.png`,
`docs/img/kpi_anomalies.png`, `img/hero.png`, `img/kpi_anomalies.png`, `reports/eval_summary.json`,
`reports/metrics.json`. They come from an interrupted run. **Before starting, run
`git checkout -- docs/img img reports/eval_summary.json reports/metrics.json`** so every item starts from HEAD. Only Item 6
regenerates them, from one clean run.

Root cause of the dirty tree: `tests/test_bi_agent.py::test_full_benchmark_evaluation_meets_targets` calls
`run_evaluation()`, which overwrites tracked reports and images on every `pytest`. Item 3 fixes this.

### Reproduced guard bypasses (current `src/bi_agent/sql_guard.py`)
| Input | Current result | Impact |
|---|---|---|
| `SELECT * FROM daily_sales, secret_table` | safe=True | The comma join skips the table allow-list (the regex only looks after FROM/JOIN, see `sql_guard.py:170-174`) |
| `SELECT * FROM stores, pragma_table_info('staff')` | safe=True, **70 rows** | Schema leak through a table-valued function |
| `SELECT * FROM daily_sales LIMIT 0, 99999` | safe=True, **36,550 rows** | LIMIT bypass (the regex `LIMIT\s+(\d+)\b` at `:208` reads the 0) |
| `SELECT * FROM daily_sales WHERE store_id IN (SELECT store_id FROM stores LIMIT 10)` | safe=True, **36,550 rows** | The inner LIMIT satisfies the regex, so the outer query is unbounded |
| `SELECT * FROM daily_sales LIMIT 99999+1` | rewritten to `LIMIT 2000+1` | Malformed bound |
| `SELECT load_extension('x') FROM stores` | safe=True | Unknown function allowed. The SQLite connection refuses it ("not authorized"), but the guard should not depend on that |
| `WITH RECURSIVE n(i) AS (...) SELECT i FROM n, stores` | **rejected** ("Table 'n' not in allow-list") | False positive: the CTE regex at `:160-167` misses `RECURSIVE` |

The raw user text reaches the guard: `engine.py:75-104` (`_detect_adversarial_or_raw_sql`) returns any input that starts
with SELECT/WITH unchanged, so `POST /v1/ask {"question": "SELECT ..."}` hits all of these through the API.

## 2. Work items (in this order; each leaves `pytest` green)

### Item 1 — sqlglot-based read-only SQL guard (HIGH)
**Why:** README:3, 20, 48-53, 69-71 and POST_X.md/POST_LINKEDIN.md claim AST parsing, but `sql_guard.py:95-227` uses
regexes, and the bypasses in the table above are real.

**Files:** `requirements.txt` (add `sqlglot==30.25.0`; it is on PyPI, verified 2026-10-29, `requires_python>=3.9`,
latest is 30.26.0, and we pin the installed version), `src/bi_agent/sql_guard.py` (rewrite `validate`),
`src/bi_agent/seed.py` (query time budget), `src/bi_agent/engine.py` (**only** `BIOperationsEngine.ask`: catch DB errors),
`.env.example`, `tests/test_sql_guard.py` (new).

**Design.** Keep the public API exactly: `GuardResult(safe, sanitized_sql, reason, referenced_tables)`, `SQLGuardError`,
`SQLGuard(allowed_tables=None, default_limit=500, max_limit=2000, semantic_layer_path=None)`, `validate()`,
`require_safe()`, `DEFAULT_SEMANTIC_LAYER_PATH`. Delete the regex helpers and `FORBIDDEN_KEYWORDS`. You may keep
`FORBIDDEN_SYSTEM_IDENTIFIERS` as a frozenset that also rejects those names when they appear as CTE names, but the
allow-list already covers them. The planner prototyped this logic and it passed every case in the table above and below,
with 48/50 and 12/12 unchanged:

```python
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

DIALECT = "sqlite"
FORBIDDEN_NODES = (exp.DML, exp.DDL, exp.Command, exp.Pragma, exp.Attach, exp.Detach, exp.Copy)
# Functions sqlglot does not model (they parse as exp.Anonymous) and that are harmless in SQLite.
SAFE_ANONYMOUS_FUNCTIONS = frozenset({"julianday", "printf", "total", "time", "datetime", "unixepoch"})

def validate(self, sql: str) -> GuardResult:
    raw = (sql or "").strip()
    if not raw: -> reject "Empty SQL query."
    tokens = sqlglot.tokenize(raw, read=DIALECT)              # TokenError -> reject "Unparseable SQL"
    if any(t.comments for t in tokens): -> reject "SQL comments ('--' or '/* */') are not permitted."
        # NB: tokenizer-based, so 'a -- b' inside a string literal is allowed (correct); real comments are not.
    stmts = [s for s in sqlglot.parse(raw, read=DIALECT) if s is not None]   # ParseError/TokenError -> reject
    if len(stmts) != 1: -> reject "Multi-statement SQL queries (';') are forbidden."  (trailing ';' alone is fine)
    tree = stmts[0]
    if not isinstance(tree, exp.Query): -> reject f"Only a single SELECT/WITH query is allowed, got {type(tree).__name__}."
        # exp.Query covers Select (incl. WITH ...) and Union/Intersect/Except.
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES): reject f"Forbidden statement type: {type(node).__name__}."
        if isinstance(node, exp.Anonymous) and node.name.lower() not in SAFE_ANONYMOUS_FUNCTIONS:
            reject f"Function '{node.name}' is not permitted."   # load_extension, readfile, zeroblob, pragma_* ...
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    referenced = []
    for t in tree.find_all(exp.Table):                         # covers FROM, every JOIN, comma joins, subqueries, CTE bodies, UNION arms
        if not isinstance(t.this, exp.Identifier): reject "Table-valued functions are not permitted."
        if t.args.get("db") or t.args.get("catalog"): reject f"Schema-qualified table '{t.sql(DIALECT)}' is not allowed."
        name = t.name.lower()
        if name in cte_names: continue
        if name not in self.allowed_tables: reject f"Table '{t.name}' is not in the semantic layer allow-list ({sorted(self.allowed_tables)})."
        append unique name to referenced
    if not referenced: reject "Query must reference at least one approved semantic layer table."
    limit = tree.args.get("limit")                            # OUTER limit only
    if limit is None: tree = tree.limit(self.default_limit)
    else:
        e = limit.expression
        if not (isinstance(e, exp.Literal) and e.is_int): reject "LIMIT must be a non-negative integer literal."
        if int(e.this) > self.max_limit: tree = tree.limit(self.max_limit)
    return GuardResult(True, tree.sql(dialect=DIALECT), None, tuple(referenced))
```
Notes the planner checked:
- `tree.limit(n)` works on `Select` and `Union` and replaces an existing limit. `LIMIT 0, 99999` parses as limit 99999
  with offset 0 and becomes `LIMIT 2000 OFFSET 0`.
- Round-tripping through `tree.sql(dialect="sqlite")` gives identical results for all 100 benchmark SQL strings (50
  generated and 50 expected). It changes text (`FROM daily_sales d` becomes `FROM daily_sales AS d`), so
  `reports/*.json` will change.
- `REPLACE INTO ...` / `VACUUM` make sqlglot log a "Falling back to parsing as a 'Command'" warning before we reject. To
  keep logs clean, silence it with `logging.getLogger("sqlglot").setLevel(logging.ERROR)` at module import.
- `LIMIT -1` is a unary negation, not an int literal, so it is rejected. That is intended.
- The CTE-name exemption is safe: CTE bodies are still walked, so `WITH stores AS (SELECT * FROM secret) ...` is rejected.

**Query time budget (needed now that `WITH RECURSIVE` is allowed).** Without it,
`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n) SELECT COUNT(*) FROM n, stores` never finishes. In
`seed.py:RetailDatabase.execute_query`, SQLite branch, before `cur.execute`:
```python
deadline = time.monotonic() + self.query_timeout_ms / 1000.0
conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
```
`query_timeout_ms` is an `__init__` kwarg with default `int(os.getenv("BI_AGENT_QUERY_TIMEOUT_MS", "5000"))`. The planner
verified this raises `sqlite3.OperationalError: interrupted` at the deadline. Leave the optional DuckDB branch alone; it
is not exercised (see Item 5). In `engine.py` `BIOperationsEngine.ask` only, wrap `self.db.execute_query(sanitized_sql)`
in `try/except sqlite3.Error as exc:` and return the same shape as the guard-reject branch, with `safe=False` and
`guard_reason=f"Query execution failed: {exc}"`. Add `BI_AGENT_QUERY_TIMEOUT_MS=5000` to `.env.example`.

**Tests (new `tests/test_sql_guard.py`, parametrized):**
- Must reject: every bypass in the table above except the recursive one. Also `SELECT * FROM stores CROSS JOIN secret`,
  `SELECT * FROM (SELECT * FROM secret)`, `SELECT * FROM stores WHERE store_id IN (SELECT x FROM secret)`,
  `SELECT store_id FROM stores UNION SELECT name FROM secret`, `SELECT * FROM main.stores`, `SELECT * FROM "sqlite_master"`,
  `SELECT * FROM [sqlite_master]`, `SELECT * FROM json_each('[1]'), stores`, `SELECT readfile('/etc/passwd') FROM stores`,
  `WITH stores AS (SELECT * FROM secret) SELECT * FROM stores`, `REPLACE INTO stores VALUES (1)`, `VACUUM`,
  `SELECT * FROM stores ORDER BY store_id LIMIT -1`, `SELECT * FROM stores; SELECT 1`, and garbage text `SELEC * FRM`.
- Must allow, with the sanitized SQL asserted: `WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<3) SELECT i FROM n, stores`
  (it executes and ends in `LIMIT 500`), `SELECT * FROM stores WHERE store_name = 'a -- b'` (a literal is not a comment),
  `SELECT * FROM stores WHERE store_name = 'Drop Zone'`, `SELECT * FROM stores;` (trailing semicolon),
  `SELECT julianday(sale_date) FROM daily_sales LIMIT 1`.
- LIMIT: `LIMIT 5` stays 5; `LIMIT 5000` becomes `LIMIT 2000`; `LIMIT 0, 99999` becomes `LIMIT 2000`; the nested-limit
  query ends in an outer `LIMIT 500`; `referenced_tables` is correct for a comma join of two allowed tables.
- Execution: run the inner-LIMIT bypass through `BIOperationsEngine.ask` and assert `len(rows) <= 500`.
- Timeout: `RetailDatabase(query_timeout_ms=300)` plus the infinite recursive COUNT, through `engine.ask`, returns
  `safe=False` with "interrupted" in `guard_reason` in under 3 s.
- The existing `tests/test_bi_agent.py` must pass unchanged, including the 13 bad-SQL cases and the 12 ADV prompts.

**Done when:** pytest is green, `evaluate` still gives 48/50 and 12/12 with `unsafe_sql_executed=0`, and every case above
behaves as listed.

### Item 2 — API key auth (fail closed) and localhost bind (LOW)
**Why:** `api.py:53-93` has no auth, and the `Makefile` `api:` target and README:115 bind `0.0.0.0`.
**Files:** `src/bi_agent/api.py`, `Makefile`, `.env.example`, `tests/test_bi_agent.py` (only `test_fastapi_endpoints`, to
add the header), `tests/test_api_auth.py` (new).
**Design:**
- Env var `BI_AGENT_API_KEY`. Dependency `require_api_key` uses `fastapi.security.APIKeyHeader(name="X-API-Key", auto_error=False)`.
  Read the env var **on each request** (so tests can monkeypatch it). If the env var is unset or empty, return **503**
  `{"detail": "API key not configured on server"}`. This is the fail-closed case, and the key value is never logged. If
  the header is missing or wrong, return **401**. Compare with `secrets.compare_digest`.
- Apply it to `/v1/ask`, `/v1/kpis/anomalies` and `/v1/digest/weekly` (use `dependencies=[Depends(require_api_key)]`).
  `/health` stays public but must not leak anything new.
- Makefile: `HOST ?= 127.0.0.1` and `PORT ?= 8000`. The `api:` target becomes
  `uvicorn src.bi_agent.api:app --host $(HOST) --port $(PORT) --reload`. The `app:` target becomes
  `streamlit run app.py --server.address $(HOST)`.
- `.env.example`: add `BI_AGENT_API_KEY=` with the comment `# required for the API; generate with: python -c "import secrets;print(secrets.token_urlsafe(32))"`.
- Tests: missing env gives 503; wrong key gives 401; correct key gives 200; `/health` without a key gives 200. In
  `test_fastapi_endpoints`, monkeypatch the env and send the header. Do not weaken any existing assertion.
**Done when:** all of the above pass, and `grep -n "0.0.0.0" Makefile README.md` finds nothing except a documented
`HOST=0.0.0.0` override example.

### Item 3 — Evaluation writes to a chosen directory; tests stop mutating tracked files (MED, supports Items 4 and 6)
**Why:** `evaluate.py:20-22, 454-455, 542-544, 653-655, 832-835` write to the repo's `reports/`, `img/` and `docs/img/`
unconditionally, and `tests/test_bi_agent.py:119-130` runs it. `charts` in the JSON holds absolute machine paths
(seen: `/tmp/.../bi_base/img/hero.png`). The failure `reason` at `evaluate.py:704` is a hard-coded string stamped on
every failure.
**Files:** `src/bi_agent/evaluate.py`, `tests/test_bi_agent.py` (only `test_full_benchmark_evaluation_meets_targets`).
**Design:** `run_evaluation(seed: int = 42, output_root: Optional[Path] = None)`. `root = output_root or ROOT_DIR`, then
`reports_dir = root/"reports"`, `img_dir = root/"img"`. Pass `img_dir` into
`generate_charts(..., img_dir: Path)`. Write the digest with `monitor.generate_weekly_digest(output_path=reports_dir/"weekly_digest.md")`
(that kwarg exists at `monitor.py:176`). **Stop writing `docs/img/`** (Item 6 removes it). Store chart paths relative to
`root` (`"img/hero.png"`). Derive the failure reason: if the response was not safe, use `f"Blocked by guard: {guard_reason}"`;
otherwise use `f"Result mismatch: expected {len(exp_rows)} rows x {len(exp_cols)} cols, got {len(rows)} x {len(cols)}"`.
For Q41 and Q50, also keep a short `analysis` string explaining the known reason (the existing multi-hop ratio/covariance
text is accurate for those two). The test calls `run_evaluation(output_root=tmp_path)` and asserts the files exist under
`tmp_path`.
**Done when:** `git status` is clean after `pytest`, and the CLI (`python -m src.bi_agent.evaluate`) still writes to the
repo root.

### Item 4 — 25 held-out reworded questions, reported separately (MED)
**Why:** the 50 benchmark questions are the exact phrasings the regex templates in `engine.py:235-760` were written for,
so 96% is an in-sample number. Report generalisation honestly.
**Files:** `src/bi_agent/evaluate.py` (new `HELDOUT_QUESTIONS` constant, scoring and JSON block), `tests/test_bi_agent.py`
or a new `tests/test_heldout.py`.
**Hard rule:** do **not** edit `SemanticPatternCompiler` / `engine.py` templates in this workstream. If you tune the
compiler to these questions, they stop being held out. Copy the 25 questions below verbatim. The planner wrote them
without changing the compiler.
**Scoring:** add a `ordered: bool` field. If `ordered` is true, use the existing `_rows_match`. Otherwise sort both row
lists by a normalised tuple (floats `round(x, 2)` then `str`, everything else `str`) and then apply `_rows_match`. The
match stays strict: the column count must be equal, so an answer with an extra descriptive column counts as a miss. Say
so in the README. JSON: add a `heldout` block with `questions_total`, `questions_correct`, `execution_accuracy_pct`,
`domain_breakdown` and `question_results` (same fields as the main ones, plus `ordered`). Keep it out of the headline
`execution_accuracy`. Also print it on the CLI.
**Planner's measurement with the Item 1 guard: 14/25 (56.0%).** Misses were H09, H11, H12, H13, H15, H18, H19, H21, H22,
H23 and H25. If your number differs, report yours and explain why. Do not chase 14.
**Test:** `len(HELDOUT_QUESTIONS) == 25`, every expected SQL passes `SQLGuard` and returns at least 1 row, and the JSON
fields exist. **No accuracy threshold** (a threshold would invite tuning).

| id | domain | question | expected_sql | ordered |
|---|---|---|---|---|
| H01 | Revenue & Growth | How much did the whole chain sell in 2024, in naira? | `SELECT ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024'` | F |
| H02 | Revenue & Growth | Give me 2024 sales broken down month by month. | `SELECT STRFTIME('%Y-%m', sale_date) AS m, ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024' GROUP BY m ORDER BY m` | T |
| H03 | Revenue & Growth | What did Abuja stores bring in during 2024? | `SELECT ROUND(SUM(d.revenue_ngn), 2) FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE s.city = 'Abuja' AND STRFTIME('%Y', d.sale_date) = '2024'` | F |
| H04 | Revenue & Growth | Total takings for Personal Care last year (2025)? | `SELECT ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE category = 'Personal Care' AND STRFTIME('%Y', sale_date) = '2025'` | F |
| H05 | Revenue & Growth | How much revenue came through Web Delivery in 2024? | `SELECT ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE channel = 'Web Delivery' AND STRFTIME('%Y', sale_date) = '2024'` | F |
| H06 | Revenue & Growth | Sales in November 2024? | `SELECT ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE STRFTIME('%Y-%m', sale_date) = '2024-11'` | F |
| H07 | Margins & Profitability | What was our gross profit in 2024? | `SELECT ROUND(SUM(gross_margin_ngn), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024'` | F |
| H08 | Margins & Profitability | Margin % for Beverages & Dairy in 2024? | `SELECT ROUND(100.0 * SUM(gross_margin_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) FROM daily_sales WHERE category = 'Beverages & Dairy' AND STRFTIME('%Y', sale_date) = '2024'` | F |
| H09 | Margins & Profitability | Which product category was least profitable by margin percentage in 2025? | `SELECT category, ROUND(100.0 * SUM(gross_margin_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS p FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2025' GROUP BY category ORDER BY p ASC LIMIT 1` | T |
| H10 | Margins & Profitability | How much did goods cost us in 2024 (COGS)? | `SELECT ROUND(SUM(cogs_ngn), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024'` | F |
| H11 | Margins & Profitability | List gross margin for each store in 2025, highest first. | `SELECT s.store_name, ROUND(SUM(d.gross_margin_ngn), 2) AS gm FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE STRFTIME('%Y', d.sale_date) = '2025' GROUP BY s.store_name ORDER BY gm DESC` | T |
| H12 | Store & Regional Rankings | Name the three best-selling stores of 2024 by revenue. | `SELECT s.store_name, ROUND(SUM(d.revenue_ngn), 2) AS r FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE STRFTIME('%Y', d.sale_date) = '2024' GROUP BY s.store_name ORDER BY r DESC LIMIT 3` | T |
| H13 | Store & Regional Rankings | Which store sold the least in 2025? | `SELECT s.store_name, ROUND(SUM(d.revenue_ngn), 2) AS r FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE STRFTIME('%Y', d.sale_date) = '2025' GROUP BY s.store_name ORDER BY r ASC LIMIT 1` | T |
| H14 | Store & Regional Rankings | Revenue per region for 2024 please. | `SELECT s.region, ROUND(SUM(d.revenue_ngn), 2) FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE STRFTIME('%Y', d.sale_date) = '2024' GROUP BY s.region` | F |
| H15 | Store & Regional Rankings | How many shops do we have in each city? | `SELECT city, COUNT(*) FROM stores GROUP BY city` | F |
| H16 | Store & Regional Rankings | How many orders did Port Harcourt stores process in 2024? | `SELECT SUM(d.orders_count) FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE s.city = 'Port Harcourt' AND STRFTIME('%Y', d.sale_date) = '2024'` | F |
| H17 | Seasonality & Salary-Week | How much did we sell in salary weeks during 2024? | `SELECT ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE is_salary_week = 1 AND STRFTIME('%Y', sale_date) = '2024'` | F |
| H18 | Seasonality & Salary-Week | What was the strongest sales month of 2024? | `SELECT STRFTIME('%Y-%m', sale_date) AS m, ROUND(SUM(revenue_ngn), 2) AS r FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024' GROUP BY m ORDER BY r DESC LIMIT 1` | T |
| H19 | Seasonality & Salary-Week | What was the typical basket value in 2024? | `SELECT ROUND(SUM(revenue_ngn) / NULLIF(SUM(orders_count), 0), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024'` | F |
| H20 | Seasonality & Salary-Week | Q1 2025 total revenue? | `SELECT ROUND(SUM(revenue_ngn), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2025' AND CAST(STRFTIME('%m', sale_date) AS INTEGER) BETWEEN 1 AND 3` | F |
| H21 | Returns, Staff & Catalog | What share of 2024 revenue was refunded? | `SELECT ROUND(100.0 * SUM(returns_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2024'` | F |
| H22 | Returns, Staff & Catalog | How many employees are there in each store, and what is each store's monthly wage bill? | `SELECT s.store_name, COUNT(st.staff_id), ROUND(SUM(st.monthly_salary_ngn), 2) FROM staff st JOIN stores s ON st.store_id = s.store_id GROUP BY s.store_name` | F |
| H23 | Returns, Staff & Catalog | What are the three cheapest products we stock? | `SELECT product_name, unit_price_ngn FROM products ORDER BY unit_price_ngn ASC LIMIT 3` | T |
| H24 | Returns, Staff & Catalog | Which city has the highest return rate in 2025? | `SELECT s.city, ROUND(100.0 * SUM(d.returns_ngn) / NULLIF(SUM(d.revenue_ngn), 0), 2) AS rr FROM daily_sales d JOIN stores s ON d.store_id = s.store_id WHERE STRFTIME('%Y', d.sale_date) = '2025' GROUP BY s.city ORDER BY rr DESC LIMIT 1` | T |
| H25 | Returns, Staff & Catalog | Average monthly salary by job role? | `SELECT role, ROUND(AVG(monthly_salary_ngn), 2) FROM staff GROUP BY role` | F |

**Done when:** the CLI prints a `Held-out` line, and the JSON `heldout` block exists with honest numbers.

### Item 5 — Remove the unsupported backend claims (MED)
**Why:** README:13 and README:59 (`DuckDB / SQLite / Postgres`), POST_LINKEDIN.md:15 ("before hitting DuckDB/SQLite")
and the engine prompt string. There is no Postgres code anywhere. `duckdb` is not in `requirements.txt`, not installed in
CI, and the DuckDB branch in `seed.py:14-18, 336-345, 375-382` is never tested. Every benchmark run reports
`backend_engine: "sqlite"`.
**Decision (planner):** do **not** build a Postgres or DuckDB adapter. Change README:59 to `SQLite (read-only URI mode)`.
Add a one-line note: "An optional DuckDB code path exists in `seed.py` when `duckdb` is installed; it is not exercised by
CI. Postgres is on the roadmap." README:13 lists SMEs' data sources as a problem statement, so it can stay. Do not touch
the code paths.
**Done when:** `grep -rn -i "postgres" README.md POST_*.md` returns only the roadmap sentence.

### Item 6 — One clean evaluation run, README/posts quote the JSON, images de-duplicated, real UI screenshot (MED + LOW)
**Why:** the latency numbers disagree (README:3, 93-94; POST_X.md:17; POST_LINKEDIN.md:16 say 48.4/202.2 ms, but the JSON
and hero.png differ). README:5-7 captions `img/hero.png` (a matplotlib chart) as "Executive Chat & Semantic Guardrail" /
"BI Operations Agent UI". `img/` and `docs/img/` are duplicates; nothing references `docs/img/` (planner grepped `*.md`
and `*.py`). README:3 says "Nigerian retail chain transactions" without saying synthetic. README:112 says "(20 tests)".
**Files:** `reports/eval_summary.json`, `reports/metrics.json`, `reports/weekly_digest.md`, `img/hero.png`,
`img/kpi_anomalies.png`, new `img/ui_streamlit.png`, `git rm -r docs/img`, `README.md`, `POST_X.md`, `POST_LINKEDIN.md`,
`app.py` (metric-card CSS only, see below), and optionally `scripts/screenshot_ui.js` (new).
**Steps:**
1. `git checkout -- docs/img img reports` (if not done already). Then run `$V/python -m src.bi_agent.evaluate` **once**
   and commit all regenerated artifacts from that single run. Do not run it again before committing (latency changes each run).
2. Screenshot: start `$V/streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8599` in
   the background, wait for HTTP 200, then run Node Playwright with `NODE_PATH=$(npm root -g)` (playwright 1.63.0 is
   installed globally and chromium is preinstalled; **never run `playwright install`**). Use a 1280x900 viewport, wait for
   `h1`, wait 3 s, and save to `img/ui_streamlit.png`. The planner tested this flow successfully. A telemetry call to
   `data.streamlit.io` is refused by the proxy, which is harmless; you may add `--browser.gatherUsageStats false`. Kill the
   server afterwards.
3. `app.py:25-31`: the metric cards hard-code `background-color: #121316` while text follows the theme, so in the light
   theme the values are dark-on-near-black and unreadable (planner screenshot). Change it to
   `background-color: rgba(127,127,127,0.08)`, keeping the border. Also format the gross margin card as
   `f"₦{k['gm']/1e9:.2f}B"`; it currently truncates to "₦1701.…" at 1280 px. No other `app.py` changes.
4. README: the table header becomes `| Streamlit UI (img/ui_streamlit.png) | KPI anomalies (img/kpi_anomalies.png) |` with
   the real screenshot, and `img/hero.png` gets the caption "Benchmark summary chart". The one-liner says **synthetic**
   Nigerian retail data. Every metric (accuracy, domain rows, adversarial, unsafe, median/p95 latency, held-out, GMV and
   order counts, test count) is copied from the new JSON or the pytest output, with a sentence that latency is
   machine-dependent, measured on the run that produced `reports/eval_summary.json`. Add a "Held-out generalisation" row
   and a paragraph: "The 50 benchmark questions are the phrasings the deterministic compiler's templates were written
   for (in-sample). On 25 reworded held-out questions it scores X/25 (strict execution match)." Add a short "Security
   model" section: sqlglot AST guard (what it enforces), the API key, 127.0.0.1 default bind, and the query time budget.
   The run command becomes `uvicorn src.bi_agent.api:app --host 127.0.0.1 --port 8000` with
   `export BI_AGENT_API_KEY=...`, and the curl example must send the `X-API-Key` header.
5. POST_X.md and POST_LINKEDIN.md: the same numbers from the same JSON, the held-out number mentioned, and the AST claim
   is now true. Do not add new claims, testimonials or links.
**Done when:** every number in README/POST_* is in the TEST_PLAN claim table and matches the JSON, `docs/img/` is gone,
and no file references it.

## 3. Things NOT to do
- Do not edit `SemanticPatternCompiler` or its templates (`engine.py:66-762`) or `BENCHMARK_QUESTIONS` /
  `ADVERSARIAL_PROMPTS` / `_rows_match`. Don't change seed data or `seed=42`.
- Do not skip, xfail or loosen any existing test assertion. Do not lower `>= 0.90` or `== 12`.
- Do not add Postgres or DuckDB adapters, an LLM call or new providers. Do not touch the `_call_external_llm` code
  (out of scope; it is guarded by Item 1 anyway).
- Do not run `playwright install`. Do not commit `.env`, `data/*.db` or a real API key.
- Do not change the LICENSE, owner name or links. No invented metrics. Do not unpin or upgrade other deps.
- The planner did not commit the 6 dirty files; restore them with `git checkout` and do not commit them as they are.

## 4. Commit plan (author `ikebude <precious.ikebude@outlook.com>`, trailer lines per RULES.md §6)
1. `feat(guard): sqlglot AST read-only SQL guard + query time budget; bypass regression tests` (Item 1)
2. `feat(api): X-API-Key auth (fail closed) and 127.0.0.1 default bind` (Item 2)
3. `refactor(eval): output_root for evaluation; tests no longer mutate tracked reports` (Item 3)
4. `feat(eval): 25 held-out reworded questions reported separately` (Item 4)
5. `docs: drop unsupported Postgres/DuckDB claims` (Item 5)
6. `docs: regenerate reports from one run; README/posts quote eval_summary.json; real UI screenshot; drop docs/img` (Item 6)

Push after each one: `git push -u origin ccr-7d21c86c-pnbh8t` (retry 2/4/8/16 s).

## 5. Final verification (paste the outputs into the tasks/todo.md Review section)
```bash
V=/tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/venv/bin
cd /home/user/AIMLOps-BI-Operations-Agent
$V/python -m pip install -r requirements.txt --dry-run 2>&1 | tail -2   # resolves with sqlglot==30.25.0
$V/python -m pytest -q                                                   # expect 20 + new tests, all pass
git status --porcelain                                                   # expect empty after pytest
$V/python -c "import json;d=json.load(open('reports/eval_summary.json'));print(d['questions_correct'],d['adversarial_prompts_blocked'],d['unsafe_sql_executed'],d['median_latency_ms'],d['p95_latency_ms'],d['heldout']['questions_correct'],d['charts'])"
grep -rn "docs/img\|0\.0\.0\.0\|48\.4\|202\.2\|Postgres" README.md POST_*.md Makefile src app.py
```

## Builder notes
- Item 5: README line 13 (SME problem statement naming PostgreSQL as a customer data source) was left as the spec allows, so `grep -i postgres` also matches it besides the roadmap sentence. The `engine.py` LLM prompt string ("DuckDB/SQLite database", inside `_call_external_llm`) was not touched because the spec's scope fence forbids editing that code.
