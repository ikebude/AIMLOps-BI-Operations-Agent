"""Read-Only SQL Guard enforcing semantic layer table allow-lists and blocking DDL/DML/injections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set

import yaml

DEFAULT_SEMANTIC_LAYER_PATH = Path(__file__).resolve().parents[2] / "config" / "semantic_layer.yaml"

FORBIDDEN_KEYWORDS = {
    "DROP",
    "DELETE",
    "UPDATE",
    "INSERT",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "ATTACH",
    "DETACH",
    "COPY",
    "PRAGMA",
    "GRANT",
    "REVOKE",
    "EXEC",
    "EXECUTE",
    "CALL",
    "REPLACE",
    "MERGE",
    "VACUUM",
    "LOAD",
    "INSTALL",
    "EXPORT",
    "IMPORT",
}

FORBIDDEN_SYSTEM_IDENTIFIERS = {
    "PG_SHADOW",
    "PG_AUTHID",
    "PG_USER",
    "PG_CATALOG",
    "INFORMATION_SCHEMA",
    "SQLITE_MASTER",
    "SQLITE_TEMP_MASTER",
    "SQLITE_SCHEMA",
    "DUCKDB_TABLES",
    "DUCKDB_SETTINGS",
    "DUCKDB_SECRETS",
    "READ_CSV",
    "READ_PARQUET",
    "READ_JSON",
}


@dataclass(frozen=True)
class GuardResult:
    """Result of validating a candidate SQL query."""

    safe: bool
    sanitized_sql: str
    reason: Optional[str] = None
    referenced_tables: tuple[str, ...] = ()


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
    def _strip_string_literals(sql: str) -> str:
        """Replace single-quoted string literals with placeholders for structural inspection."""
        return re.sub(r"'([^']|'')*'", "'__STR__'", sql)

    def validate(self, sql: str) -> GuardResult:
        """Validate a SQL query and return a GuardResult."""
        if not sql or not sql.strip():
            return GuardResult(safe=False, sanitized_sql="", reason="Empty SQL query.")

        raw = sql.strip()

        # 1. Reject SQL comments (-- or /* */) which are common injection vectors
        if "--" in raw or "/*" in raw or "*/" in raw:
            return GuardResult(
                safe=False,
                sanitized_sql="",
                reason="SQL comments ('--' or '/* */') are not permitted.",
            )

        # Strip trailing semicolon if it's at the very end, then forbid any internal semicolons
        if raw.endswith(";"):
            raw = raw[:-1].strip()

        masked = self._strip_string_literals(raw)
        if ";" in masked:
            return GuardResult(
                safe=False,
                sanitized_sql="",
                reason="Multi-statement SQL queries (';') are forbidden.",
            )

        # 2. Must start with SELECT or WITH
        first_token_match = re.match(r"^\s*([A-Za-z_]+)", masked)
        if not first_token_match:
            return GuardResult(
                safe=False,
                sanitized_sql="",
                reason="Query must begin with SELECT or WITH.",
            )
        first_token = first_token_match.group(1).upper()
        if first_token not in {"SELECT", "WITH"}:
            return GuardResult(
                safe=False,
                sanitized_sql="",
                reason=f"Statement must start with SELECT or WITH, got '{first_token}'.",
            )

        # 3. Check for forbidden DDL/DML/admin tokens anywhere outside string literals
        upper_tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", masked.upper())
        for token in upper_tokens:
            if token in FORBIDDEN_KEYWORDS:
                return GuardResult(
                    safe=False,
                    sanitized_sql="",
                    reason=f"Forbidden DDL/DML/administrative keyword detected: {token}.",
                )
            if token in FORBIDDEN_SYSTEM_IDENTIFIERS:
                return GuardResult(
                    safe=False,
                    sanitized_sql="",
                    reason=f"Forbidden system catalog or file function access: {token}.",
                )

        # 4. Extract CTE aliases so they aren't mistaken for physical tables
        cte_names: Set[str] = {
            m.lower()
            for m in re.findall(
                r"(?:\bWITH\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(",
                masked,
                flags=re.IGNORECASE,
            )
        }

        # 5. Extract table references after FROM and JOIN
        table_Candidates = re.findall(
            r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)",
            masked,
            flags=re.IGNORECASE,
        )

        referenced: list[str] = []
        for candidate in table_Candidates:
            clean_name = candidate.strip().lower()
            # Reject schema-qualified system tables like pg_catalog.pg_user
            if "." in clean_name:
                return GuardResult(
                    safe=False,
                    sanitized_sql="",
                    reason=f"Schema-qualified or external table reference '{candidate}' is not allowed.",
                )
            if clean_name in cte_names:
                continue
            if clean_name not in self.allowed_tables:
                return GuardResult(
                    safe=False,
                    sanitized_sql="",
                    reason=(
                        f"Table '{candidate}' is not in the semantic layer allow-list "
                        f"({sorted(self.allowed_tables)})."
                    ),
                )
            if clean_name not in referenced:
                referenced.append(clean_name)

        if not referenced:
            return GuardResult(
                safe=False,
                sanitized_sql="",
                reason="Query must reference at least one approved semantic layer table.",
            )

        # 6. Enforce LIMIT clause
        limit_match = re.search(r"\bLIMIT\s+(\d+)\b", masked, flags=re.IGNORECASE)
        sanitized = raw
        if limit_match:
            limit_val = int(limit_match.group(1))
            if limit_val > self.max_limit:
                sanitized = re.sub(
                    r"\bLIMIT\s+\d+\b",
                    f"LIMIT {self.max_limit}",
                    sanitized,
                    flags=re.IGNORECASE,
                )
        else:
            sanitized = f"{sanitized} LIMIT {self.default_limit}"

        return GuardResult(
            safe=True,
            sanitized_sql=sanitized,
            reason=None,
            referenced_tables=tuple(referenced),
        )

    def require_safe(self, sql: str) -> str:
        """Return sanitized SQL or raise SQLGuardError if unsafe."""
        res = self.validate(sql)
        if not res.safe:
            raise SQLGuardError(res.reason or "Unsafe SQL rejected by SQLGuard.")
        return res.sanitized_sql
