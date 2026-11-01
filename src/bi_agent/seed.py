"""Synthetic Nigerian retail chain dataset generator (2024-2025) and local database loader."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import duckdb  # type: ignore

    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT_DIR / "data"
# Largest single string/blob a query may build (1 MB); far above any real result value.
MAX_VALUE_BYTES = 1_000_000
DEFAULT_SQLITE_PATH = DEFAULT_DATA_DIR / "retail_ops.db"
DEFAULT_DUCKDB_PATH = DEFAULT_DATA_DIR / "retail_ops.duckdb"


STORES_SPEC = [
    ("STR-LOS-01", "Lekki Flagship", "Lagos", "South-West", "Flagship", "2021-03-15", 1450, 1.45),
    ("STR-LOS-02", "Victoria Island Hyper", "Lagos", "South-West", "Hypermarket", "2021-07-01", 1600, 1.38),
    ("STR-LOS-03", "Ikeja City Mall", "Lagos", "South-West", "Mall", "2022-01-20", 1100, 1.18),
    ("STR-ABJ-01", "Wuse II Mall", "Abuja", "North-Central", "Mall", "2021-05-10", 1300, 1.28),
    ("STR-ABJ-02", "Gwarinpa Express", "Abuja", "North-Central", "Express", "2022-06-12", 680, 0.92),
    ("STR-PHC-01", "Port Harcourt GRA", "Port Harcourt", "South-South", "Flagship", "2021-11-01", 1200, 1.12),
    ("STR-PHC-02", "Trans-Amadi Express", "Port Harcourt", "South-South", "Express", "2023-02-14", 620, 0.84),
    ("STR-IBA-01", "Ibadan Dugbe Express", "Ibadan", "South-West", "Express", "2022-04-05", 720, 0.88),
    ("STR-KAN-01", "Kano City Mall", "Kano", "North-West", "Mall", "2022-09-01", 1150, 0.96),
    ("STR-ENU-01", "Enugu Polo Park", "Enugu", "South-East", "Mall", "2022-11-18", 980, 0.90),
]

CATEGORIES_SPEC = [
    ("Groceries & Staples", 4850.0, 0.22, 1.30),
    ("Beverages & Dairy", 3600.0, 0.26, 1.15),
    ("Personal Care", 5400.0, 0.34, 0.85),
    ("Household & Cleaning", 4100.0, 0.29, 0.90),
    ("Fresh & Bakery", 2950.0, 0.38, 0.95),
]

PRODUCTS_SPEC = [
    ("PRD-GRO-01", "Royal Stallion Parboiled Rice 10kg", "Groceries & Staples", 14200.0, 18200.0),
    ("PRD-GRO-02", "Golden Penny Semovita 5kg", "Groceries & Staples", 5800.0, 7400.0),
    ("PRD-GRO-03", "Devon King's Vegetable Oil 3L", "Groceries & Staples", 6900.0, 8800.0),
    ("PRD-GRO-04", "Dangote Refined Sugar 2kg", "Groceries & Staples", 2700.0, 3500.0),
    ("PRD-GRO-05", "Indomie Instant Noodles Carton", "Groceries & Staples", 6400.0, 8200.0),
    ("PRD-BEV-01", "Peak Full Cream Milk Tin 6-Pack", "Beverages & Dairy", 4900.0, 6600.0),
    ("PRD-BEV-02", "Milo Activ-Go Refill 800g", "Beverages & Dairy", 5100.0, 6900.0),
    ("PRD-BEV-03", "Malta Guinness Can 6-Pack", "Beverages & Dairy", 2800.0, 3800.0),
    ("PRD-BEV-04", "Chivita 100% Fruit Juice 1L", "Beverages & Dairy", 1450.0, 2000.0),
    ("PRD-BEV-05", "Hollandia Yoghurt 1L", "Beverages & Dairy", 1600.0, 2200.0),
    ("PRD-PER-01", "Nivea Nourishing Body Lotion 400ml", "Personal Care", 3400.0, 5200.0),
    ("PRD-PER-02", "Dettol Antiseptic Liquid 500ml", "Personal Care", 2600.0, 3900.0),
    ("PRD-PER-03", "Sensodyne Rapid Relief Toothpaste", "Personal Care", 2200.0, 3400.0),
    ("PRD-PER-04", "Pears Baby Oil & Wash Pack", "Personal Care", 3100.0, 4700.0),
    ("PRD-PER-05", "Dove Moisture Shampoo 400ml", "Personal Care", 2900.0, 4400.0),
    ("PRD-HOU-01", "Ariel Automatic Detergent 2kg", "Household & Cleaning", 4100.0, 5800.0),
    ("PRD-HOU-02", "Morning Fresh Dishwash 1L", "Household & Cleaning", 1550.0, 2200.0),
    ("PRD-HOU-03", "Harpic Power Plus Twin Pack", "Household & Cleaning", 2700.0, 3850.0),
    ("PRD-HOU-04", "Rose Carla Tissue 9-Roll Pack", "Household & Cleaning", 2900.0, 4100.0),
    ("PRD-HOU-05", "Sniper Multi-Insecticide 600ml", "Household & Cleaning", 2400.0, 3400.0),
    ("PRD-FRE-01", "Artisan Sliced Brioche Loaf", "Fresh & Bakery", 1100.0, 1800.0),
    ("PRD-FRE-02", "Assorted Nigerian Meat Pie 4-Pack", "Fresh & Bakery", 1950.0, 3200.0),
    ("PRD-FRE-03", "Farm Fresh Crate of 30 Eggs", "Fresh & Bakery", 3400.0, 4900.0),
    ("PRD-FRE-04", "Pre-Washed Jos Salad Greens", "Fresh & Bakery", 1200.0, 2000.0),
    ("PRD-FRE-05", "Grilled Chicken Rotisserie Whole", "Fresh & Bakery", 4600.0, 7400.0),
]

STAFF_ROLES = [
    ("Store Manager", 420000.0, 1),
    ("Inventory Lead", 240000.0, 1),
    ("Cashier", 135000.0, 3),
    ("Sales Associate", 125000.0, 2),
    ("Fulfillment Rider", 145000.0, 1),
]

FIRST_NAMES = [
    "Chinedu", "Amina", "Tunde", "Ngozi", "Ibrahim", "Funke", "Emeka", "Zainab",
    "Olumide", "Chioma", "Musa", "Adaeze", "Segun", "Halima", "Obinna", "Bukola",
]
LAST_NAMES = [
    "Okafor", "Bello", "Adeyemi", "Eze", "Abubakar", "Ogunleye", "Nwosu", "Suleiman",
    "Balogun", "Okonkwo", "Lawal", "Nnamdi", "Afolabi", "Danjuma", "Chukwu", "Ojo",
]

CHANNELS = ["In-Store POS", "WhatsApp Commerce", "Web Delivery"]


def generate_retail_frames(seed: int = 42) -> Dict[str, pd.DataFrame]:
    """Generate deterministic 2-year Nigerian retail tables with seasonality and anomalies."""
    rng = np.random.default_rng(seed)

    # 1. Stores table
    stores_rows = []
    for sid, sname, city, region, channel, opened, sqm, _mult in STORES_SPEC:
        stores_rows.append(
            {
                "store_id": sid,
                "store_name": sname,
                "city": city,
                "region": region,
                "channel": channel,
                "opened_date": opened,
                "floor_area_sqm": sqm,
            }
        )
    stores_df = pd.DataFrame(stores_rows)

    # 2. Products table
    products_rows = []
    for pid, pname, cat, cost, price in PRODUCTS_SPEC:
        products_rows.append(
            {
                "product_id": pid,
                "product_name": pname,
                "category": cat,
                "unit_cost_ngn": cost,
                "unit_price_ngn": price,
            }
        )
    products_df = pd.DataFrame(products_rows)

    # 3. Staff table (8 staff per store = 80 staff total)
    staff_rows = []
    staff_idx = 1
    for sid, sname, city, _reg, _chan, _opened, _sqm, store_mult in STORES_SPEC:
        for role, base_salary, count in STAFF_ROLES:
            for _ in range(count):
                fname = FIRST_NAMES[(staff_idx * 3) % len(FIRST_NAMES)]
                lname = LAST_NAMES[(staff_idx * 7) % len(LAST_NAMES)]
                salary_jitter = rng.uniform(0.94, 1.08)
                city_factor = 1.12 if city in ("Lagos", "Abuja") else 0.96
                salary = round(base_salary * city_factor * salary_jitter, -2)
                tenure = int(rng.integers(6, 48))
                staff_rows.append(
                    {
                        "staff_id": f"STF-{staff_idx:03d}",
                        "store_id": sid,
                        "full_name": f"{fname} {lname}",
                        "role": role,
                        "monthly_salary_ngn": float(salary),
                        "tenure_months": tenure,
                        "active": 1,
                    }
                )
                staff_idx += 1
    staff_df = pd.DataFrame(staff_rows)

    # 4. Daily sales table (2024-01-01 through 2025-12-31 = 731 days)
    start_dt = date(2024, 1, 1)
    end_dt = date(2025, 12, 31)
    total_days = (end_dt - start_dt).days + 1

    sales_rows = []
    sale_counter = 1

    for day_offset in range(total_days):
        cur_date = start_dt + timedelta(days=day_offset)
        date_str = cur_date.isoformat()
        year = cur_date.year
        month = cur_date.month
        day = cur_date.day
        dow = cur_date.weekday()  # 0=Mon, 6=Sun

        # YoY organic + inflation growth factor
        yoy_mult = 1.00 if year == 2024 else 1.19

        # Seasonality: December festive peak (+38%), November Black Friday (+12%), Jan post-holiday dip (-10%)
        if month == 12:
            month_mult = 1.38
        elif month == 11:
            month_mult = 1.12
        elif month == 1:
            month_mult = 0.91
        elif month in (4, 6):  # Eid / Easter boosts
            month_mult = 1.06
        else:
            month_mult = 1.00

        # Salary-week spike (25th to 30th of every month)
        is_salary_week = 1 if 25 <= day <= 30 else 0
        salary_mult = 1.27 if is_salary_week else 1.00

        # Weekend shopping boost (Fri/Sat/Sun)
        dow_mult = 1.16 if dow in (4, 5) else (1.08 if dow == 6 else 0.96)

        is_festive_month = 1 if month == 12 else 0

        for sid, _sname, city, _reg, _schan, _opened, _sqm, store_mult in STORES_SPEC:
            for cat_idx, (cat_name, base_basket, base_margin_pct, cat_mult) in enumerate(CATEGORIES_SPEC):
                # Deterministic primary fulfillment channel rotation weighted toward In-Store POS
                chan_selector = (day_offset + cat_idx + int(sid[-2:])) % 10
                if chan_selector < 6:
                    channel = "In-Store POS"
                elif chan_selector < 9:
                    channel = "WhatsApp Commerce"
                else:
                    channel = "Web Delivery"

                noise = rng.normal(1.0, 0.045)
                expected_orders = (
                    42.0
                    * store_mult
                    * cat_mult
                    * yoy_mult
                    * month_mult
                    * salary_mult
                    * dow_mult
                    * noise
                )
                orders_count = max(5, int(round(expected_orders)))
                units_per_order = rng.uniform(2.1, 3.4)
                units_sold = max(orders_count, int(round(orders_count * units_per_order)))

                basket_jitter = rng.normal(1.0, 0.03)
                city_price_mult = 1.06 if city in ("Lagos", "Abuja") else 0.98
                revenue_ngn = round(orders_count * base_basket * city_price_mult * yoy_mult * basket_jitter, 2)

                margin_pct = np.clip(base_margin_pct + rng.normal(0.0, 0.008), 0.12, 0.52)
                return_rate_frac = np.clip(rng.normal(0.018, 0.0035), 0.004, 0.045)

                # Inject high-signal operational anomalies in late December 2025 for KPI monitor
                if date_str == "2025-12-28" and sid == "STR-LOS-01" and cat_name == "Groceries & Staples":
                    # Anomaly 1: Corporate hamper surge at Lekki Flagship
                    orders_count = int(orders_count * 2.55)
                    units_sold = int(units_sold * 2.70)
                    revenue_ngn = round(revenue_ngn * 2.65, 2)
                elif date_str == "2025-12-29" and sid == "STR-LOS-02" and cat_name == "Beverages & Dairy":
                    # Anomaly 2: Bulk festive event orders at Victoria Island Hyper
                    orders_count = int(orders_count * 2.35)
                    units_sold = int(units_sold * 2.40)
                    revenue_ngn = round(revenue_ngn * 2.45, 2)
                elif date_str == "2025-12-22" and sid == "STR-PHC-01" and cat_name == "Groceries & Staples":
                    # Anomaly 3: Severe POS network & inverter outage at Port Harcourt GRA
                    orders_count = max(4, int(orders_count * 0.24))
                    units_sold = max(6, int(units_sold * 0.24))
                    revenue_ngn = round(revenue_ngn * 0.24, 2)
                elif date_str == "2025-12-27" and sid == "STR-KAN-01" and cat_name == "Fresh & Bakery":
                    # Anomaly 4: Cold-chain chiller failure spiking returns & slashing margin at Kano City Mall
                    return_rate_frac = 0.195
                    margin_pct = 0.115
                elif date_str == "2025-12-30" and sid == "STR-ABJ-01" and cat_name == "Beverages & Dairy":
                    # Anomaly 5: Damaged supplier batch spiking return rate at Wuse II Mall
                    return_rate_frac = 0.172
                elif date_str == "2025-12-26" and sid == "STR-IBA-01" and cat_name == "Personal Care":
                    # Anomaly 6: Unapproved clearance discount slashing gross margin at Ibadan Dugbe Express
                    margin_pct = 0.082

                gross_margin_ngn = round(revenue_ngn * float(margin_pct), 2)
                cogs_ngn = round(revenue_ngn - gross_margin_ngn, 2)
                returns_ngn = round(revenue_ngn * float(return_rate_frac), 2)
                returned_orders = max(0, int(round(orders_count * float(return_rate_frac))))

                sales_rows.append(
                    {
                        "sale_id": f"SL-{sale_counter:06d}",
                        "sale_date": date_str,
                        "store_id": sid,
                        "category": cat_name,
                        "channel": channel,
                        "orders_count": orders_count,
                        "units_sold": units_sold,
                        "revenue_ngn": revenue_ngn,
                        "cogs_ngn": cogs_ngn,
                        "gross_margin_ngn": gross_margin_ngn,
                        "returns_ngn": returns_ngn,
                        "returned_orders": returned_orders,
                        "is_salary_week": is_salary_week,
                        "is_festive_month": is_festive_month,
                    }
                )
                sale_counter += 1

    daily_sales_df = pd.DataFrame(sales_rows)
    return {
        "stores": stores_df,
        "products": products_df,
        "staff": staff_df,
        "daily_sales": daily_sales_df,
    }


def seed_all(
    data_dir: Optional[Path] = None,
    sqlite_path: Optional[Path] = None,
    duckdb_path: Optional[Path] = None,
    seed: int = 42,
    force: bool = False,
) -> Dict[str, Any]:
    """Generate CSV files and populate local SQLite (and DuckDB if installed) databases."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    sq_path = sqlite_path or (target_dir / "retail_ops.db")
    dk_path = duckdb_path or (target_dir / "retail_ops.duckdb")

    if not force and sq_path.exists() and (target_dir / "daily_sales.csv").exists():
        # Verify row count in existing sqlite database
        with sqlite3.connect(sq_path) as conn:
            cur = conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM daily_sales")
                cnt = cur.fetchone()[0]
                if cnt == 36550:
                    return {
                        "status": "cached",
                        "sqlite_path": str(sq_path),
                        "duckdb_path": str(dk_path) if HAS_DUCKDB else None,
                        "daily_sales_rows": cnt,
                    }
            except sqlite3.Error:
                pass

    frames = generate_retail_frames(seed=seed)

    # Save CSV files
    for name, df in frames.items():
        df.to_csv(target_dir / f"{name}.csv", index=False)

    # Populate SQLite database
    if sq_path.exists():
        sq_path.unlink()
    with sqlite3.connect(sq_path) as conn:
        for name, df in frames.items():
            df.to_sql(name, conn, index=False, if_exists="replace")
        conn.execute("CREATE INDEX idx_sales_date ON daily_sales(sale_date)")
        conn.execute("CREATE INDEX idx_sales_store ON daily_sales(store_id)")
        conn.execute("CREATE INDEX idx_sales_category ON daily_sales(category)")

    # Populate DuckDB database if duckdb package is installed
    if HAS_DUCKDB:
        if dk_path.exists():
            dk_path.unlink()
        con = duckdb.connect(str(dk_path))
        try:
            for name, df in frames.items():
                con.register(f"df_{name}", df)
                con.execute(f"CREATE TABLE {name} AS SELECT * FROM df_{name}")
        finally:
            con.close()

    return {
        "status": "seeded",
        "sqlite_path": str(sq_path),
        "duckdb_path": str(dk_path) if HAS_DUCKDB else None,
        "stores_rows": len(frames["stores"]),
        "products_rows": len(frames["products"]),
        "staff_rows": len(frames["staff"]),
        "daily_sales_rows": len(frames["daily_sales"]),
    }


class RetailDatabase:
    """Read-only analytical query executor supporting DuckDB and SQLite seamlessly."""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        prefer_duckdb: bool = True,
        query_timeout_ms: Optional[int] = None,
    ) -> None:
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.query_timeout_ms = (
            int(query_timeout_ms)
            if query_timeout_ms is not None
            else int(os.getenv("BI_AGENT_QUERY_TIMEOUT_MS", "5000"))
        )
        self.sqlite_path = self.data_dir / "retail_ops.db"
        self.duckdb_path = self.data_dir / "retail_ops.duckdb"
        self.prefer_duckdb = prefer_duckdb and HAS_DUCKDB
        if not self.sqlite_path.exists():
            seed_all(data_dir=self.data_dir)

    @property
    def backend_name(self) -> str:
        return "duckdb" if self.prefer_duckdb else "sqlite"

    def execute_query(self, sql: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Execute a validated read-only SQL query and return (columns, list_of_row_dicts)."""
        if self.prefer_duckdb and self.duckdb_path.exists():
            con = duckdb.connect(str(self.duckdb_path), read_only=True)
            try:
                rel = con.execute(sql)
                columns = [desc[0] for desc in rel.description]
                raw_rows = rel.fetchall()
            finally:
                con.close()
        else:
            # Open SQLite in read-only URI mode
            uri = f"file:{self.sqlite_path.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
                # Wall-clock budget: aborts runaway queries (e.g. unbounded WITH RECURSIVE).
                deadline = time.monotonic() + self.query_timeout_ms / 1000.0
                conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
                # The progress handler cannot interrupt a single huge printf()/replace() call,
                # so also cap any one string/blob value (SQLite's default is 1 GB).
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
                cur = conn.cursor()
                cur.execute(sql)
                columns = [desc[0] for desc in cur.description] if cur.description else []
                raw_rows = cur.fetchall()

        rows: List[Dict[str, Any]] = []
        for r in raw_rows:
            row_dict: Dict[str, Any] = {}
            for col, val in zip(columns, r):
                if isinstance(val, (np.integer,)):
                    val = int(val)
                elif isinstance(val, (np.floating,)):
                    val = float(val)
                row_dict[col] = val
            rows.append(row_dict)
        return columns, rows


if __name__ == "__main__":
    info = seed_all(force=True)
    print(f"Seeded Nigerian Retail dataset: {info}")
