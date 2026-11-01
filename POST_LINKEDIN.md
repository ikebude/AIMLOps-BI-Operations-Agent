# LinkedIn Post — BI Operations Agent

Most text-to-SQL demos I see would be risky on a real company database.

If you connect an LLM directly to a client's database without an AST guardrail or a governed semantic layer:
1. It invents incompatible formulas for core metrics (e.g. gross margin vs contribution margin).
2. It is vulnerable to prompt injection, schema leaking, and table drops.
3. It cannot tell the difference between normal seasonality and an operational anomaly.

So I built **BI Operations Agent** (`AIMLOps-BI-Operations-Agent`), an open-source, read-only analytics assistant: plain-English questions, a governed YAML semantic layer, a SQL guard, and KPI monitoring.

Tested on a 2-year synthetic dataset of a Nigerian retail chain (10 stores across Lagos, Abuja, Port Harcourt, Ibadan, Kano, Enugu; ₦10.15B GMV across 2.15M orders):

- **96.0% Execution Accuracy (48/50)** on the in-sample benchmark across 5 domains (revenue growth, margins, regional rankings, salary-week dynamics, returns & payroll), and an honest **56.0% (14/25)** on 25 reworded held-out questions.
- **12/12 adversarial prompts blocked:** zero unsafe queries executed (`DROP`, `DELETE`, `UPDATE`, comments, and semicolons are blocked by the sqlglot AST guard before hitting SQLite).
- **Latency:** 18.8 ms median (39.1 ms p95; machine-dependent).
- **Anomaly monitoring:** rolling 28-day Median Absolute Deviation (MAD z-scores) to flag store-level revenue drops and margin spikes, auto-generating a weekly executive digest.

The numbers come from the deterministic compiler; the 56% held-out score is the one to take seriously, and improving it is the next step. An optional LLM provider exists but is not benchmarked.

Full code, benchmark suite, and API: https://github.com/ikebude/AIMLOps-BI-Operations-Agent

How does your team safeguard text-to-SQL queries in production?

#BusinessIntelligence #DataEngineering #MachineLearning #Python #AI #DataAnalytics
