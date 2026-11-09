"""
app.py — Streamlit UI (thin wrapper around PortfolioAgent.answer)

Run:
    streamlit run app.py
"""

import os
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).parent.resolve()

st.set_page_config(
    page_title="PL Capital — Portfolio Analytics Agent",
    page_icon="📊",
    layout="wide",
)

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Configuration")

    api_key = st.text_input(
        "Gemini API Key",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
    )

    if st.button("🛠️ Setup Database"):
        with st.spinner("Building portfolio.db …"):
            try:
                from setup_db import setup_database
                conn = setup_database(HERE / "portfolio.db", force=True)
                conn.close()
                st.success("Database ready!")
            except Exception as e:
                st.error(str(e))

    st.divider()
    st.subheader("📋 Sample Questions")
    samples = [
        "How many portfolios do we have?",
        "What are the names of all active portfolios?",
        "Which securities are in the Technology sector?",
        "What is the total AUM for high risk portfolios?",
        "Show top 5 holdings by cost basis in Growth Equity Fund",
        "What is the average price of securities in each sector?",
        "What are the sector exposures for the Tech Innovation Fund?",
        "Calculate the sector exposure breakdown for international equity",
    ]
    for s in samples:
        if st.button(s, use_container_width=True):
            st.session_state["prefill"] = s

    st.divider()
    st.caption("PL Capital AI Engineer Assessment 2026")


# ── Agent (cached per API key) ─────────────────────────────────────────────
@st.cache_resource
def _get_agent(key: str):
    from agent.portfolio_agent import PortfolioAgent
    return PortfolioAgent(db_path=HERE / "portfolio.db", api_key=key)


# ── Main chat ──────────────────────────────────────────────────────────────
st.title("📊 PL Capital Portfolio Analytics Agent")
st.caption("Ask any portfolio question in plain English. The agent picks SQL or Sector Exposure automatically.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prefill  = st.session_state.pop("prefill", None)
user_in  = st.chat_input("Ask a portfolio question…")
question = prefill or user_in

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    if not api_key:
        st.error("Enter your Gemini API key in the sidebar.")
        st.stop()

    db_path = HERE / "portfolio.db"
    if not db_path.exists():
        st.error("Database not found. Click **Setup Database** in the sidebar.")
        st.stop()

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                agent  = _get_agent(api_key)
                result = agent.answer(question)
                answer = result.get("answer", "")
                tool   = result.get("tool_used", "")
                tr     = result.get("tool_result") or {}

                st.markdown(answer)
                st.caption(f"{'🟢' if tool == 'sql_query' else '🔵'} Tool: **{tool}**")

                # Show generated SQL
                if tr.get("sql"):
                    with st.expander("🗄️ Generated SQL"):
                        st.code(tr["sql"], language="sql")

                # Show exposure chart
                if tool == "exposure_calculator" and tr.get("equity_exposures"):
                    st.subheader("Equity Sector Exposures")
                    st.bar_chart(tr["equity_exposures"])

                st.session_state.messages.append(
                    {"role": "assistant", "content": answer}
                )
            except Exception as e:
                st.error(str(e))
