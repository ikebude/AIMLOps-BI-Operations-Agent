# Weekly Executive Operations Digest (Period: 2025-12-25 to 2025-12-31)

## 1. Headline KPI Performance (WoW)

| Governed Metric | Current Week (Dec 25–31) | Prior Week (Dec 18–24) | WoW Delta |
|---|---:|---:|---:|
| **Total Revenue (NGN)** | ₦178,774,398.74 | ₦143,065,349.03 | +24.96% |
| **Gross Margin (NGN)** | ₦50,927,622.23 | ₦40,998,116.43 | +24.22% |
| **Gross Margin (%)** | 28.49% | 28.66% | -0.17 pp |
| **Completed Orders** | 34,934 | 28,033 | +24.62% |
| **Average Basket (NGN)** | ₦5,117.49 | ₦5,103.46 | +0.27% |
| **Return Rate (%)** | 1.91% | 1.83% | +0.08 pp |

## 2. Top 5 Performing Stores (Current Week)

| Rank | Store Name | City | Region | Weekly Revenue (NGN) | Gross Margin (%) | Orders |
|---:|---|---|---|---:|---:|---:|
| 1 | **Lekki Flagship** | Lagos | South-West | ₦25,464,698.51 | 28.38% | 4,803 |
| 2 | **Victoria Island Hyper** | Lagos | South-West | ₦24,101,233.40 | 28.42% | 4,550 |
| 3 | **Wuse II Mall** | Abuja | North-Central | ₦21,699,293.19 | 28.76% | 4,097 |
| 4 | **Ikeja City Mall** | Lagos | South-West | ₦19,715,017.87 | 28.74% | 3,716 |
| 5 | **Port Harcourt GRA** | Port Harcourt | South-South | ₦16,967,349.83 | 28.49% | 3,485 |

## 3. Category Performance Breakdown

| Category | Weekly Revenue (NGN) | Gross Margin (%) | Return Rate (%) |
|---|---:|---:|---:|
| Groceries & Staples | ₦53,448,715.02 | 22.14% | 1.79% |
| Personal Care | ₦37,656,171.71 | 33.73% | 1.89% |
| Beverages & Dairy | ₦34,566,315.27 | 26.04% | 2.07% |
| Household & Cleaning | ₦30,064,954.54 | 29.00% | 1.84% |
| Fresh & Bakery | ₦23,038,242.20 | 37.64% | 2.04% |

## 4. Rolling 28-Day Robust Z-Score Anomalies (`|z| >= 2.5`)

| Date | Severity | Store (City) | Category | Metric | Observed | 28d Median | Robust Z | Actionable Note |
|---|---|---|---|---|---:|---:|---:|---|
| 2025-12-30 | **CRITICAL** | Wuse II Mall (Abuja) | Beverages & Dairy | `return_rate` | 17.20% | 1.78% | `+67.10` | Wuse II Mall (Abuja) experienced an abnormal return rate spike in Beverages & Dairy (17.20% vs 28d median 1.78%, z=+67.10); audit cold-chain temperature logs and recent supplier batch quality. |
| 2025-12-27 | **CRITICAL** | Kano City Mall (Kano) | Fresh & Bakery | `return_rate` | 19.50% | 2.01% | `+32.76` | Kano City Mall (Kano) experienced an abnormal return rate spike in Fresh & Bakery (19.50% vs 28d median 2.01%, z=+32.76); audit cold-chain temperature logs and recent supplier batch quality. |
| 2025-12-26 | **CRITICAL** | Ibadan Dugbe Express (Ibadan) | Personal Care | `gross_margin_pct` | 8.20% | 33.94% | `-28.69` | Ibadan Dugbe Express (Ibadan) showed sharp gross margin compression in Personal Care (8.20% vs 28d median 33.94%, z=-28.69); review unapproved promotional markdowns and supplier unit cost overrides. |
| 2025-12-27 | **CRITICAL** | Kano City Mall (Kano) | Fresh & Bakery | `gross_margin_pct` | 11.50% | 37.84% | `-27.76` | Kano City Mall (Kano) showed sharp gross margin compression in Fresh & Bakery (11.50% vs 28d median 37.84%, z=-27.76); review unapproved promotional markdowns and supplier unit cost overrides. |
| 2025-12-28 | **CRITICAL** | Lekki Flagship (Lagos) | Groceries & Staples | `revenue_ngn` | ₦2,722,644 | ₦850,769 | `+12.53` | Lekki Flagship (Lagos) recorded a +220.0% revenue surge in Groceries & Staples (₦2,722,644 vs 28d median ₦850,769, z=+12.53) driven by bulk festive corporate hamper demand. |
| 2025-12-29 | **CRITICAL** | Victoria Island Hyper (Lagos) | Beverages & Dairy | `revenue_ngn` | ₦1,550,944 | ₦537,434 | `+10.32` | Victoria Island Hyper (Lagos) recorded a +188.6% revenue surge in Beverages & Dairy (₦1,550,944 vs 28d median ₦537,434, z=+10.32) driven by bulk festive corporate hamper demand. |
| 2025-12-22 | **CRITICAL** | Port Harcourt GRA (Port Harcourt) | Groceries & Staples | `revenue_ngn` | ₦126,696 | ₦606,092 | `-5.74` | Port Harcourt GRA (Port Harcourt) suffered a -79.1% revenue drop in Groceries & Staples (₦126,696 vs 28d median ₦606,092, z=-5.74); investigate POS terminal uptime and inverter power logs. |

## 5. Recommended Operations Actions for Next Week

1. **Cold-Chain & Quality Audit (Kano City Mall & Wuse II Mall):** Inspect chiller compressor logs in Kano (`Fresh & Bakery`) and quarantine the affected `Beverages & Dairy` batch in Abuja to bring return rates back below the 2.0% threshold.
2. **POS Failover Redundancy (Port Harcourt GRA):** Add secondary cellular failover and test inverter switchover following the Dec 22 transaction drop.
3. **Promotional Pricing Governance (Ibadan Dugbe Express):** Require regional manager approval in POS for any `Personal Care` markdowns exceeding 10%.
4. **Inventory Replenishment (Lekki Flagship & Victoria Island Hyper):** Replenish `Groceries & Staples` and `Beverages & Dairy` safety stock ahead of New Year restocking.
