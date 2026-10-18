# Nigerian Retail Operations Dataset (`data/`)

## Overview & Provenance
- **Source:** Synthetic deterministic generator (`python -m src.bi_agent.seed`, seed `42`).
- **Licence:** MIT (100% synthetic; safe for commercial and public portfolio use).
- **Coverage:** 24 months (`2024-01-01` to `2025-12-31`, 731 calendar days) across 10 retail stores in 6 Nigerian cities (`Lagos`, `Abuja`, `Port Harcourt`, `Ibadan`, `Kano`, `Enugu`) and 5 geopolitical zones (`South-West`, `North-Central`, `South-South`, `North-West`, `South-East`).
- **Volume:**
  - `stores`: 10 store locations
  - `products`: 25 SKUs across 5 retail categories (`Groceries & Staples`, `Beverages & Dairy`, `Personal Care`, `Household & Cleaning`, `Fresh & Bakery`)
  - `staff`: 80 active store employees across 5 operational roles
  - `daily_sales`: 36,550 daily store-category records with December festive seasonality, 25th–30th salary-week spikes, and 6 injected late-December 2025 operational anomalies.

## Regenerating the Data
```bash
python -m src.bi_agent.seed
```
