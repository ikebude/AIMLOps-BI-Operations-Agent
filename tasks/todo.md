# Todo — bi workstream (plan = tasks/BUILD_SPEC.md)

Baseline: `pytest -q` 20 passed; evaluate 48/50, 12/12 blocked, unsafe_sql_executed=0.

- [x] Item 1 sqlglot AST guard + query time budget + tests
- [x] Item 2 API key auth (fail closed) + localhost bind
- [x] Item 3 evaluate output_root; tests stop mutating tracked files
- [x] Item 4 (held-out 14/25 = 56.0%, same misses as planner) 25 held-out questions
- [x] Item 5 remove Postgres/DuckDB claims
- [ ] Item 6 single clean eval run, README/posts, screenshot, drop docs/img
