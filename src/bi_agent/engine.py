"""NL-to-SQL Engine with Provider Adapter (Claude/OpenAI/Ollama) and Deterministic Semantic Compiler."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.bi_agent.seed import RetailDatabase
from src.bi_agent.sql_guard import DEFAULT_SEMANTIC_LAYER_PATH, SQLGuard


CITY_NAMES = ["Lagos", "Abuja", "Port Harcourt", "Ibadan", "Kano", "Enugu"]
REGION_NAMES = ["South-West", "North-Central", "South-South", "North-West", "South-East"]
CATEGORY_NAMES = [
    "Groceries & Staples",
    "Beverages & Dairy",
    "Personal Care",
    "Household & Cleaning",
    "Fresh & Bakery",
]
SALES_CHANNELS = ["In-Store POS", "WhatsApp Commerce", "Web Delivery"]
STORE_FORMATS = ["Flagship", "Hypermarket", "Mall", "Express"]
STORE_NAMES = [
    "Lekki Flagship",
    "Victoria Island Hyper",
    "Ikeja City Mall",
    "Wuse II Mall",
    "Gwarinpa Express",
    "Port Harcourt GRA",
    "Trans-Amadi Express",
    "Ibadan Dugbe Express",
    "Kano City Mall",
    "Enugu Polo Park",
]
STAFF_ROLE_NAMES = [
    "Store Manager",
    "Inventory Lead",
    "Cashier",
    "Sales Associate",
    "Fulfillment Rider",
]

MONTH_MAP = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


# Returned by the compiler when a question names no metric it knows. The agent then says it does not
# understand instead of silently answering a different question (it used to default to total revenue).
NOT_UNDERSTOOD = "-- NOT_UNDERSTOOD"

# Plain-English ways people ask about revenue without saying "revenue" or "sales".
REVENUE_PHRASES = (
    "revenue", "sales", "sell", "sold", "takings", "bring in", "brought in", "earn", "made",
    "turnover", "income", "gmv", "naira", "perform", "how much",
)

NOT_UNDERSTOOD_MESSAGE = (
    "I don't understand that question yet, so I haven't guessed. I can answer questions about revenue, "
    "gross margin, orders, basket size, units sold, returns, COGS, staff and payroll, products and stores, "
    "by store, city, region, channel, category or period. For example: "
    "\"What was the total revenue in Lagos in 2025?\" or \"Orders by channel in Q4 2025\"."
)


class SemanticPatternCompiler:
    """Deterministic NL-to-SQL compiler mapping business questions onto governed semantic views."""

    def __init__(self, semantic_layer_path: Optional[Path] = None) -> None:
        path = semantic_layer_path or DEFAULT_SEMANTIC_LAYER_PATH
        with open(path, "r", encoding="utf-8") as f:
            self.schema = yaml.safe_load(f)

    @staticmethod
    def _detect_adversarial_or_raw_sql(question: str) -> Optional[str]:
        """If the input is raw SQL or an adversarial SQL injection/DDL/DML command, pass it to SQLGuard."""
        q_strip = question.strip()
        q_upper = q_strip.upper()

        # Direct SQL statement or multi-statement or comment injection
        if (
            re.match(r"^\s*(SELECT|WITH)\s+", q_upper)
            or ";" in q_strip
            or "--" in q_strip
            or "/*" in q_strip
            or re.search(
                r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|ATTACH|COPY|PRAGMA|GRANT|EXEC|EXECUTE)\b",
                q_upper,
            )
            or re.search(
                r"\b(PG_SHADOW|PG_AUTHID|INFORMATION_SCHEMA|SQLITE_MASTER|DUCKDB_SECRETS|READ_CSV|READ_PARQUET)\b",
                q_upper,
            )
        ):
            return q_strip

        # If the user asks in plain English to delete, drop, wipe, or modify tables
        if re.search(
            r"\b(wipe|erase|destroy|overwrite|modify|hack|dump\s+passwords|exfiltrate)\b",
            question.lower(),
        ):
            return "DELETE FROM daily_sales"

        return None

    @staticmethod
    def _extract_time_filters(q_lower: str, alias: str = "d") -> List[str]:
        clauses: List[str] = []
        col = f"{alias}.sale_date" if alias else "sale_date"

        # Specific ISO date (YYYY-MM-DD)
        date_match = re.search(r"\b(202[45]-\d{2}-\d{2})\b", q_lower)
        if date_match:
            clauses.append(f"{col} = '{date_match.group(1)}'")
            return clauses

        # Specific Month + Year (e.g. "december 2025", "november 2024")
        for m_name, m_num in MONTH_MAP.items():
            m_yr = re.search(rf"\b{m_name}\s+(202[45])\b", q_lower)
            if m_yr:
                yr = m_yr.group(1)
                clauses.append(f"STRFTIME('%Y-%m', {col}) = '{yr}-{m_num}'")
                return clauses

        # Quarter + Year (e.g. "q4 2025", "q1 2024", "fourth quarter of 2025")
        q_match = re.search(r"\bq([1-4])\s+(202[45])\b", q_lower)
        if q_match:
            q_num = int(q_match.group(1))
            yr = q_match.group(2)
            start_m = (q_num - 1) * 3 + 1
            end_m = q_num * 3
            clauses.append(
                f"STRFTIME('%Y-%m', {col}) BETWEEN '{yr}-{start_m:02d}' AND '{yr}-{end_m:02d}'"
            )
            return clauses

        # Half-year (H1/H2)
        h_match = re.search(r"\bh([12])\s+(202[45])\b", q_lower)
        if h_match:
            h_num = int(h_match.group(1))
            yr = h_match.group(2)
            if h_num == 1:
                clauses.append(f"STRFTIME('%Y-%m', {col}) BETWEEN '{yr}-01' AND '{yr}-06'")
            else:
                clauses.append(f"STRFTIME('%Y-%m', {col}) BETWEEN '{yr}-07' AND '{yr}-12'")
            return clauses

        # Single Year (when not doing a YoY comparison across both years)
        years_found = re.findall(r"\b(202[45])\b", q_lower)
        unique_years = sorted(set(years_found))
        if len(unique_years) == 1 and "yoy" not in q_lower and "year-over-year" not in q_lower and "year over year" not in q_lower:
            clauses.append(f"STRFTIME('%Y', {col}) = '{unique_years[0]}'")

        # Month without year (e.g. "in december") when not comparing festive vs non-festive
        if "vs" not in q_lower and "versus" not in q_lower and "compare" not in q_lower:
            for m_name, m_num in MONTH_MAP.items():
                if re.search(rf"\bin\s+{m_name}\b", q_lower):
                    clauses.append(f"STRFTIME('%m', {col}) = '{m_num}'")
                    break

        return clauses

    @staticmethod
    def _extract_entity_filters(question: str, q_lower: str) -> Tuple[List[str], bool]:
        """Return (where_clauses, needs_stores_join)."""
        clauses: List[str] = []
        needs_stores = False

        # Store name filter
        for sname in STORE_NAMES:
            if sname.lower() in q_lower:
                clauses.append(f"s.store_name = '{sname}'")
                needs_stores = True

        # City filter
        for city in CITY_NAMES:
            if re.search(rf"\b{re.escape(city.lower())}\b", q_lower):
                # Avoid double-filtering if store name in that city was already matched
                if not any("s.store_name" in c for c in clauses):
                    clauses.append(f"s.city = '{city}'")
                    needs_stores = True

        # Region filter
        for reg in REGION_NAMES:
            if reg.lower() in q_lower or reg.lower().replace("-", " ") in q_lower:
                clauses.append(f"s.region = '{reg}'")
                needs_stores = True

        # Category filter
        category_aliases = {
            "Groceries & Staples": ["groceries & staples", "groceries and staples", "groceries"],
            "Beverages & Dairy": ["beverages & dairy", "beverages and dairy", "beverages", "dairy"],
            "Personal Care": ["personal care"],
            "Household & Cleaning": ["household & cleaning", "household and cleaning", "household", "cleaning"],
            "Fresh & Bakery": ["fresh & bakery", "fresh and bakery", "bakery"],
        }
        for canonical_cat, aliases in category_aliases.items():
            if any(re.search(rf"\b{re.escape(a)}\b", q_lower) for a in aliases):
                clauses.append(f"d.category = '{canonical_cat}'")
                break

        # Sales Channel filter
        channel_aliases = {
            "In-Store POS": ["in-store pos", "in store pos", "pos channel"],
            "WhatsApp Commerce": ["whatsapp commerce", "whatsapp"],
            "Web Delivery": ["web delivery", "online delivery", "web channel"],
        }
        for canonical_chan, aliases in channel_aliases.items():
            if any(a in q_lower for a in aliases):
                clauses.append(f"d.channel = '{canonical_chan}'")
                break

        # Store format filter
        for fmt in STORE_FORMATS:
            if re.search(rf"\b{fmt.lower()}\s+(?:stores|format|branches|locations)\b", q_lower):
                clauses.append(f"s.channel = '{fmt}'")
                needs_stores = True
                break

        # Salary week / Festive filters (when not comparing them)
        is_comparison = any(w in q_lower for w in ["vs", "versus", "compare", "comparison", "difference between"])
        if not is_comparison:
            if "salary week" in q_lower or "salary-week" in q_lower or "payday week" in q_lower:
                clauses.append("d.is_salary_week = 1")
            elif "off-week" in q_lower or "non-salary week" in q_lower or "non salary week" in q_lower:
                clauses.append("d.is_salary_week = 0")

            if "festive month" in q_lower:
                clauses.append("d.is_festive_month = 1")
            elif "non-festive" in q_lower or "non festive" in q_lower:
                clauses.append("d.is_festive_month = 0")

        return clauses, needs_stores

    def compile(self, question: str) -> str:
        """Compile a natural language question into a governed SQL query."""
        adv = self._detect_adversarial_or_raw_sql(question)
        if adv is not None:
            return adv

        q_lower = question.strip().lower()

        # ------------------------------------------------------------------
        # 1. Special Pattern: YoY Growth (2025 vs 2024)
        # ------------------------------------------------------------------
        if any(k in q_lower for k in ["yoy", "year-over-year", "year over year", "2025 vs 2024", "growth from 2024 to 2025"]):
            if "by city" in q_lower or "each city" in q_lower or "per city" in q_lower:
                return (
                    "SELECT s.city, "
                    "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2024_ngn, "
                    "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2025_ngn, "
                    "ROUND(100.0 * (SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END) - "
                    "SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END)) / "
                    "NULLIF(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 0), 2) AS yoy_growth_pct "
                    "FROM daily_sales d JOIN stores s ON d.store_id = s.store_id "
                    "GROUP BY s.city ORDER BY yoy_growth_pct DESC LIMIT 500"
                )
            if "by category" in q_lower or "each category" in q_lower or "per category" in q_lower:
                return (
                    "SELECT d.category, "
                    "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2024_ngn, "
                    "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2025_ngn, "
                    "ROUND(100.0 * (SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END) - "
                    "SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END)) / "
                    "NULLIF(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 0), 2) AS yoy_growth_pct "
                    "FROM daily_sales d "
                    "GROUP BY d.category ORDER BY yoy_growth_pct DESC LIMIT 500"
                )
            if "by region" in q_lower or "each region" in q_lower:
                return (
                    "SELECT s.region, "
                    "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2024_ngn, "
                    "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2025_ngn, "
                    "ROUND(100.0 * (SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END) - "
                    "SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END)) / "
                    "NULLIF(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 0), 2) AS yoy_growth_pct "
                    "FROM daily_sales d JOIN stores s ON d.store_id = s.store_id "
                    "GROUP BY s.region ORDER BY yoy_growth_pct DESC LIMIT 500"
                )
            return (
                "SELECT "
                "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2024_ngn, "
                "ROUND(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END), 2) AS revenue_2025_ngn, "
                "ROUND(100.0 * (SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2025' THEN d.revenue_ngn ELSE 0 END) - "
                "SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END)) / "
                "NULLIF(SUM(CASE WHEN STRFTIME('%Y', d.sale_date) = '2024' THEN d.revenue_ngn ELSE 0 END), 0), 2) AS yoy_growth_pct "
                "FROM daily_sales d LIMIT 500"
            )

        # ------------------------------------------------------------------
        # 2. Special Pattern: Salary-Week vs Off-Week Comparison
        # ------------------------------------------------------------------
        if ("salary week" in q_lower or "salary-week" in q_lower) and any(
            w in q_lower for w in ["vs", "versus", "compare", "comparison", "off-week", "non-salary"]
        ):
            time_clauses = self._extract_time_filters(q_lower, alias="d")
            where_sql = f"WHERE {' AND '.join(time_clauses)} " if time_clauses else ""
            return (
                "SELECT "
                "CASE WHEN d.is_salary_week = 1 THEN 'Salary Week (25th-30th)' ELSE 'Off-Week (1st-24th, 31st)' END AS period_type, "
                "COUNT(DISTINCT d.sale_date) AS days_count, "
                "ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn, "
                "ROUND(SUM(d.revenue_ngn) / NULLIF(COUNT(DISTINCT d.sale_date), 0), 2) AS avg_daily_revenue_ngn, "
                "SUM(d.orders_count) AS orders_count, "
                "ROUND(SUM(d.revenue_ngn) / NULLIF(SUM(d.orders_count), 0), 2) AS avg_basket_ngn "
                f"FROM daily_sales d {where_sql}"
                "GROUP BY d.is_salary_week ORDER BY d.is_salary_week DESC LIMIT 500"
            )

        # ------------------------------------------------------------------
        # 3. Special Pattern: Festive December vs Non-Festive Comparison
        # ------------------------------------------------------------------
        if ("festive" in q_lower or "december" in q_lower) and any(
            w in q_lower for w in ["vs", "versus", "compare", "comparison", "non-festive", "other months", "rest of the year"]
        ):
            time_clauses = [c for c in self._extract_time_filters(q_lower, alias="d") if "%m" not in c]
            where_sql = f"WHERE {' AND '.join(time_clauses)} " if time_clauses else ""
            return (
                "SELECT "
                "CASE WHEN d.is_festive_month = 1 THEN 'Festive (December)' ELSE 'Non-Festive (Jan-Nov)' END AS season_type, "
                "COUNT(DISTINCT d.sale_date) AS days_count, "
                "ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn, "
                "ROUND(SUM(d.revenue_ngn) / NULLIF(COUNT(DISTINCT d.sale_date), 0), 2) AS avg_daily_revenue_ngn, "
                "SUM(d.orders_count) AS orders_count "
                f"FROM daily_sales d {where_sql}"
                "GROUP BY d.is_festive_month ORDER BY d.is_festive_month DESC LIMIT 500"
            )

        # ------------------------------------------------------------------
        # 4. Special Pattern: Staff, Payroll & Productivity Queries
        # ------------------------------------------------------------------
        q_without_salary_week = q_lower.replace("salary week", "").replace("salary-week", "")
        if any(
            w in q_without_salary_week
            for w in ["staff", "employee", "employees", "payroll", "salary", "salaries", "headcount", "tenure", "cashier", "store manager", "fulfillment rider"]
        ):
            # Revenue per staff member (productivity)
            if "revenue per staff" in q_lower or "productivity" in q_lower or "sales per staff" in q_lower or "revenue per employee" in q_lower:
                time_clauses = self._extract_time_filters(q_lower, alias="d")
                where_sql = f"WHERE {' AND '.join(time_clauses)} " if time_clauses else ""
                if "by city" in q_lower or "per city" in q_lower:
                    return (
                        "WITH city_sales AS ("
                        "  SELECT s.city, ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn "
                        f"  FROM daily_sales d JOIN stores s ON d.store_id = s.store_id {where_sql}"
                        "  GROUP BY s.city"
                        "), city_staff AS ("
                        "  SELECT s.city, COUNT(st.staff_id) AS staff_count "
                        "  FROM staff st JOIN stores s ON st.store_id = s.store_id WHERE st.active = 1 GROUP BY s.city"
                        ") "
                        "SELECT cs.city, cs.revenue_ngn, cf.staff_count, "
                        "ROUND(cs.revenue_ngn / NULLIF(cf.staff_count, 0), 2) AS revenue_per_staff_ngn "
                        "FROM city_sales cs JOIN city_staff cf ON cs.city = cf.city "
                        "ORDER BY revenue_per_staff_ngn DESC LIMIT 500"
                    )
                return (
                    "WITH store_sales AS ("
                    "  SELECT d.store_id, ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn "
                    f"  FROM daily_sales d {where_sql}GROUP BY d.store_id"
                    "), store_staff AS ("
                    "  SELECT store_id, COUNT(staff_id) AS staff_count FROM staff WHERE active = 1 GROUP BY store_id"
                    ") "
                    "SELECT s.store_name, s.city, ss.revenue_ngn, st.staff_count, "
                    "ROUND(ss.revenue_ngn / NULLIF(st.staff_count, 0), 2) AS revenue_per_staff_ngn "
                    "FROM store_sales ss JOIN stores s ON ss.store_id = s.store_id "
                    "JOIN store_staff st ON ss.store_id = st.store_id "
                    "ORDER BY revenue_per_staff_ngn DESC LIMIT 500"
                )

            # Staff queries against staff (+ stores)
            staff_where = ["st.active = 1"]
            for city in CITY_NAMES:
                if re.search(rf"\b{re.escape(city.lower())}\b", q_lower):
                    staff_where.append(f"s.city = '{city}'")
            for role in STAFF_ROLE_NAMES:
                if role.lower() in q_lower:
                    staff_where.append(f"st.role = '{role}'")
            where_clause = "WHERE " + " AND ".join(staff_where)

            if "tenure" in q_lower:
                if "by role" in q_lower or "per role" in q_lower or "each role" in q_lower:
                    return (
                        "SELECT st.role, COUNT(*) AS staff_count, "
                        "ROUND(AVG(st.tenure_months), 2) AS avg_tenure_months "
                        f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} "
                        "GROUP BY st.role ORDER BY avg_tenure_months DESC LIMIT 500"
                    )
                if "by city" in q_lower or "per city" in q_lower:
                    return (
                        "SELECT s.city, COUNT(*) AS staff_count, "
                        "ROUND(AVG(st.tenure_months), 2) AS avg_tenure_months "
                        f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} "
                        "GROUP BY s.city ORDER BY avg_tenure_months DESC LIMIT 500"
                    )
                return (
                    "SELECT ROUND(AVG(st.tenure_months), 2) AS avg_tenure_months, COUNT(*) AS staff_count "
                    f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} LIMIT 500"
                )

            if "by role" in q_lower or "per role" in q_lower or "each role" in q_lower:
                return (
                    "SELECT st.role, COUNT(*) AS staff_count, "
                    "ROUND(SUM(st.monthly_salary_ngn), 2) AS total_monthly_payroll_ngn, "
                    "ROUND(AVG(st.monthly_salary_ngn), 2) AS avg_monthly_salary_ngn "
                    f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} "
                    "GROUP BY st.role ORDER BY total_monthly_payroll_ngn DESC LIMIT 500"
                )
            if "by city" in q_lower or "per city" in q_lower or "each city" in q_lower:
                return (
                    "SELECT s.city, COUNT(*) AS staff_count, "
                    "ROUND(SUM(st.monthly_salary_ngn), 2) AS total_monthly_payroll_ngn, "
                    "ROUND(AVG(st.monthly_salary_ngn), 2) AS avg_monthly_salary_ngn "
                    f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} "
                    "GROUP BY s.city ORDER BY total_monthly_payroll_ngn DESC LIMIT 500"
                )
            if "by store" in q_lower or "per store" in q_lower or "each store" in q_lower:
                return (
                    "SELECT s.store_name, s.city, COUNT(*) AS staff_count, "
                    "ROUND(SUM(st.monthly_salary_ngn), 2) AS total_monthly_payroll_ngn "
                    f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} "
                    "GROUP BY s.store_name, s.city ORDER BY total_monthly_payroll_ngn DESC LIMIT 500"
                )
            if "payroll" in q_lower or "salary" in q_lower or "salaries" in q_lower:
                return (
                    "SELECT ROUND(SUM(st.monthly_salary_ngn), 2) AS total_monthly_payroll_ngn, "
                    "ROUND(AVG(st.monthly_salary_ngn), 2) AS avg_monthly_salary_ngn, "
                    "COUNT(*) AS staff_count "
                    f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} LIMIT 500"
                )
            return (
                "SELECT COUNT(*) AS staff_count, "
                "ROUND(SUM(st.monthly_salary_ngn), 2) AS total_monthly_payroll_ngn "
                f"FROM staff st JOIN stores s ON st.store_id = s.store_id {where_clause} LIMIT 500"
            )

        # ------------------------------------------------------------------
        # 5. Special Pattern: Product Catalog Queries (`products` table)
        # ------------------------------------------------------------------
        if any(w in q_lower for w in ["sku", "skus", "product catalog", "unit price", "unit cost", "most expensive product", "products in catalog", "how many products"]):
            cat_filter = ""
            for cat in CATEGORY_NAMES:
                if cat.lower() in q_lower or cat.split()[0].lower() in q_lower:
                    cat_filter = f"WHERE p.category = '{cat}' "
                    break
            if "by category" in q_lower or "per category" in q_lower or "each category" in q_lower:
                return (
                    "SELECT p.category, COUNT(*) AS product_count, "
                    "ROUND(AVG(p.unit_price_ngn), 2) AS avg_unit_price_ngn, "
                    "ROUND(AVG(p.unit_cost_ngn), 2) AS avg_unit_cost_ngn, "
                    "ROUND(AVG(100.0 * (p.unit_price_ngn - p.unit_cost_ngn) / NULLIF(p.unit_price_ngn, 0)), 2) AS avg_markup_pct "
                    "FROM products p GROUP BY p.category ORDER BY avg_unit_price_ngn DESC LIMIT 500"
                )
            if "top" in q_lower or "most expensive" in q_lower or "highest" in q_lower:
                top_n = 5
                m_top = re.search(r"\btop\s+(\d+)\b", q_lower)
                if m_top:
                    top_n = int(m_top.group(1))
                return (
                    "SELECT p.product_name, p.category, p.unit_cost_ngn, p.unit_price_ngn, "
                    "ROUND(p.unit_price_ngn - p.unit_cost_ngn, 2) AS unit_margin_ngn "
                    f"FROM products p {cat_filter}ORDER BY p.unit_price_ngn DESC LIMIT {top_n}"
                )
            return (
                "SELECT COUNT(*) AS product_count, "
                "ROUND(AVG(p.unit_price_ngn), 2) AS avg_unit_price_ngn, "
                "ROUND(AVG(p.unit_cost_ngn), 2) AS avg_unit_cost_ngn "
                f"FROM products p {cat_filter}LIMIT 500"
            )

        # ------------------------------------------------------------------
        # 6. Special Pattern: Store Floor Area / Revenue per Sqm
        # ------------------------------------------------------------------
        if "square meter" in q_lower or "sqm" in q_lower or "floor area" in q_lower:
            if "revenue" in q_lower or "sales" in q_lower:
                time_clauses = self._extract_time_filters(q_lower, alias="d")
                where_sql = f"WHERE {' AND '.join(time_clauses)} " if time_clauses else ""
                return (
                    "SELECT s.store_name, s.city, s.floor_area_sqm, "
                    "ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn, "
                    "ROUND(SUM(d.revenue_ngn) / NULLIF(s.floor_area_sqm, 0), 2) AS revenue_per_sqm_ngn "
                    f"FROM daily_sales d JOIN stores s ON d.store_id = s.store_id {where_sql}"
                    "GROUP BY s.store_name, s.city, s.floor_area_sqm "
                    "ORDER BY revenue_per_sqm_ngn DESC LIMIT 500"
                )
            if "by city" in q_lower or "per city" in q_lower:
                return (
                    "SELECT s.city, COUNT(*) AS store_count, SUM(s.floor_area_sqm) AS total_floor_area_sqm, "
                    "ROUND(AVG(s.floor_area_sqm), 1) AS avg_floor_area_sqm "
                    "FROM stores s GROUP BY s.city ORDER BY total_floor_area_sqm DESC LIMIT 500"
                )
            return (
                "SELECT s.store_name, s.city, s.region, s.channel AS store_format, s.floor_area_sqm "
                "FROM stores s ORDER BY s.floor_area_sqm DESC LIMIT 500"
            )

        # ------------------------------------------------------------------
        # 7. Special Pattern: Store Count Lookup
        # ------------------------------------------------------------------
        if re.search(r"\bhow many stores\b", q_lower) or "count of stores" in q_lower or "number of stores" in q_lower:
            if "by region" in q_lower or "per region" in q_lower or "each region" in q_lower:
                return "SELECT s.region, COUNT(*) AS store_count FROM stores s GROUP BY s.region ORDER BY store_count DESC LIMIT 500"
            if "by city" in q_lower or "per city" in q_lower or "each city" in q_lower:
                return "SELECT s.city, COUNT(*) AS store_count FROM stores s GROUP BY s.city ORDER BY store_count DESC LIMIT 500"
            if "by format" in q_lower or "by channel" in q_lower:
                return "SELECT s.channel AS store_format, COUNT(*) AS store_count FROM stores s GROUP BY s.channel ORDER BY store_count DESC LIMIT 500"
            for city in CITY_NAMES:
                if city.lower() in q_lower:
                    return f"SELECT COUNT(*) AS store_count FROM stores s WHERE s.city = '{city}' LIMIT 500"
            for reg in REGION_NAMES:
                if reg.lower() in q_lower or reg.lower().replace("-", " ") in q_lower:
                    return f"SELECT COUNT(*) AS store_count FROM stores s WHERE s.region = '{reg}' LIMIT 500"
            return "SELECT COUNT(*) AS store_count FROM stores s LIMIT 500"

        # ------------------------------------------------------------------
        # 8. Composable Analytical Query Builder over `daily_sales` + `stores`
        # ------------------------------------------------------------------
        time_clauses = self._extract_time_filters(q_lower, alias="d")
        entity_clauses, needs_stores = self._extract_entity_filters(question, q_lower)
        where_clauses = time_clauses + entity_clauses

        # Determine grouping dimension(s)
        group_cols: List[str] = []
        select_dims: List[str] = []

        if any(p in q_lower for p in ["by store format", "per store format", "by format", "across store formats"]):
            select_dims.append("s.channel AS store_format")
            group_cols.append("s.channel")
            needs_stores = True
        elif any(
            p in q_lower
            for p in [
                "by store",
                "per store",
                "each store",
                "which store",
                "top store",
                "top 3 stores",
                "top 5 stores",
                "bottom 3 stores",
                "bottom 5 stores",
                "stores ranked",
                "store had the highest",
                "store had the lowest",
                "store generated the most",
                "store with the highest",
                "store with the lowest",
            ]
        ):
            select_dims.extend(["s.store_name", "s.city"])
            group_cols.extend(["s.store_name", "s.city"])
            needs_stores = True
        elif any(
            p in q_lower
            for p in [
                "by city",
                "per city",
                "each city",
                "which city",
                "across cities",
                "top city",
                "top 3 cities",
                "city had the highest",
                "city had the lowest",
                "city generated",
            ]
        ):
            select_dims.append("s.city")
            group_cols.append("s.city")
            needs_stores = True
        elif any(
            p in q_lower
            for p in [
                "by region",
                "per region",
                "each region",
                "which region",
                "across regions",
                "region had the highest",
                "region had the lowest",
                "regional breakdown",
            ]
        ):
            select_dims.append("s.region")
            group_cols.append("s.region")
            needs_stores = True
        elif any(
            p in q_lower
            for p in [
                "by category",
                "per category",
                "each category",
                "which category",
                "across categories",
                "category breakdown",
                "category had the highest",
                "category had the lowest",
                "top category",
            ]
        ):
            select_dims.append("d.category")
            group_cols.append("d.category")
        elif any(
            p in q_lower
            for p in [
                "by channel",
                "per channel",
                "each channel",
                "which channel",
                "across channels",
                "sales channel",
                "channel breakdown",
                "channel had the highest",
            ]
        ):
            select_dims.append("d.channel")
            group_cols.append("d.channel")
        elif any(
            p in q_lower
            for p in [
                "by month",
                "per month",
                "monthly",
                "each month",
                "month-over-month",
                "which month",
                "month had the highest",
                "month had the lowest",
            ]
        ):
            select_dims.append("STRFTIME('%Y-%m', d.sale_date) AS sale_month")
            group_cols.append("STRFTIME('%Y-%m', d.sale_date)")
        elif any(p in q_lower for p in ["by year", "per year", "each year", "annual", "yearly"]):
            select_dims.append("STRFTIME('%Y', d.sale_date) AS sale_year")
            group_cols.append("STRFTIME('%Y', d.sale_date)")

        # Determine primary and secondary metrics
        metric_exprs: List[str] = []
        primary_order_col = "revenue_ngn"

        wants_return_rate = "return rate" in q_lower or "refund rate" in q_lower or "% returned" in q_lower
        wants_returns_ngn = ("returns" in q_lower or "refunds" in q_lower or "returned value" in q_lower) and not wants_return_rate
        wants_returned_orders = "returned orders" in q_lower
        wants_margin_pct = any(
            p in q_lower
            for p in ["gross margin %", "gross margin pct", "gross margin percentage", "margin percentage", "margin %", "margin rate"]
        )
        wants_margin_ngn = ("gross margin" in q_lower or "gross profit" in q_lower or "margin" in q_lower) and not wants_margin_pct
        wants_basket = any(p in q_lower for p in ["average basket", "avg basket", "basket size", "order value", "aov", "revenue per order"])
        wants_orders = any(p in q_lower for p in ["orders", "transactions", "order count"]) and not wants_basket and not wants_returned_orders
        wants_units = "units sold" in q_lower or "total units" in q_lower or "unit volume" in q_lower
        wants_cogs = "cogs" in q_lower or "cost of goods sold" in q_lower
        wants_avg_daily_rev = "average daily revenue" in q_lower or "avg daily revenue" in q_lower or "daily average revenue" in q_lower
        other_metric = any(
            [
                wants_return_rate,
                wants_returns_ngn,
                wants_returned_orders,
                wants_margin_pct,
                wants_margin_ngn,
                wants_basket,
                wants_orders,
                wants_units,
                wants_cogs,
                wants_avg_daily_rev,
            ]
        )
        asks_revenue = any(p in q_lower for p in REVENUE_PHRASES)
        if not other_metric and not asks_revenue:
            return NOT_UNDERSTOOD
        wants_revenue = "revenue" in q_lower or "sales" in q_lower or not other_metric

        if wants_avg_daily_rev:
            metric_exprs.append(
                "ROUND(SUM(d.revenue_ngn) / NULLIF(COUNT(DISTINCT d.sale_date), 0), 2) AS avg_daily_revenue_ngn"
            )
            metric_exprs.append("ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn")
            primary_order_col = "avg_daily_revenue_ngn"
        else:
            if wants_revenue:
                metric_exprs.append("ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn")
                primary_order_col = "revenue_ngn"
            if wants_margin_ngn:
                metric_exprs.append("ROUND(SUM(d.gross_margin_ngn), 2) AS gross_margin_ngn")
                if not wants_revenue:
                    primary_order_col = "gross_margin_ngn"
            if wants_margin_pct:
                metric_exprs.append(
                    "ROUND(100.0 * SUM(d.gross_margin_ngn) / NULLIF(SUM(d.revenue_ngn), 0), 2) AS gross_margin_pct"
                )
                primary_order_col = "gross_margin_pct"
            if wants_cogs:
                metric_exprs.append("ROUND(SUM(d.cogs_ngn), 2) AS cogs_ngn")
                if not wants_revenue:
                    primary_order_col = "cogs_ngn"
            if wants_orders:
                metric_exprs.append("SUM(d.orders_count) AS orders_count")
                if not wants_revenue and not wants_margin_ngn:
                    primary_order_col = "orders_count"
            if wants_units:
                metric_exprs.append("SUM(d.units_sold) AS units_sold")
                if not wants_revenue and not wants_orders:
                    primary_order_col = "units_sold"
            if wants_basket:
                metric_exprs.append(
                    "ROUND(SUM(d.revenue_ngn) / NULLIF(SUM(d.orders_count), 0), 2) AS avg_basket_ngn"
                )
                primary_order_col = "avg_basket_ngn"
            if wants_return_rate:
                metric_exprs.append(
                    "ROUND(100.0 * SUM(d.returns_ngn) / NULLIF(SUM(d.revenue_ngn), 0), 2) AS return_rate"
                )
                primary_order_col = "return_rate"
            if wants_returns_ngn:
                metric_exprs.append("ROUND(SUM(d.returns_ngn), 2) AS returns_ngn")
                if not wants_revenue:
                    primary_order_col = "returns_ngn"
            if wants_returned_orders:
                metric_exprs.append("SUM(d.returned_orders) AS returned_orders")
                primary_order_col = "returned_orders"

        if not metric_exprs:
            metric_exprs.append("ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn")

        select_clause = ", ".join(select_dims + metric_exprs)
        from_clause = "FROM daily_sales d JOIN stores s ON d.store_id = s.store_id" if needs_stores else "FROM daily_sales d"
        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        group_sql = ""
        order_sql = ""
        limit_val = 500

        if group_cols:
            group_sql = f" GROUP BY {', '.join(group_cols)}"
            # Check chronological ordering for monthly/yearly trends unless user asks for highest/lowest month
            is_ranking_question = any(
                w in q_lower for w in ["highest", "lowest", "top", "bottom", "best", "worst", "most", "least"]
            )
            if ("sale_month" in select_clause or "sale_year" in select_clause) and not is_ranking_question:
                order_col = "sale_month" if "sale_month" in select_clause else "sale_year"
                order_sql = f" ORDER BY {order_col} ASC"
            else:
                direction = (
                    "ASC"
                    if any(w in q_lower for w in ["lowest", "bottom", "least", "worst", "smallest"])
                    else "DESC"
                )
                order_sql = f" ORDER BY {primary_order_col} {direction}"

            # Check explicit Top-N / Bottom-N or singular "which store/city/category/month had the highest/lowest"
            m_limit = re.search(r"\b(?:top|bottom)\s+(\d+)\b", q_lower)
            if m_limit:
                limit_val = int(m_limit.group(1))
            elif any(
                p in q_lower
                for p in [
                    "which store",
                    "which city",
                    "which region",
                    "which category",
                    "which channel",
                    "which month",
                ]
            ) and is_ranking_question:
                limit_val = 1

        return f"SELECT {select_clause} {from_clause}{where_sql}{group_sql}{order_sql} LIMIT {limit_val}"


class BIOperationsEngine:
    """End-to-end NL-to-SQL engine combining provider adapters, semantic compiler, SQL guard, and chart spec."""

    def __init__(
        self,
        db: Optional[RetailDatabase] = None,
        guard: Optional[SQLGuard] = None,
        compiler: Optional[SemanticPatternCompiler] = None,
        provider: Optional[str] = None,
    ) -> None:
        self.db = db or RetailDatabase()
        self.guard = guard or SQLGuard()
        self.compiler = compiler or SemanticPatternCompiler()
        self.provider = (provider or os.getenv("BI_AGENT_PROVIDER", "semantic")).lower()

    def _call_external_llm(self, question: str) -> Optional[str]:
        """Optional adapter for Anthropic Claude, OpenAI, or Ollama when configured."""
        system_prompt = (
            "You are a SQL compiler for a Nigerian retail DuckDB/SQLite database.\n"
            "Approved tables:\n"
            "- stores(store_id, store_name, city, region, channel, opened_date, floor_area_sqm)\n"
            "- products(product_id, product_name, category, unit_cost_ngn, unit_price_ngn)\n"
            "- daily_sales(sale_id, sale_date, store_id, category, channel, orders_count, units_sold, "
            "revenue_ngn, cogs_ngn, gross_margin_ngn, returns_ngn, returned_orders, is_salary_week, is_festive_month)\n"
            "- staff(staff_id, store_id, full_name, role, monthly_salary_ngn, tenure_months, active)\n"
            "Return ONLY a single valid read-only SELECT SQL query with no markdown fences or commentary."
        )
        try:
            if self.provider == "openai" and os.getenv("OPENAI_API_KEY"):
                req_body = json.dumps(
                    {
                        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        "temperature": 0.0,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": question},
                        ],
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=req_body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
                    },
                )
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    return payload["choices"][0]["message"]["content"].strip()

            if self.provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
                req_body = json.dumps(
                    {
                        "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
                        "max_tokens": 400,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": question}],
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=req_body,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
                        "anthropic-version": "2023-06-01",
                    },
                )
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    return payload["content"][0]["text"].strip()

            if self.provider == "ollama" and os.getenv("OLLAMA_BASE_URL"):
                base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
                req_body = json.dumps(
                    {
                        "model": os.getenv("OLLAMA_MODEL", "llama3.1"),
                        "prompt": f"{system_prompt}\n\nQuestion: {question}\nSQL:",
                        "stream": False,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"{base_url}/api/generate",
                    data=req_body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    return payload.get("response", "").strip()
        except Exception:
            return None
        return None

    @staticmethod
    def _build_chart_spec(columns: List[str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a declarative chart specification from query result columns and rows."""
        if not rows or not columns:
            return {"type": "none", "x": None, "y": None}
        if len(rows) == 1 and len(columns) <= 3:
            return {"type": "kpi_card", "metrics": columns}
        dim_col = columns[0]
        num_cols = [
            c
            for c in columns[1:]
            if any(isinstance(r.get(c), (int, float)) for r in rows)
        ]
        y_col = num_cols[0] if num_cols else columns[-1]
        if "month" in dim_col or "year" in dim_col or "date" in dim_col:
            chart_type = "line"
        else:
            chart_type = "bar"
        return {
            "type": chart_type,
            "x": dim_col,
            "y": y_col,
            "series": num_cols,
            "row_count": len(rows),
        }

    @staticmethod
    def _format_value(col_name: str, val: Any) -> str:
        if val is None:
            return "N/A"
        if isinstance(val, (int, float)):
            if "ngn" in col_name or "salary" in col_name or "payroll" in col_name or "price" in col_name or "cost" in col_name:
                return f"₦{val:,.2f}"
            if "pct" in col_name or "rate" in col_name:
                return f"{val:.2f}%"
            if isinstance(val, float):
                return f"{val:,.2f}"
            return f"{val:,}"
        return str(val)

    def _build_summary_text(
        self,
        question: str,
        columns: List[str],
        rows: List[Dict[str, Any]],
        safe: bool,
        reason: Optional[str],
    ) -> str:
        if not safe:
            return f"Blocked by SQLGuard: {reason}"
        if not rows:
            return "Query executed safely but returned 0 matching rows."
        top = rows[0]
        if len(rows) == 1:
            parts = [f"{col}={self._format_value(col, top.get(col))}" for col in columns]
            return f"Result for '{question}': " + ", ".join(parts) + "."
        lead_parts = [f"{col}={self._format_value(col, top.get(col))}" for col in columns[:3]]
        return (
            f"Returned {len(rows)} rows. Top record: "
            + ", ".join(lead_parts)
            + "."
        )

    def ask(self, question: str) -> Dict[str, Any]:
        """Translate a natural language question into SQL, validate via SQLGuard, execute, and summarize."""
        t0 = time.perf_counter()

        candidate_sql = self._call_external_llm(question)
        used_provider = self.provider if candidate_sql else "semantic_compiler"
        if not candidate_sql:
            candidate_sql = self.compiler.compile(question)

        if candidate_sql.strip() == NOT_UNDERSTOOD:
            return {
                "question": question,
                "sql": None,
                "rows": [],
                "columns": [],
                "summary_text": NOT_UNDERSTOOD_MESSAGE,
                "chart_spec": {"type": "none", "x": None, "y": None},
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
                "safe": True,
                "understood": False,
                "guard_reason": None,
                "provider": used_provider,
                "cost_usd": 0.0,
            }

        guard_res = self.guard.validate(candidate_sql)
        reject_reason = guard_res.reason if not guard_res.safe else None
        columns: List[str] = []
        rows: List[Dict[str, Any]] = []
        if guard_res.safe:
            sanitized_sql = guard_res.sanitized_sql
            try:
                columns, rows = self.db.execute_query(sanitized_sql)
            except sqlite3.Error as exc:
                reject_reason = f"Query execution failed: {exc}"
        if reject_reason is not None:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return {
                "question": question,
                "sql": candidate_sql,
                "rows": [],
                "columns": [],
                "summary_text": self._build_summary_text(
                    question, [], [], safe=False, reason=reject_reason
                ),
                "chart_spec": {"type": "none", "x": None, "y": None},
                "latency_ms": latency_ms,
                "safe": False,
                "guard_reason": reject_reason,
                "provider": used_provider,
                "cost_usd": 0.0,
            }

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        return {
            "question": question,
            "sql": sanitized_sql,
            "rows": rows,
            "columns": columns,
            "summary_text": self._build_summary_text(
                question, columns, rows, safe=True, reason=None
            ),
            "chart_spec": self._build_chart_spec(columns, rows),
            "latency_ms": latency_ms,
            "safe": True,
            "understood": True,
            "guard_reason": None,
            "provider": used_provider,
            "cost_usd": 0.0 if used_provider == "semantic_compiler" else 0.0004,
        }
