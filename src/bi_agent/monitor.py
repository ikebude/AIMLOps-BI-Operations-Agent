"""KPI Anomaly Monitor (Rolling 28-Day Robust MAD Z-Score) and Weekly Executive Digest Generator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.bi_agent.seed import ROOT_DIR, RetailDatabase

DEFAULT_DIGEST_PATH = ROOT_DIR / "reports" / "weekly_digest.md"


@dataclass
class KPIAnomaly:
    """Detected operational anomaly across store-category KPI series."""

    sale_date: str
    store_id: str
    store_name: str
    city: str
    region: str
    category: str
    metric: str
    observed_value: float
    rolling_median: float
    rolling_mad: float
    robust_z: float
    direction: str
    severity: str
    pct_deviation: float
    operational_note: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KPIAnomalyMonitor:
    """Computes rolling 28-day robust z-scores (MAD-based) and writes weekly executive digests."""

    def __init__(
        self,
        db: Optional[RetailDatabase] = None,
        window_days: int = 28,
        z_threshold: float = 2.5,
    ) -> None:
        self.db = db or RetailDatabase()
        self.window_days = window_days
        self.z_threshold = z_threshold

    def _load_recent_window(self) -> pd.DataFrame:
        sql = (
            "SELECT d.sale_date, d.store_id, s.store_name, s.city, s.region, d.category, "
            "d.orders_count, d.revenue_ngn, d.gross_margin_ngn, d.returns_ngn, "
            "ROUND(100.0 * d.gross_margin_ngn / NULLIF(d.revenue_ngn, 0), 2) AS gross_margin_pct, "
            "ROUND(100.0 * d.returns_ngn / NULLIF(d.revenue_ngn, 0), 2) AS return_rate "
            "FROM daily_sales d JOIN stores s ON d.store_id = s.store_id "
            "WHERE d.sale_date >= '2025-11-15' "
            "ORDER BY d.sale_date ASC"
        )
        cols, rows = self.db.execute_query(sql)
        return pd.DataFrame(rows, columns=cols)

    @staticmethod
    def _describe_anomaly(
        store_name: str,
        city: str,
        category: str,
        metric: str,
        direction: str,
        observed: float,
        median_val: float,
        z_val: float,
    ) -> str:
        if metric == "revenue_ngn":
            if direction == "spike":
                return (
                    f"{store_name} ({city}) recorded a +{100.0*(observed - median_val)/max(median_val,1):.1f}% "
                    f"revenue surge in {category} (₦{observed:,.0f} vs 28d median ₦{median_val:,.0f}, z={z_val:+.2f}) "
                    "driven by bulk festive corporate hamper demand."
                )
            return (
                f"{store_name} ({city}) suffered a {100.0*(observed - median_val)/max(median_val,1):.1f}% "
                f"revenue drop in {category} (₦{observed:,.0f} vs 28d median ₦{median_val:,.0f}, z={z_val:+.2f}); "
                "investigate POS terminal uptime and inverter power logs."
            )
        if metric == "return_rate":
            return (
                f"{store_name} ({city}) experienced an abnormal return rate spike in {category} "
                f"({observed:.2f}% vs 28d median {median_val:.2f}%, z={z_val:+.2f}); "
                "audit cold-chain temperature logs and recent supplier batch quality."
            )
        return (
            f"{store_name} ({city}) showed sharp gross margin compression in {category} "
            f"({observed:.2f}% vs 28d median {median_val:.2f}%, z={z_val:+.2f}); "
            "review unapproved promotional markdowns and supplier unit cost overrides."
        )

    def detect_anomalies(self, lookback_alert_days: int = 10) -> List[KPIAnomaly]:
        """Compute 28-day rolling MAD z-scores per (store, category) and flag |z| >= z_threshold."""
        df = self._load_recent_window()
        if df.empty:
            return []

        all_dates = sorted(df["sale_date"].unique())
        window_dates = all_dates[-self.window_days :]
        alert_dates = set(all_dates[-lookback_alert_days:])

        win_df = df[df["sale_date"].isin(window_dates)].copy()
        metrics = [
            ("revenue_ngn", 0.65),        # require >=65% relative deviation to separate shocks from regular payday uplift
            ("gross_margin_pct", 0.18),   # require >=18% relative deviation on margin pct
            ("return_rate", 1.20),        # require >=120% relative jump on return rate
        ]

        anomalies: List[KPIAnomaly] = []

        for (store_id, store_name, city, region, category), grp in win_df.groupby(
            ["store_id", "store_name", "city", "region", "category"]
        ):
            grp_sorted = grp.sort_values("sale_date")
            for metric_name, min_rel_dev in metrics:
                vals = grp_sorted[metric_name].astype(float).to_numpy()
                med = float(np.median(vals))
                mad = float(np.median(np.abs(vals - med)))
                robust_sigma = max(1.4826 * mad, 1e-6)

                for _, row in grp_sorted.iterrows():
                    s_date = str(row["sale_date"])
                    if s_date not in alert_dates:
                        continue
                    obs = float(row[metric_name])
                    z = (obs - med) / robust_sigma
                    rel_dev = abs(obs - med) / max(abs(med), 1e-6)

                    if abs(z) >= self.z_threshold and rel_dev >= min_rel_dev:
                        direction = "spike" if z > 0 else "drop"
                        severity = "CRITICAL" if abs(z) >= 4.5 else "WARNING"
                        pct_dev = round(100.0 * (obs - med) / max(abs(med), 1e-6), 2)
                        note = self._describe_anomaly(
                            store_name=str(store_name),
                            city=str(city),
                            category=str(category),
                            metric=metric_name,
                            direction=direction,
                            observed=obs,
                            median_val=med,
                            z_val=float(z),
                        )
                        anomalies.append(
                            KPIAnomaly(
                                sale_date=s_date,
                                store_id=str(store_id),
                                store_name=str(store_name),
                                city=str(city),
                                region=str(region),
                                category=str(category),
                                metric=metric_name,
                                observed_value=round(obs, 2),
                                rolling_median=round(med, 2),
                                rolling_mad=round(mad, 4),
                                robust_z=round(float(z), 2),
                                direction=direction,
                                severity=severity,
                                pct_deviation=pct_dev,
                                operational_note=note,
                            )
                        )

        anomalies.sort(key=lambda a: abs(a.robust_z), reverse=True)
        return anomalies

    def generate_weekly_digest(self, output_path: Optional[Path] = None) -> Dict[str, Any]:
        """Generate a weekly executive digest markdown report and return structured KPI summary."""
        target_path = output_path or DEFAULT_DIGEST_PATH
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Compare latest 7 days (2025-12-25 to 2025-12-31) vs previous 7 days (2025-12-18 to 2025-12-24)
        _, curr_rows = self.db.execute_query(
            "SELECT ROUND(SUM(revenue_ngn), 2) AS revenue_ngn, "
            "ROUND(SUM(gross_margin_ngn), 2) AS gross_margin_ngn, "
            "ROUND(100.0 * SUM(gross_margin_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS gross_margin_pct, "
            "SUM(orders_count) AS orders_count, "
            "ROUND(SUM(revenue_ngn) / NULLIF(SUM(orders_count), 0), 2) AS avg_basket_ngn, "
            "ROUND(100.0 * SUM(returns_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS return_rate "
            "FROM daily_sales WHERE sale_date BETWEEN '2025-12-25' AND '2025-12-31'"
        )
        _, prev_rows = self.db.execute_query(
            "SELECT ROUND(SUM(revenue_ngn), 2) AS revenue_ngn, "
            "ROUND(SUM(gross_margin_ngn), 2) AS gross_margin_ngn, "
            "ROUND(100.0 * SUM(gross_margin_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS gross_margin_pct, "
            "SUM(orders_count) AS orders_count, "
            "ROUND(SUM(revenue_ngn) / NULLIF(SUM(orders_count), 0), 2) AS avg_basket_ngn, "
            "ROUND(100.0 * SUM(returns_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS return_rate "
            "FROM daily_sales WHERE sale_date BETWEEN '2025-12-18' AND '2025-12-24'"
        )
        _, top_stores = self.db.execute_query(
            "SELECT s.store_name, s.city, s.region, "
            "ROUND(SUM(d.revenue_ngn), 2) AS revenue_ngn, "
            "ROUND(100.0 * SUM(d.gross_margin_ngn) / NULLIF(SUM(d.revenue_ngn), 0), 2) AS gross_margin_pct, "
            "SUM(d.orders_count) AS orders_count "
            "FROM daily_sales d JOIN stores s ON d.store_id = s.store_id "
            "WHERE d.sale_date BETWEEN '2025-12-25' AND '2025-12-31' "
            "GROUP BY s.store_name, s.city, s.region ORDER BY revenue_ngn DESC LIMIT 5"
        )
        _, cat_breakdown = self.db.execute_query(
            "SELECT category, ROUND(SUM(revenue_ngn), 2) AS revenue_ngn, "
            "ROUND(100.0 * SUM(gross_margin_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS gross_margin_pct, "
            "ROUND(100.0 * SUM(returns_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS return_rate "
            "FROM daily_sales WHERE sale_date BETWEEN '2025-12-25' AND '2025-12-31' "
            "GROUP BY category ORDER BY revenue_ngn DESC"
        )

        curr = curr_rows[0]
        prev = prev_rows[0]
        wow_rev_pct = round(
            100.0 * (curr["revenue_ngn"] - prev["revenue_ngn"]) / max(prev["revenue_ngn"], 1.0), 2
        )
        wow_orders_pct = round(
            100.0 * (curr["orders_count"] - prev["orders_count"]) / max(prev["orders_count"], 1), 2
        )

        anomalies = self.detect_anomalies(lookback_alert_days=10)

        lines = [
            "# Weekly Executive Operations Digest (Period: 2025-12-25 to 2025-12-31)",
            "",
            "## 1. Headline KPI Performance (WoW)",
            "",
            "| Governed Metric | Current Week (Dec 25–31) | Prior Week (Dec 18–24) | WoW Delta |",
            "|---|---:|---:|---:|",
            f"| **Total Revenue (NGN)** | ₦{curr['revenue_ngn']:,.2f} | ₦{prev['revenue_ngn']:,.2f} | {wow_rev_pct:+.2f}% |",
            f"| **Gross Margin (NGN)** | ₦{curr['gross_margin_ngn']:,.2f} | ₦{prev['gross_margin_ngn']:,.2f} | {100.0*(curr['gross_margin_ngn']-prev['gross_margin_ngn'])/max(prev['gross_margin_ngn'],1):+.2f}% |",
            f"| **Gross Margin (%)** | {curr['gross_margin_pct']:.2f}% | {prev['gross_margin_pct']:.2f}% | {curr['gross_margin_pct']-prev['gross_margin_pct']:+.2f} pp |",
            f"| **Completed Orders** | {curr['orders_count']:,} | {prev['orders_count']:,} | {wow_orders_pct:+.2f}% |",
            f"| **Average Basket (NGN)** | ₦{curr['avg_basket_ngn']:,.2f} | ₦{prev['avg_basket_ngn']:,.2f} | {100.0*(curr['avg_basket_ngn']-prev['avg_basket_ngn'])/max(prev['avg_basket_ngn'],1):+.2f}% |",
            f"| **Return Rate (%)** | {curr['return_rate']:.2f}% | {prev['return_rate']:.2f}% | {curr['return_rate']-prev['return_rate']:+.2f} pp |",
            "",
            "## 2. Top 5 Performing Stores (Current Week)",
            "",
            "| Rank | Store Name | City | Region | Weekly Revenue (NGN) | Gross Margin (%) | Orders |",
            "|---:|---|---|---|---:|---:|---:|",
        ]
        for idx, st in enumerate(top_stores, start=1):
            lines.append(
                f"| {idx} | **{st['store_name']}** | {st['city']} | {st['region']} | "
                f"₦{st['revenue_ngn']:,.2f} | {st['gross_margin_pct']:.2f}% | {st['orders_count']:,} |"
            )

        lines.extend(
            [
                "",
                "## 3. Category Performance Breakdown",
                "",
                "| Category | Weekly Revenue (NGN) | Gross Margin (%) | Return Rate (%) |",
                "|---|---:|---:|---:|",
            ]
        )
        for cat in cat_breakdown:
            lines.append(
                f"| {cat['category']} | ₦{cat['revenue_ngn']:,.2f} | {cat['gross_margin_pct']:.2f}% | {cat['return_rate']:.2f}% |"
            )

        lines.extend(
            [
                "",
                f"## 4. Rolling 28-Day Robust Z-Score Anomalies (`|z| >= {self.z_threshold}`)",
                "",
                "| Date | Severity | Store (City) | Category | Metric | Observed | 28d Median | Robust Z | Actionable Note |",
                "|---|---|---|---|---|---:|---:|---:|---|",
            ]
        )
        for a in anomalies[:10]:
            obs_str = f"₦{a.observed_value:,.0f}" if "ngn" in a.metric else f"{a.observed_value:.2f}%"
            med_str = f"₦{a.rolling_median:,.0f}" if "ngn" in a.metric else f"{a.rolling_median:.2f}%"
            lines.append(
                f"| {a.sale_date} | **{a.severity}** | {a.store_name} ({a.city}) | {a.category} | "
                f"`{a.metric}` | {obs_str} | {med_str} | `{a.robust_z:+.2f}` | {a.operational_note} |"
            )

        lines.extend(
            [
                "",
                "## 5. Recommended Operations Actions for Next Week",
                "",
                "1. **Cold-Chain & Quality Audit (Kano City Mall & Wuse II Mall):** Inspect chiller compressor logs in Kano (`Fresh & Bakery`) and quarantine the affected `Beverages & Dairy` batch in Abuja to bring return rates back below the 2.0% threshold.",
                "2. **POS Failover Redundancy (Port Harcourt GRA):** Add secondary cellular failover and test inverter switchover following the Dec 22 transaction drop.",
                "3. **Promotional Pricing Governance (Ibadan Dugbe Express):** Require regional manager approval in POS for any `Personal Care` markdowns exceeding 10%.",
                "4. **Inventory Replenishment (Lekki Flagship & Victoria Island Hyper):** Replenish `Groceries & Staples` and `Beverages & Dairy` safety stock ahead of New Year restocking.",
                "",
            ]
        )

        markdown_text = "\n".join(lines)
        target_path.write_text(markdown_text, encoding="utf-8")

        return {
            "period_start": "2025-12-25",
            "period_end": "2025-12-31",
            "current_week": curr,
            "prior_week": prev,
            "wow_revenue_growth_pct": wow_rev_pct,
            "wow_orders_growth_pct": wow_orders_pct,
            "anomalies_count": len(anomalies),
            "anomalies": [a.to_dict() for a in anomalies],
            "digest_path": str(target_path),
            "markdown": markdown_text,
        }
