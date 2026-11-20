"""
Data Cleaning Agent - Web App
Run locally:   streamlit run app.py
"""

import os
import tempfile

import pandas as pd
import streamlit as st

from data_cleaning_agent import DataCleaningAgent

st.set_page_config(page_title="Data Cleaning Agent", page_icon="🧹", layout="wide")

st.title("🧹 Data Cleaning Agent")
st.write(
    "Upload a messy **CSV, Excel or JSON** file. The agent fixes blanks, duplicates, "
    "messy text, numbers stored as text, mixed date formats and impossible values, "
    "then gives you a clean file and a report of every change."
)
st.caption(
    "🔒 Your file is processed in memory for this session only and deleted right after cleaning. "
    "Nothing is saved. Please don't upload confidential or personal data you aren't allowed to share."
)

# ---------------- Settings (left sidebar)
with st.sidebar:
    st.header("Settings")
    missing = st.radio(
        "Blank values",
        ["fill", "drop", "keep"],
        format_func={
            "fill": "Fill them (median / 'Unknown')",
            "drop": "Delete rows that have blanks",
            "keep": "Leave them blank",
        }.get,
    )
    outliers = st.radio(
        "Extreme numbers",
        ["flag", "remove", "keep"],
        format_func={
            "flag": "Flag them for review",
            "remove": "Clear them",
            "keep": "Leave them alone",
        }.get,
    )
    st.divider()
    use_ai = st.toggle("Use AI (Claude) to help plan fixes", value=False)
    api_key = ""
    if use_ai:
        api_key = st.text_input(
            "Your Anthropic API key",
            type="password",
            help="Used only for this cleaning run and never saved. Get one at console.anthropic.com",
        )

# ---------------- Upload
uploaded = st.file_uploader("Upload your file", type=["csv", "xlsx", "xls", "json"])

if uploaded is None:
    st.info("👆 Upload a file to get started.")
    st.stop()

ext = os.path.splitext(uploaded.name)[1].lower()
base = os.path.splitext(uploaded.name)[0]

with tempfile.TemporaryDirectory() as tmp:
    in_path = os.path.join(tmp, uploaded.name)
    with open(in_path, "wb") as f:
        f.write(uploaded.getbuffer())

    # Preview the raw data
    agent_preview = DataCleaningAgent(verbose=False)
    try:
        raw = agent_preview.load(in_path)
    except Exception as e:
        st.error(f"Couldn't read this file: {e}")
        st.stop()

    st.subheader("Your data (before)")
    st.caption(f"{len(raw)} rows × {len(raw.columns)} columns")
    st.dataframe(raw.head(100), width="stretch")

    if not st.button("🧹 Clean my data", type="primary"):
        st.stop()

    if use_ai and not api_key:
        st.warning("AI is on but no API key was entered, so the agent will use its built-in rules only.")
    with st.spinner("Cleaning..."):
        agent = DataCleaningAgent(
            use_ai=use_ai and bool(api_key), missing_strategy=missing, outlier_action=outliers,
            api_key=api_key or None, verbose=False,
        )
        try:
            clean = agent.run(in_path, out_dir=tmp)
        except Exception as e:
            st.error(f"Something went wrong while cleaning: {e}")
            st.stop()

    with open(os.path.join(tmp, f"{base}_cleaning_report.md"), encoding="utf-8") as f:
        report = f.read()

# ---------------- Results
st.success("Done! Your data is clean.")

c1, c2, c3 = st.columns(3)
c1.metric("Rows", len(clean), len(clean) - len(raw))
c2.metric("Columns", len(clean.columns), len(clean.columns) - len(raw.columns))
c3.metric("Fixes made", len(agent.log))

st.subheader("Clean data (after)")
st.dataframe(clean, width="stretch")

d1, d2 = st.columns(2)
d1.download_button(
    "⬇️ Download clean CSV",
    clean.to_csv(index=False).encode("utf-8"),
    file_name=f"{base}_cleaned.csv",
    mime="text/csv",
    type="primary",
)
d2.download_button(
    "⬇️ Download cleaning report",
    report.encode("utf-8"),
    file_name=f"{base}_cleaning_report.md",
    mime="text/markdown",
)

with st.expander("📋 See every fix the agent made", expanded=True):
    for step, msg in agent.log:
        st.markdown(f"- **{step}**: {msg}")
