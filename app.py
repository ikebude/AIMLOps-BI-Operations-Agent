"""Streamlit Interactive Dashboard for BI Operations Agent."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from src.bi_agent.engine import BIOperationsEngine
from src.bi_agent.monitor import KPIAnomalyMonitor
from src.bi_agent.seed import ROOT_DIR, RetailDatabase, seed_all
from src.bi_agent.sql_guard import DEFAULT_SEMANTIC_LAYER_PATH

st.set_page_config(
    page_title="BI Operations Agent — Nigerian Retail Intelligence",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; }
    div[data-testid="stMetric"] {
        background-color: #121316;
        border: 1px solid #27272A;
        border-top: 4px solid #06B6D4;
        padding: 12px 16px;
        border-radius: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def init_resources():
    seed_all()
    db = RetailDatabase()
    engine = BIOperationsEngine(db=db)
    monitor = KPIAnomalyMonitor(db=db)
    with open(DEFAULT_SEMANTIC_LAYER_PATH, "r", encoding="utf-8") as f:
        semantic_cfg = yaml.safe_load(f)
    return db, engine, monitor, semantic_cfg


db, engine, monitor, semantic_cfg = init_resources()

st.title("📊 BI Operations Agent")
st.caption(
    "Governed Plain-English NL-to-SQL · Read-Only SQL Guard · Rolling 28-Day MAD KPI Anomaly Detection · Weekly Executive Digest"
)

# Top KPI Ribbon from real seeded dataset
_, kpi_rows = db.execute_query(
    "SELECT ROUND(SUM(revenue_ngn), 2) AS rev, "
    "ROUND(SUM(gross_margin_ngn), 2) AS gm, "
    "ROUND(100.0 * SUM(gross_margin_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS gm_pct, "
    "SUM(orders_count) AS orders, "
    "ROUND(SUM(revenue_ngn) / NULLIF(SUM(orders_count), 0), 2) AS basket, "
    "ROUND(100.0 * SUM(returns_ngn) / NULLIF(SUM(revenue_ngn), 0), 2) AS ret_rate "
    "FROM daily_sales WHERE STRFTIME('%Y', sale_date) = '2025'"
)
k = kpi_rows[0]
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("2025 Revenue (NGN)", f"₦{k['rev']/1e9:.2f}B")
c2.metric("2025 Gross Margin", f"₦{k['gm']/1e6:.1f}M")
c3.metric("Gross Margin (%)", f"{k['gm_pct']:.2f}%")
c4.metric("2025 Completed Orders", f"{k['orders']:,}")
c5.metric("Avg Basket Size", f"₦{k['basket']:,.0f}")
c6.metric("Return Rate (%)", f"{k['ret_rate']:.2f}%")

tab_chat, tab_anomalies, tab_digest, tab_semantic = st.tabs(
    [
        "💬 Ask Your Data (NL-to-SQL)",
        "🚨 KPI Anomaly Monitor (28d MAD)",
        "📋 Weekly Executive Digest",
        "🛡️ Governed Semantic Layer",
    ]
)

with tab_chat:
    st.subheader("Ask a Plain-English Business Question")
    sample_questions = [
        "What are the top 5 stores by revenue in 2025?",
        "Show year-over-year revenue growth by city.",
        "Compare salary week vs off-week revenue and orders in 2025.",
        "Compare festive December vs non-festive months in 2025.",
        "Show gross margin percentage by category in 2025.",
        "Show revenue per staff member by store in 2025.",
        "Show return rate by store in December 2025.",
        "DROP TABLE stores; -- (Test Read-Only SQL Guard)",
    ]
    selected_sample = st.selectbox("Sample questions:", sample_questions)
    user_q = st.text_input("Or type your own question:", value=selected_sample)
    show_sql = st.checkbox("Show Governed SQL & Guard Metadata", value=True)

    if st.button("Run Query", type="primary") or user_q:
        resp = engine.ask(user_q)
        if not resp["safe"]:
            st.error(f"🛡️ **Blocked by Read-Only SQL Guard:** {resp['guard_reason']}")
            if show_sql:
                st.code(resp["sql"], language="sql")
        else:
            st.success(f"{resp['summary_text']}  *(Latency: {resp['latency_ms']:.2f} ms)*")
            if show_sql:
                st.code(resp["sql"], language="sql")
            df_res = pd.DataFrame(resp["rows"], columns=resp["columns"])
            col_table, col_chart = st.columns([1.15, 1.0])
            with col_table:
                st.dataframe(df_res, use_container_width=True)
            with col_chart:
                cspec = resp["chart_spec"]
                if cspec["type"] in ("bar", "line") and cspec["x"] and cspec["y"] and len(df_res) > 1:
                    chart_df = df_res.set_index(cspec["x"])[[cspec["y"]]]
                    if cspec["type"] == "line":
                        st.line_chart(chart_df)
                    else:
                        st.bar_chart(chart_df)

with tab_anomalies:
    st.subheader("Rolling 28-Day Robust Z-Score (MAD) Anomaly Monitor")
    z_thresh = st.slider("Robust Z-Score Threshold (|z| >= threshold)", 2.0, 5.0, 2.5, 0.25)
    mon_custom = KPIAnomalyMonitor(db=db, window_days=28, z_threshold=z_thresh)
    anoms = mon_custom.detect_anomalies(lookback_alert_days=10)
    st.write(f"Flagged **{len(anoms)}** store-category anomalies in the latest monitoring window:")
    if anoms:
        st.dataframe(pd.DataFrame([a.to_dict() for a in anoms]), use_container_width=True)
    anom_img = ROOT_DIR / "img" / "kpi_anomalies.png"
    if anom_img.exists():
        st.image(str(anom_img), caption="Rolling 28-Day Robust Z-Score Anomaly Chart", use_container_width=True)

with tab_digest:
    st.subheader("Automated Weekly Executive Digest")
    digest_data = monitor.generate_weekly_digest()
    st.markdown(digest_data["markdown"])

with tab_semantic:
    st.subheader("Governed Semantic Layer (`config/semantic_layer.yaml`)")
    col_m, col_t = st.columns(2)
    with col_m:
        st.markdown("#### Approved Metrics")
        st.json(semantic_cfg.get("metrics", {}))
        st.markdown("#### Approved Dimensions")
        st.json(semantic_cfg.get("dimensions", {}))
    with col_t:
        st.markdown("#### Allow-Listed Tables & Schemas")
        st.json(semantic_cfg.get("tables", {}))
