# TEST_PLAN — bi workstream (independent tester checks)

Don't trust the builder's report. Run everything from a fresh clone of the branch so local dirt can't mask problems:
```bash
V=/tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/venv/bin
T=/tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/bi_test
rm -rf $T && git clone -q -b ccr-7d21c86c-pnbh8t /home/user/AIMLOps-BI-Operations-Agent $T && cd $T
```
Baseline before the build (planner, 2026-10-29): pytest 20 passed; evaluate 48/50, 12/12, unsafe 0; median 16.4 ms / p95 37.5 ms on this machine.

## A. Standard gates
| # | Command | Expected |
|---|---|---|
| A1 | `$V/python -m pytest -q` | All pass. Count = 20 + new tests, and no test skipped or xfailed (`-rs` shows none). |
| A2 | `git status --porcelain` right after A1 | Empty. Tests must not rewrite `reports/` or `img/`. |
| A3 | `$V/python -m src.bi_agent.evaluate` | 48/50, 12/12, unsafe_sql_executed=0, plus a Held-out line (planner measured 14/25). |
| A4 | `grep -n sqlglot requirements.txt` | `sqlglot==30.25.0` |
| A5 | `git diff origin/main --stat -- src/bi_agent/engine.py` | Only the `ask()` try/except. No template or compiler changes (the held-out set must stay held out). |
| A6 | `git diff origin/main -- src/bi_agent/evaluate.py \| grep -E '^[-+].*(expected_sql\|"question")' \| grep -v '"H[0-9]'` | No edits to Q01–Q50 or ADV01–12. |
| A7 | `git log --format='%an <%ae>%n%b' origin/main..HEAD` | Author `ikebude <precious.ikebude@outlook.com>`. Trailer lines are present, and no model name appears anywhere else. |
| A8 | `git ls-files \| grep -E '\.env$\|\.db$\|\.duckdb$'` | Empty. `.env.example` lists `BI_AGENT_API_KEY` and `BI_AGENT_QUERY_TIMEOUT_MS`. |

## B. Adversarial: SQL guard (run through `SQLGuard().validate` **and** through `BIOperationsEngine().ask`, and assert `rows == []` or `len(rows) <= 500`)
Must be **blocked**:
- Comma join: `SELECT * FROM daily_sales, secret_table`; `SELECT name FROM stores, sqlite_master`
- Table-valued and unknown functions: `SELECT * FROM stores, pragma_table_info('staff')`, `SELECT * FROM json_each('[1]'), stores`, `SELECT load_extension('x') FROM stores`, `SELECT readfile('/etc/passwd') FROM stores`, `SELECT zeroblob(1000000000) FROM stores`, `SELECT randomblob(999999999) FROM stores`
- Nesting: `SELECT * FROM (SELECT * FROM secret)`, `... WHERE x IN (SELECT y FROM secret)`, `SELECT 1 FROM stores UNION SELECT name FROM sqlite_master`, `WITH stores AS (SELECT * FROM secret) SELECT * FROM stores`, `WITH a AS (SELECT * FROM sqlite_master) SELECT * FROM a, stores`
- Quoting and qualification: `"sqlite_master"`, `[sqlite_master]`, `` `sqlite_master` ``, `main.stores`, `temp.stores`, `information_schema.tables`. (Note: `SELECT * FROM "STORES"` is a case variant of an allowed table and should be **allowed**.)
- Statements: `PRAGMA table_info(stores)`, `ATTACH DATABASE 'x' AS y`, `DETACH y`, `VACUUM`, `REPLACE INTO stores VALUES (1)`, `INSERT INTO stores SELECT * FROM stores`, `CREATE TEMP TABLE t AS SELECT 1`, `SELECT 1 FROM stores; DROP TABLE stores`, `SELECT 1 FROM stores;;`, `EXPLAIN SELECT * FROM stores`
- Comments: `SELECT * FROM stores -- x`, `SELECT * FROM stores /* x */`, `SELECT * FROM stores #x` (SQLite has no `#` comments, so this one only needs to be rejected or harmless)
- Garbage, empty, whitespace-only, and a 1 MB string (must not hang; it should be rejected or bounded in under 5 s)
- The 12 ADV prompts in `evaluate.py` and the 13 cases in `tests/test_bi_agent.py:55-69`

Must be **allowed or bounded**:
- `SELECT * FROM daily_sales LIMIT 0, 99999` → ≤ 2000 rows. `SELECT * FROM daily_sales WHERE store_id IN (SELECT store_id FROM stores LIMIT 10)` → ≤ 500 rows (it was 36,550 before). `SELECT * FROM daily_sales LIMIT 99999+1` → rejected.
- `SELECT * FROM stores UNION ALL SELECT * FROM stores` gets an outer LIMIT. `SELECT * FROM stores LIMIT 1 OFFSET 5` is unchanged.
- `WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<3) SELECT i FROM n, stores` → allowed and executes.
- `SELECT * FROM stores WHERE store_name = 'a -- b; DROP'` → allowed (it is a string literal).
- Every Q01–Q50 expected_sql passes the guard.

DoS: `WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n) SELECT COUNT(*) FROM n, stores` through `engine.ask` and `POST /v1/ask` → `safe=False` within about `BI_AGENT_QUERY_TIMEOUT_MS` plus 1 s, no 500 error, and the server keeps serving. Also try a big cartesian product: `SELECT COUNT(*) FROM daily_sales a, daily_sales b`.

## C. Adversarial: API
Start `BI_AGENT_API_KEY=k1 $V/uvicorn src.bi_agent.api:app --host 127.0.0.1 --port 8765`.
| Case | Expected |
|---|---|
| `make -n api` / `make -n app` | `--host 127.0.0.1` / `--server.address 127.0.0.1`; `HOST=0.0.0.0 make -n api` overrides it |
| `ss -ltnp \| grep 8765` | Bound to 127.0.0.1 only |
| `GET /health` with no key | 200 |
| `POST /v1/ask`, `GET /v1/kpis/anomalies`, `GET /v1/digest/weekly` with no header / wrong key / `X-API-Key: ` empty / key with trailing space / correct key in lowercase header name | 401 / 401 / 401 / 401 / 200 (header names are case-insensitive) |
| Server started **without** `BI_AGENT_API_KEY` (or set to the empty string), correct-looking header sent | 503 on protected routes (fail closed), and never 200 |
| `?api_key=k1` query param only | 401 (header only) |
| Raw SQL through the API: `{"question":"SELECT * FROM daily_sales, staff"}` (both allowed: should succeed with ≤500 rows), `{"question":"SELECT * FROM stores, pragma_table_info('staff')"}` (blocked) | As stated |
| Server log during these calls | The API key value never appears |
| `/docs` / `/openapi.json` | Shows the security scheme; does not expose the key |

## D. Evaluation integrity
- `reports/eval_summary.json` and `reports/metrics.json` are byte-identical, or metrics is a documented subset. `charts` paths are relative (`img/...`), and no `/tmp` or `/home` string appears in any committed report: `grep -rn "/tmp/\|/home/" reports/`.
- `error_analysis[].reason` differs per failure type. Q41 and Q50 are listed.
- `heldout.questions_total == 25`. Recompute held-out accuracy independently: copy the 25 questions out of `tasks/BUILD_SPEC.md` (not out of the code), score them with your own script, and confirm the JSON number. Check the 25 in the code match the spec verbatim.
- Check that `img/hero.png` shows the same accuracy and median latency text as the JSON (view the image).
- Determinism: run `evaluate` twice into `output_root=tmp`; everything except latency fields is identical.

## E. UI screenshots (Streamlit)
Start `$V/streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8599 --browser.gatherUsageStats false`. Take screenshots with Node Playwright (`NODE_PATH=$(npm root -g)`, never `playwright install`) at **390x844 and 1280x900, in light and dark** (`colorScheme: 'light'|'dark'` on the context; Streamlit follows the system theme). Check:
- The metric-card values are readable in both themes (this was the light-theme defect), and the gross margin card is not truncated at 1280.
- At 390 px there is no horizontal page scroll (`document.documentElement.scrollWidth <= innerWidth`). A wide SQL code block may scroll inside itself.
- Typing `DROP TABLE stores; -- (Test Read-Only SQL Guard)` and clicking Run Query shows a "Blocked by SQLGuard" message.
- `img/ui_streamlit.png` in the repo is a real Streamlit screenshot (it shows the "BI Operations Agent" title and tabs), not a matplotlib chart.

## F. Docs and links
- Broken-link scan: every relative link and image in `README.md`, `POST_*.md` and `data/README.md` resolves to a tracked file (`git ls-files`). `docs/img/` no longer exists, and nothing references it.
- `grep -rn -i "postgres" README.md POST_*.md` → only the roadmap line. `grep -rn "0\.0\.0\.0" README.md Makefile` → only an explicit override example.
- `grep -rn "48\.4\|202\.2\|50\.35" README.md POST_*.md` → nothing, unless the new JSON happens to say so.
- The README says "synthetic" in the one-liner and in the Data section, and describes the 50-question set as in-sample.

## G. README claim → evidence table (tester fills the "Verified" column)
| Claim (README/POST_*) | Evidence (file → field) | Verified |
|---|---|---|
| Execution accuracy X/50 (Y%) | `reports/eval_summary.json` → `questions_correct`, `execution_accuracy_pct` || ✅ 48/50, 96.0 (fresh run = committed JSON, latency fields aside) |
| Per-domain accuracies (5 rows) | → `domain_breakdown[]` || ✅ 12/12, 10/10, 10/10, 8/9, 8/9 |
| Held-out X/25 | → `heldout.questions_correct`, `heldout.execution_accuracy_pct` || ✅ 14/25, 56.0. Spec table = code (25/25 verbatim). Own scorer: 14/25 strict, 17/25 if extra columns allowed (H11, H13, H22) |
| 12/12 adversarial blocked, 0 unsafe executed | → `adversarial_prompts_blocked`, `unsafe_sql_executed` || ✅ 12 / 0 |
| Median / p95 latency ms (with a machine-dependent note) | → `median_latency_ms`, `p95_latency_ms` || ✅ 17.803 / 36.647 → 17.8 / 36.6 (tester re-run: 16.4 / 34.4) |
| ₦ GMV, gross margin, margin %, orders, 36,550 rows | → `dataset_summary.*` || ✅ 10,149,566,063.03 / 2.90B / 28.61 / 2,145,814 / 36,550 |
| 10 stores / 6 cities / 80 staff / 25 SKUs / 731 days | `dataset_summary` + `tests/test_bi_agent.py::test_seed_data_integrity` || ✅ dataset_summary + test passes |
| Anomalies flagged N (if quoted) | → `kpi_monitor_summary.anomalies_flagged` || n/a (not quoted; JSON says 7) |
| "N tests" | `pytest -q` output || ✅ after tester fixes: 70 passed (README/POST_X updated from 63) |
| "AST guard" (SELECT/WITH only, allow-list incl. comma joins/subqueries/CTEs, LIMIT enforced) | `src/bi_agent/sql_guard.py` (sqlglot) + `tests/test_sql_guard.py` || ✅ after fix c3c2280 (CTE-scope bypass, see findings) |
| Q41/Q50 are the misses | → `error_analysis[].id` || ✅ |
| API requires X-API-Key; binds 127.0.0.1 by default | `src/bi_agent/api.py`, `Makefile`, section C results || ✅ section C all as expected |
| Backend = SQLite | → `backend_engine` || ✅ |

## Tester findings
Tester run 2026-10-30, fresh clone + working tree. 114 own guard attacks + the plan's list, via `SQLGuard.validate` and `engine.ask`.

**Fixed by tester (commits on this branch):**
1. **HIGH, allow-list bypass via CTE scope** (`src/bi_agent/sql_guard.py`, the old `cte_names` skip in the table loop). Any CTE name, wherever defined, was exempt everywhere in the statement. `SELECT staff.* FROM stores, (WITH staff AS (SELECT 1) SELECT * FROM staff), staff` with allow-list `[stores]` returned real `staff` rows (names, salaries). Against the shipped allow-list, `sqlite_temp_schema` / `sqlite_sequence` / `sqlite_dbpage` passed the guard the same way (empty or absent here, so no data leaked). Fix: CTE names resolve only inside the query that owns the `WITH` (`_visible_cte_names`). Regression test added. → c3c2280
2. **MEDIUM, parser DoS / 500.** A 400 KB `1+1+…` took 9 s to validate, and 5,000 nested parentheses raised an uncaught `RecursionError` (500 from `/v1/ask`). Fix: 20,000-char cap, plus `RecursionError` → reject. → c3c2280
3. **LOW, `SELECT 1 FROM stores;;` accepted** (sqlglot drops empty statements). Now only one trailing `;` is allowed. → c3c2280
4. **LOW, `/v1/ask` had no question length cap.** A 5 MB question took 6.5 s and was echoed back in full. `max_length=20,000` → 422. → 9b93c73
5. **UI**: the "2025 Completed Orders" card showed `1,164,…` at 1280. It now shows `1.16M`, and `img/ui_streamlit.png` was re-captured. → 68b0ca1

**Open (not fixed, low):**
- `sql_guard.py` lets `sqlite_version()` through (sqlglot models it, so it isn't `Anonymous`). It only discloses the SQLite version. Consider a function allow-list instead of the Anonymous-only deny.
- Harmless accepted forms: `LIMIT 1 OFFSET -1` (SQLite treats it as 0), `LIMIT 0x7fffffff` (clamped to 2000), `NOT INDEXED`, `stores AS sqlite_master`.
- `engine.py` compiler: unrecognised input (`VACUUM`, `garbage`, `''`, full-width `ＳＥＬＥＣＴ`) silently runs the default "total revenue" query and reports `safe=True`. Nothing unsafe runs, but a user may misread the answer. Consider an "I don't understand" response.
- `seed.py:398`: `with sqlite3.connect(...)` commits but does not close the connection. Small fd churn per query.
- `README.md:17` mentions PostgreSQL in the problem statement (it's about users' data, not a capability claim). Left as is.
- Test plan wording: the UI error says "Blocked by Read-Only SQL Guard", not "Blocked by SQLGuard". Verified with that text.
