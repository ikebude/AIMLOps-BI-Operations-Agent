# Todo — bi workstream (plan = tasks/BUILD_SPEC.md)

Baseline: `pytest -q` 20 passed; evaluate 48/50, 12/12 blocked, unsafe_sql_executed=0.

- [x] Item 1 sqlglot AST guard + query time budget + tests
- [x] Item 2 API key auth (fail closed) + localhost bind
- [x] Item 3 evaluate output_root; tests stop mutating tracked files
- [x] Item 4 (held-out 14/25 = 56.0%, same misses as planner) 25 held-out questions
- [x] Item 5 remove Postgres/DuckDB claims
- [x] Item 6 single clean eval run, README/posts, screenshot, drop docs/img

## Review

```
$ pip install -r requirements.txt --dry-run | tail -2
Requirement already satisfied: six>=1.6 in /tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/venv/lib/python3.13/site-packages (from python-dateutil>=2.9.0->pandas>=2.0.0->-r requirements.txt (line 2)) (1.17.0)
Requirement already satisfied: tzdata in /tmp/claude-0/-home-user/e6ff3af4-b759-5a42-ae4a-d7f12c188d23/scratchpad/venv/lib/python3.13/site-packages (from python-dateutil>=2.9.0->pandas>=2.0.0->-r requirements.txt (line 2)) (2026.6)
$ pytest -q
63 passed, 1 warning in 10.90s
$ git status --porcelain (after pytest; only the intentional Item 6 changes should appear)
13
$ eval_summary.json fields
48 12 0 17.803 36.647 14 {'hero_png': 'img/hero.png', 'kpi_anomalies_png': 'img/kpi_anomalies.png'}
$ grep -rn "docs/img|0.0.0.0|48.4|202.2|Postgres" README.md POST_*.md Makefile src app.py
README.md:70:Storage backend: SQLite (read-only URI mode). An optional DuckDB code path exists in `seed.py` when `duckdb` is installed; it is not exercised by CI. Postgres is on the roadmap.
```

Baseline 20 tests -> 63 tests (51 after Item 1, 61 after Item 2, 63 after Item 4). In-sample 48/50 unchanged; held-out 14/25 (H09,H11,H12,H13,H15,H18,H19,H21,H22,H23,H25 missed, identical to planner). Builder notes in tasks/BUILD_SPEC.md.

## Tester review (2026-10-30)
- [x] TEST_PLAN A–G executed from a fresh clone; results in `tasks/TEST_PLAN.md` (G table + Tester findings)
- [x] Fixed: CTE-scope allow-list bypass (HIGH), parser DoS/RecursionError, `;;`, `/v1/ask` length cap, orders card truncation
- [x] pytest 63 → 70 passed, 0 skipped; evaluate 48/50, 12/12, unsafe 0, held-out 14/25 (unchanged, deterministic)
- Verdict: PASS-WITH-NOTES
