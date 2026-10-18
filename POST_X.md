# X (Twitter) Thread — BI Operations Agent

**1/6**
Connecting an LLM directly to a database without an AST guardrail is a massive liability.

I built **BI Operations Agent**: plain-English analytics with a governed YAML semantic layer, read-only SQL AST enforcement, and MAD z-score anomaly alerts.

Repo: https://github.com/ikebude/AIMLOps-BI-Operations-Agent

![Hero](img/hero.png)

**2/6**
Benchmark results across a 50-question enterprise test suite:
- Execution accuracy: **96.0% (48/50)**
- Adversarial attacks blocked: **12/12 (100%)**
- Unsafe SQL executed: **0**
- Median latency: **48.4 ms** (p95: 202.2 ms)

**3/6**
Why a Semantic Layer?
Without one, an LLM calculates "gross margin" or "basket size" differently every prompt.
Our YAML layer defines exact dimension joins and formula expressions so answers match official management reporting.

**4/6**
Security first:
Our SQL guard inspects the AST before execution.
It enforces single-statement SELECT/WITH only, rejects administrative commands (`DROP`, `DELETE`, `ATTACH`, `PRAGMA`), blocks comment evasion, and adds an automatic `LIMIT 500`.

**5/6**
Beyond Chat: Proactive KPI Alerts
It runs rolling 28-day Median Absolute Deviation (MAD robust z-scores) on daily revenue and return rates.
When $|z| \ge 2.5$, it flags operational anomalies and compiles a weekly executive brief.

**6/6**
Runs offline with local embeddings/compiler or connected to Claude/OpenAI/Ollama.
FastAPI + Streamlit + 20 passing unit tests in CI.
Check it out: https://github.com/ikebude/AIMLOps-BI-Operations-Agent
