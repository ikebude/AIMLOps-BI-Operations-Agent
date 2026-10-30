"""Read-Only SQL Guard enforcing semantic layer table allow-lists and blocking DDL/DML/injections."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType

DEFAULT_SEMANTIC_LAYER_PATH = Path(__file__).resolve().parents[2] / "config" / "semantic_layer.yaml"

DIALECT = "sqlite"

# Longest benchmark query is ~650 chars; anything far beyond this is abuse and slow/recursive to parse.
MAX_SQL_CHARS = 20_000

# sqlglot logs a "Falling back to parsing as a 'Command'" warning for REPLACE/VACUUM; we reject those anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

FORBIDDEN_NODES = (exp.DML, exp.DDL, exp.Command, exp.Pragma, exp.Attach, exp.Detach, exp.Copy)

# Functions sqlglot does not model (they parse as exp.Anonymous) and that are harmless in SQLite.
SAFE_ANONYMOUS_FUNCTIONS = frozenset({"julianday", "printf", "total", "time", "datetime", "unixepoch"})

FORBIDDEN_SYSTEM_IDENTIFIERS = frozenset(
    {
        "pg_shadow",
        "pg_authid",
        "pg_user",
        "pg_catalog",
        "information_schema",
        "sqlite_master",
        "sqlite_temp_master",
        "sqlite_schema",
        "duckdb_tables",
        "duckdb_settings",
        "duckdb_secrets",
        "read_csv",
        "read_parquet",
        "read_json",
    }
)


@dataclass(frozen=True)
class GuardResult:
    """Result of validating a candidate SQL query."""

    safe: bool
    sanitized_sql: str
    reason: Optional[str] = None
    referenced_tables: tuple[str, ...] = ()


def _visible_cte_names(node: exp.Expression) -> Set[str]:
    """CTE names in scope at `node`: those defined by a WITH on any enclosing query."""
    names: Set[str] = set()
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Query):
            with_ = parent.args.get("with_") or parent.args.get("with")
            if with_ is not None:
                names |= {c.alias_or_name.lower() for c in with_.expressions}
        parent = parent.parent
    return names


class SQLGuardError(ValueError):
    """Raised when a query violates read-only SQL guard rules."""


class SQLGuard:
    """Validates and sanitizes SQL queries against the governed semantic layer."""

    def __init__(
        self,
        allowed_tables: Optional[Iterable[str]] = None,
        default_limit: int = 500,
        max_limit: int = 2000,
        semantic_layer_path: Optional[Path] = None,
    ) -> None:
        if allowed_tables is None:
            path = semantic_layer_path or DEFAULT_SEMANTIC_LAYER_PATH
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            tables_dict = cfg.get("tables", {})
            allowed_tables = list(tables_dict.keys())
            default_limit = int(cfg.get("default_limit", default_limit))
            max_limit = int(cfg.get("max_limit", max_limit))

        self.allowed_tables: Set[str] = {t.lower() for t in allowed_tables}
        self.default_limit = default_limit
        self.max_limit = max_limit

    @staticmethod
    def _reject(reason: str) -> GuardResult:
        return GuardResult(safe=False, sanitized_sql="", reason=reason)

    def validate(self, sql: str) -> GuardResult:
        """Parse the SQL with sqlglot and validate the AST; return a GuardResult."""
        raw = (sql or "").strip()
        if not raw:
            return self._reject("Empty SQL query.")
        if len(raw) > MAX_SQL_CHARS:
            return self._reject(f"SQL longer than {MAX_SQL_CHARS} characters is not permitted.")

        try:
            tokens = sqlglot.tokenize(raw, read=DIALECT)
            if any(t.comments for t in tokens):
                return self._reject("SQL comments ('--' or '/* */') are not permitted.")
            semis = [i for i, t in enumerate(tokens) if t.token_type == TokenType.SEMICOLON]
            if semis and semis != [len(tokens) - 1]:
                return self._reject("Multi-statement SQL queries (';') are forbidden.")
            stmts = [s for s in sqlglot.parse(raw, read=DIALECT) if s is not None]
        except (ParseError, TokenError) as exc:
            return self._reject(f"Unparseable SQL: {str(exc).splitlines()[0][:160]}")
        except RecursionError:
            return self._reject("SQL is nested too deeply to validate.")

        if len(stmts) != 1:
            return self._reject("Multi-statement SQL queries (';') are forbidden.")
        tree = stmts[0]
        if not isinstance(tree, exp.Query):
            return self._reject(
                f"Only a single SELECT/WITH query is allowed, got {type(tree).__name__}."
            )

        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                return self._reject(f"Forbidden statement type: {type(node).__name__}.")
            if isinstance(node, exp.Anonymous) and node.name.lower() not in SAFE_ANONYMOUS_FUNCTIONS:
                return self._reject(f"Function '{node.name}' is not permitted.")

        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for name in cte_names & FORBIDDEN_SYSTEM_IDENTIFIERS:
            return self._reject(f"Forbidden system catalog name used as CTE: {name}.")

        referenced: list[str] = []
        for t in tree.find_all(exp.Table):
            if not isinstance(t.this, exp.Identifier):
                return self._reject("Table-valued functions are not permitted.")
            if t.args.get("db") or t.args.get("catalog"):
                return self._reject(
                    f"Schema-qualified table '{t.sql(DIALECT)}' is not allowed."
                )
            name = t.name.lower()
            # A CTE name only shadows a table inside the WITH's own query, not elsewhere.
            if name in _visible_cte_names(t):
                continue
            if name not in self.allowed_tables:
                return self._reject(
                    f"Table '{t.name}' is not in the semantic layer allow-list "
                    f"({sorted(self.allowed_tables)})."
                )
            if name not in referenced:
                referenced.append(name)

        if not referenced:
            return self._reject("Query must reference at least one approved semantic layer table.")

        # Only the outer LIMIT bounds the result set; nested LIMITs do not count.
        limit = tree.args.get("limit")
        if limit is None:
            tree = tree.limit(self.default_limit)
        else:
            bound = limit.expression
            if not (isinstance(bound, exp.Literal) and bound.is_int):
                return self._reject("LIMIT must be a non-negative integer literal.")
            if int(bound.this) > self.max_limit:
                tree = tree.limit(self.max_limit)

        return GuardResult(
            safe=True,
            sanitized_sql=tree.sql(dialect=DIALECT),
            reason=None,
            referenced_tables=tuple(referenced),
        )

    def require_safe(self, sql: str) -> str:
        """Return sanitized SQL or raise SQLGuardError if unsafe."""
        res = self.validate(sql)
        if not res.safe:
            raise SQLGuardError(res.reason or "Unsafe SQL rejected by SQLGuard.")
        return res.sanitized_sql
