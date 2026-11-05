"""SLT Customer Payment Behaviour & Collection Prioritization — Phase 1 dashboard.

To run from the original SLT_Payment_Project folder:
    python -m pip install streamlit pandas openpyxl
    python 12_find_defaulting_customers.py
    python -m streamlit run dashboard.py

Uses the 200-account Excel workbook plus the operational CSV created by
12_find_defaulting_customers.py. Earlier ML results (from a different synthetic
CSV dataset) are shown separately and must not be presented as this workbook's
model performance.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_DIR = Path(__file__).resolve().parent
DATA_FILE = PROJECT_DIR / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"
RESULT_FILE = PROJECT_DIR / "outputs" / "defaulting_customers.csv"
ML_RESULTS_FILE = PROJECT_DIR / "outputs" / "model_test_results.csv"
SNAPSHOT_LABEL = "26 October 2026 (dummy workbook snapshot)"

st.set_page_config(
    page_title="SLT | Customer Payment Risk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .stApp { background: #f6f8fc; color: #15243b; }
      h1, h2, h3 { color: #102a43; letter-spacing: -0.025em; }
      [data-testid="stMetric"] {
          background: #ffffff; border: 1px solid #e2e8f0;
          border-radius: 14px; padding: 14px 18px;
          box-shadow: 0px 2px 10px rgba(25,49,83,0.04);
      }
      [data-testid="stMetricLabel"] { color: #4b6177; }
      .hero { background: linear-gradient(115deg, #092b4a, #075a78);
              color: white; padding: 26px 30px; border-radius: 18px;
              margin-bottom: 18px; }
      .hero .eyebrow { color:#abe1df; text-transform: uppercase;
                       letter-spacing: .15em; font-size: 12px; font-weight: 700; }
      .hero h1 { color: white !important; margin: 4px 0; font-size: 31px; }
      .hero p { color:#d8eef4; margin: 4px 0 0; font-size: 14px; }
      .helpnote { background: #eef5fb; border-left:4px solid #188c9c;
                  padding: 12px 16px; border-radius: 8px; color:#294862; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">SLT · Customer Payment Behaviour · Phase 1</div>
      <h1>Customer Payment Risk Dashboard</h1>
      <p>Overdue account identification · Collection follow-up · Synthetic demo data</p>
    </div>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def load_workbook(path: str, last_modified_ns: int) -> pd.DataFrame:
    """Read workbook summary to show all assessed accounts; not used for prediction."""
    return pd.read_excel(path, sheet_name="Behaviour_Summary", engine="openpyxl")


@st.cache_data(show_spinner=False)
def load_csv(path: str, last_modified_ns: int) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"account_no": "string", "customer_id": "string"})


def show_money(value: float) -> str:
    return f"LKR {value:,.2f}"


if not RESULT_FILE.exists():
    st.error("The defaulting-customer CSV is missing.")
    st.code("python 12_find_defaulting_customers.py", language="powershell")
    st.info("Run the script in the original SLT_Payment_Project folder, then refresh this page.")
    st.stop()

try:
    overdue = load_csv(str(RESULT_FILE), RESULT_FILE.stat().st_mtime_ns)
except Exception as exc:
    st.error(f"Could not read {RESULT_FILE.name}: {exc}")
    st.stop()

required = ["account_no", "customer_id", "unpaid_past_due_bills", "current_overdue_days", "current_outstanding_LKR"]
missing = [c for c in required if c not in overdue.columns]
if missing:
    st.error(f"The overdue CSV is missing required columns: {missing}")
    st.stop()

for column in ["unpaid_past_due_bills", "current_overdue_days", "current_outstanding_LKR"]:
    overdue[column] = pd.to_numeric(overdue[column], errors="coerce")

valid = (
    overdue["unpaid_past_due_bills"].gt(0)
    & overdue["current_overdue_days"].gt(0)
    & overdue["current_outstanding_LKR"].gt(0)
)
if overdue[required].isna().any().any() or overdue["account_no"].duplicated().any() or not valid.all():
    st.error("Output validation failed: missing/duplicate accounts or records that do not satisfy the overdue rule.")
    st.info("Rerun 12_find_defaulting_customers.py after checking the workbook.")
    st.stop()

customers = None
if DATA_FILE.exists():
    try:
        customers = load_workbook(str(DATA_FILE), DATA_FILE.stat().st_mtime_ns)
    except Exception as exc:
        st.warning(f"The workbook could not be opened ({exc}). Showing overdue CSV results only.")
else:
    st.warning("Excel workbook not found. Add data/SLT_Payment_Behaviour_Dummy_Data.xlsx to show total assessed accounts.")

assessed = len(customers) if customers is not None else None

with st.sidebar:
    st.markdown("### About this dashboard")
    st.caption("Phase 1 — rule-based identification from the supplied dummy workbook")
    st.info("**Current overdue rule:** unpaid past-due bills > 0, outstanding balance > 0, and overdue days > 0.")
    st.caption("These are overdue accounts, not officially confirmed SLT defaults.")
    st.markdown("---")
    st.markdown("**Dataset**")
    st.caption("SLT_Payment_Behaviour_Dummy_Data.xlsx")
    st.markdown("**Snapshot**")
    st.caption(SNAPSHOT_LABEL)
    if st.button("Refresh saved data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

overview_tab, accounts_tab, insights_tab, ml_tab = st.tabs(
    ["Overview", "Overdue accounts", "Collection insights", "Earlier ML results"]
)

with overview_tab:
    a, b, c, d = st.columns(4)
    a.metric("Accounts assessed", f"{assessed:,}" if assessed is not None else "Unavailable")
    b.metric("Currently overdue", f"{len(overdue):,}")
    c.metric("Overdue share", f"{len(overdue)/assessed:.1%}" if assessed else "Unavailable")
    d.metric("Combined outstanding", show_money(overdue["current_outstanding_LKR"].sum()))

    st.markdown("### At a glance")
    left, right = st.columns(2)
    with left:
        st.markdown("**Overdue accounts by customer type**")
        if "customer_type" in overdue.columns:
            counts = overdue["customer_type"].fillna("Unknown").value_counts().rename_axis("Customer type")
            st.bar_chart(counts)
        else:
            st.caption("Customer type is not available in the output CSV.")
    with right:
        st.markdown("**Overdue duration distribution**")
        days = overdue["current_overdue_days"]
        age_group = pd.cut(
            days, bins=[0, 30, 60, 90, 180, float("inf")],
            labels=["1–30", "31–60", "61–90", "91–180", "180+"],
            include_lowest=True,
        )
        age_counts = age_group.value_counts().reindex(["1–30", "31–60", "61–90", "91–180", "180+"], fill_value=0)
        age_counts.index.name = "Overdue days"
        st.bar_chart(age_counts)

    st.markdown(
        "<div class='helpnote'><b>Interpretation:</b> This dashboard describes accounts that are already overdue. "
        "The official SLT definition of <i>default</i> still needs approval. No future-default model has "
        "been trained on the 200-account workbook.</div>",
        unsafe_allow_html=True,
    )

with accounts_tab:
    st.markdown("### Explore identified overdue accounts")
    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input("Search account or customer ID", placeholder="e.g., C10337")
    if "customer_type" in overdue.columns:
        customer_types = sorted(overdue["customer_type"].dropna().astype(str).unique().tolist())
        chosen_types = f2.multiselect("Customer types", customer_types, default=customer_types)
    else:
        chosen_types = []
    max_days = max(1, int(overdue["current_overdue_days"].max()))
    min_days = f3.number_input("Minimum overdue days", min_value=1, max_value=max_days, value=1)
    filtered = overdue.copy()
    if search.strip():
        match = (
            filtered["customer_id"].astype(str).str.contains(search.strip(), case=False, regex=False, na=False)
            | filtered["account_no"].astype(str).str.contains(search.strip(), case=False, regex=False, na=False)
        )
        filtered = filtered.loc[match]
    if "customer_type" in overdue.columns:
        filtered = filtered[filtered["customer_type"].astype(str).isin(chosen_types)]
    filtered = filtered[filtered["current_overdue_days"] >= min_days]
    filtered = filtered.sort_values(["current_overdue_days", "current_outstanding_LKR"], ascending=False)

    st.caption(f"Showing {len(filtered)} of {len(overdue)} overdue accounts · Outstanding: {show_money(filtered['current_outstanding_LKR'].sum())}")
    preferred = [
        "account_no", "customer_id", "customer_type", "unpaid_past_due_bills",
        "current_overdue_days", "current_outstanding_LKR", "suspensions",
        "collection_actions", "agent_classification", "recommended_action",
    ]
    st.dataframe(
        filtered[[col for col in preferred if col in filtered.columns]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "current_outstanding_LKR": st.column_config.NumberColumn("Outstanding (LKR)", format="%.2f"),
            "current_overdue_days": st.column_config.NumberColumn("Overdue days", format="%d"),
        },
    )
    st.download_button(
        "Download filtered overdue accounts (CSV)",
        filtered.to_csv(index=False).encode("utf-8-sig"),
        file_name="filtered_overdue_accounts.csv",
        mime="text/csv",
        use_container_width=True,
    )

with insights_tab:
    st.markdown("### Collection insights")
    st.caption("Descriptive analysis only — based on saved overdue accounts")
    x, y = st.columns(2)
    with x:
        st.markdown("**Top 10 accounts by outstanding balance**")
        top = overdue.nlargest(10, "current_outstanding_LKR")
        st.bar_chart(top.set_index("customer_id")["current_outstanding_LKR"])
    with y:
        st.markdown("**Suggested actions in operational output**")
        if "recommended_action" in overdue.columns:
            st.bar_chart(overdue["recommended_action"].fillna("Unspecified").value_counts())
        else:
            st.caption("No recommended-action column was found.")
    st.markdown("**Most overdue accounts**")
    cols = [c for c in ["customer_id", "account_no", "current_overdue_days", "current_outstanding_LKR", "unpaid_past_due_bills"] if c in overdue.columns]
    st.dataframe(overdue.sort_values(["current_overdue_days", "current_outstanding_LKR"], ascending=False).head(10)[cols], hide_index=True, use_container_width=True)

    if customers is not None and "designed_profile (ground truth)" in customers.columns:
        st.markdown("**Designed behaviour profiles — validation only**")
        st.caption("The workbook supplies these synthetic ground-truth profiles; they are not model predictions or verified real customer behaviour.")
        profile_lookup = customers[["account_no", "designed_profile (ground truth)"]].copy()
        profile_lookup["account_no"] = profile_lookup["account_no"].astype(str)
        merged = overdue[["account_no"]].merge(profile_lookup, on="account_no", how="left")
        counts = merged["designed_profile (ground truth)"].fillna("Unknown").value_counts()
        st.bar_chart(counts)

with ml_tab:
    st.markdown("### Earlier machine-learning experiment")
    st.warning(
        "These Random Forest metrics belong to the earlier 10,000-customer synthetic CSV pipeline "
        "(invoice-level unpaid-after-30-days prediction). They do NOT evaluate this 200-account workbook or the 41-account overdue list."
    )
    if ML_RESULTS_FILE.exists():
        try:
            results = pd.read_csv(ML_RESULTS_FILE)
            st.dataframe(results, hide_index=True, use_container_width=True)
            st.caption("Source: outputs/model_test_results.csv — historical model test results")
        except Exception as exc:
            st.warning(f"Could not read earlier test results: {exc}")
    else:
        st.info("No earlier model test result file is available. This tab is optional.")
    st.markdown("**Next project steps**")
    st.markdown(
        "1. Confirm SLT's official default definition and action thresholds.\n"
        "2. Build dated, historical features and future-outcome labels from the new workbook.\n"
        "3. Evaluate a suitable prediction model on a held-out time period.\n"
        "4. Add model probability and actionable priority only after validation."
    )

st.caption(
    "SLT Customer Payment Risk Dashboard | Phase 1 | "
    "Analysis based on the provided dummy dataset"
)
