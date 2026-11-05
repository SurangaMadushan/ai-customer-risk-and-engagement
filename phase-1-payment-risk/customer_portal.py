
"""SLT Customer Payment Intelligence Portal.

Run the analysis scripts before starting the website:
    python 13_classify_payment_behaviour.py
    python 14_collection_recommendations.py
    python 15_creditworthiness_analysis.py
    python -m streamlit run customer_portal.py

Local demonstration using approved dummy data only.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st


# ============================================================
# 1. PROJECT FILE PATHS
# ============================================================

ROOT = Path(__file__).resolve().parent

WORKBOOK = ROOT / "data" / "SLT_Payment_Behaviour_Dummy_Data.xlsx"

SCORES = ROOT / "outputs" / "customer_creditworthiness.csv"

RECOMMENDATIONS = ROOT / "outputs" / "collection_recommendations.csv"

FOLLOWUP = ROOT / "outputs" / "collection_followup_list.csv"


# ============================================================
# 2. STREAMLIT PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SLT | Payment Intelligence Portal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# 3. CUSTOM WEBSITE DESIGN
# ============================================================

st.markdown("""
<style>
.stApp {
    background-color: #f4f7fb;
    color: #152840;
}

h1, h2, h3 {
    color: #17344f;
}

[data-testid="stSidebar"] {
    background-color: #eaf1f6;
}

[data-testid="stMetric"] {
    background-color: white;
    padding: 15px 18px;
    border: 1px solid #e0e7ef;
    border-radius: 14px;
}

[data-testid="stMetricLabel"] {
    font-weight: 600;
    color: #51647d;
}

.banner {
    background: linear-gradient(120deg, #07314e, #087a8b);
    border-radius: 18px;
    padding: 25px 30px;
    margin-bottom: 20px;
    color: white;
}

.banner h1 {
    color: #fff !important;
    margin: 2px 0 7px;
    font-size: 30px;
}

.banner p {
    color: #d5f1f2;
    margin: 0;
    font-size: 14px;
}
</style>
""", unsafe_allow_html=True)


# ============================================================
# 4. HELPER FUNCTIONS
# ============================================================

def id_string(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def currency(value: object) -> str:
    return f"LKR {float(value):,.2f}"


def as_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


# ============================================================
# 5. LOAD CREDITWORTHINESS RESULTS
# ============================================================

@st.cache_data(show_spinner=False)
def read_scores(path: str, mtime: int) -> pd.DataFrame:

    result = pd.read_csv(
        path,
        dtype={
            "account_no": "string",
            "customer_id": "string",
        },
    )

    required = {
        "account_no",
        "customer_id",
        "payment_reliability_score",
        "reliability_band",
        "behaviour_classification",
        "current_outstanding_LKR",
        "current_overdue_days",
        "unpaid_past_due_bills",
        "score_reason",
    }

    missing = required - set(result.columns)

    if missing:
        raise ValueError(
            f"Creditworthiness CSV missing columns: {sorted(missing)}"
        )

    if result["account_no"].duplicated().any():
        raise ValueError("Duplicate account numbers found.")

    result["account_no"] = id_string(result["account_no"])
    result["customer_id"] = id_string(result["customer_id"])

    return result


# ============================================================
# 6. LOAD COLLECTION RECOMMENDATIONS
# ============================================================

@st.cache_data(show_spinner=False)
def read_recommendations(path: str, mtime: int) -> pd.DataFrame:

    result = pd.read_csv(
        path,
        dtype={
            "account_no": "string",
            "customer_id": "string",
        },
    )

    required = {
        "account_no",
        "customer_id",
        "recommended_action",
        "recommendation_reason",
        "recommendation_rule",
        "requires_human_review",
        "currently_overdue",
        "recommendation_status",
        "priority_rank",
    }

    missing = required - set(result.columns)

    if missing:
        raise ValueError(
            f"Collection recommendation CSV missing: {sorted(missing)}"
        )

    result["account_no"] = id_string(result["account_no"])
    result["customer_id"] = id_string(result["customer_id"])

    if (
        result["account_no"].isna().any()
        or result["account_no"].duplicated().any()
    ):
        raise ValueError("Invalid or duplicate account numbers.")

    return result


# ============================================================
# 7. LOAD EXCEL WORKBOOK
# ============================================================

@st.cache_data(show_spinner=False)
def read_workbook(path: str, mtime: int):

    tables = pd.read_excel(
        path,
        sheet_name=[
            "Customer_Master",
            "Billing",
            "Payments",
            "Collection_History",
            "Suspension_History",
            "Thresholds",
        ],
        engine="openpyxl",
    )

    for name in (
        "Customer_Master",
        "Billing",
        "Payments",
        "Collection_History",
        "Suspension_History",
    ):
        tables[name]["account_no"] = id_string(
            tables[name]["account_no"]
        )

    date_columns = {
        "Billing": [
            "bill_dtm",
            "payment_due_dtm",
            "bill_settled_dtm",
        ],
        "Payments": ["payment_dtm"],
        "Collection_History": ["action_dtm"],
        "Suspension_History": [
            "suspension_dtm",
            "reconnection_dtm",
        ],
    }

    for name, columns in date_columns.items():
        for column in columns:
            if column in tables[name].columns:
                tables[name][column] = as_date(
                    tables[name][column]
                )

    return tables


# ============================================================
# 8. WEBSITE HEADER
# ============================================================


# ============================================================
# 8. WEBSITE HEADER
# ============================================================

st.markdown(
    """
<div class="banner">
<div style="letter-spacing: .13em; font-size: 12px; font-weight: bold; color: #a7dfe5;">
SLT | PHASE 1 | CUSTOMER PAYMENT INTELLIGENCE
</div>
<h1 style="color: white; margin: 12px 0;">
Customer Payment Intelligence Portal
</h1>
<p style="color: #d5f1f2; font-size: 15px;">
Customer Search | Payment History | Creditworthiness Analysis | Collection Recommendations
</p>
</div>
""",
    unsafe_allow_html=True
)


# ============================================================
# 9. CHECK REQUIRED FILES
# ============================================================

if (
    not SCORES.is_file()
    or not WORKBOOK.is_file()
    or not RECOMMENDATIONS.is_file()
):

    st.error(
        "Required project files are missing. "
        "Please run Steps 13, 14 and 15 first."
    )

    st.code(
        "python 13_classify_payment_behaviour.py\n"
        "python 14_collection_recommendations.py\n"
        "python 15_creditworthiness_analysis.py\n"
        "python -m streamlit run customer_portal.py",
        language="powershell",
    )

    st.stop()


# ============================================================
# 10. READ ALL PROJECT DATA
# ============================================================

try:

    scores = read_scores(
        str(SCORES),
        SCORES.stat().st_mtime_ns,
    )

    recommendations = read_recommendations(
        str(RECOMMENDATIONS),
        RECOMMENDATIONS.stat().st_mtime_ns,
    )

    data = read_workbook(
        str(WORKBOOK),
        WORKBOOK.stat().st_mtime_ns,
    )

except Exception as exc:

    st.error(f"Unable to load project data: {exc}")
    st.stop()


# ============================================================
# 11. CREATE COMPLETE CUSTOMER PROFILES
# ============================================================

master = data["Customer_Master"].copy()

master["customer_id"] = id_string(master["customer_id"])

master = master.drop(
    columns=[
        c for c in master.columns
        if "ground truth" in c.lower()
    ],
    errors="ignore",
)

profiles = scores.merge(
    master.drop(columns=["customer_id"]),
    on="account_no",
    how="left",
    validate="one_to_one",
    suffixes=("", "_master"),
)

recommendation_fields = [
    "account_no",
    "customer_id",
    "recommended_action",
    "recommendation_reason",
    "recommendation_rule",
    "currently_overdue",
    "requires_human_review",
    "recommendation_status",
    "priority_rank",
]

profiles = profiles.merge(
    recommendations[recommendation_fields].drop(
        columns="customer_id"
    ),
    on="account_no",
    how="left",
    validate="one_to_one",
)

if (
    profiles["recommended_action"].isna().any()
    or len(profiles) != len(recommendations)
):
    st.error(
        "Recommendation join failed. "
        "Please rerun Steps 13, 14 and 15."
    )
    st.stop()

ids = profiles[
    ["account_no", "customer_id"]
].merge(
    recommendations[["account_no", "customer_id"]],
    on="account_no",
    how="inner",
    suffixes=("_profile", "_recommendation"),
    validate="one_to_one",
)

if not ids["customer_id_profile"].eq(
    ids["customer_id_recommendation"]
).all():
    st.error(
        "Customer IDs do not match between the "
        "creditworthiness and recommendation files."
    )
    st.stop()

if (
    profiles["customer_id"].isna().any()
    or len(profiles) != len(scores)
):
    st.error("Customer profile validation failed.")
    st.stop()


# ============================================================
# 12. GET DATA SNAPSHOT DATE
# ============================================================

snapshot = "26 October 2026"

thresholds = data["Thresholds"]

if {"Parameter", "Value"}.issubset(thresholds.columns):

    matched = thresholds.loc[
        thresholds["Parameter"].eq("Snapshot / analysis date"),
        "Value",
    ]

    if not matched.empty:

        date = pd.to_datetime(
            matched.iloc[0],
            errors="coerce",
        )

        if pd.notna(date):
            snapshot = date.strftime("%d %B %Y")


# ============================================================
# 13. IDENTIFY CURRENTLY OVERDUE ACCOUNTS
# ============================================================

overdue_mask = (
    profiles["unpaid_past_due_bills"].gt(0)
    & profiles["current_overdue_days"].gt(0)
    & profiles["current_outstanding_LKR"].gt(0)
)


# ============================================================
# 14. SIDEBAR NAVIGATION
# ============================================================

with st.sidebar:

    st.markdown("## Customer Portal")

    section = st.radio(
        "Navigation",
        [
            "Overview",
            "Customer Search & Profile",
            "Creditworthiness Method",
            "Project Progress",
        ],
        label_visibility="collapsed",
    )

    st.markdown("---")

    st.caption("Data snapshot")
    st.write(snapshot)

    st.caption("200-account supplied dummy workbook")

    st.warning(
        "Prototype scoring only. "
        "Not an approved SLT credit score or default prediction."
    )

    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ============================================================
# 15. OVERVIEW DASHBOARD
# ============================================================

if section == "Overview":

    st.subheader("Customer Account Overview")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Total Customer Accounts",
        f"{len(profiles):,}",
    )

    col2.metric(
        "Currently Overdue",
        f"{int(overdue_mask.sum()):,}",
    )

    col3.metric(
        "Overdue Account Outstanding",
        currency(
            profiles.loc[
                overdue_mask,
                "current_outstanding_LKR",
            ].sum()
        ),
    )

    col4.metric(
        "Average Reliability Score",
        f"{profiles['payment_reliability_score'].mean():.1f}/100",
    )

    st.markdown("### Collection Recommendations")

    action_counts = (
        profiles["recommended_action"]
        .value_counts()
        .reindex(
            [
                "No follow-up",
                "Normal follow-up",
                "Priority follow-up",
            ],
            fill_value=0,
        )
    )

    st.bar_chart(
        action_counts,
        x_label="Recommended Action",
        y_label="Accounts",
    )

    left, right = st.columns(2)

    with left:

        st.markdown("### Customer Payment Behaviour")

        behaviour_order = [
            "Regular payer",
            "Predictable late payer",
            "Irregular payer",
            "High-risk non-payer",
        ]

        behaviour_counts = (
            profiles["behaviour_classification"]
            .value_counts()
            .reindex(
                behaviour_order,
                fill_value=0,
            )
        )

        st.bar_chart(
            behaviour_counts,
            x_label="Classification",
            y_label="Accounts",
        )

    with right:

        st.markdown("### Creditworthiness Categories")

        bands = profiles["reliability_band"].value_counts()

        st.bar_chart(
            bands,
            x_label="Reliability Category",
            y_label="Accounts",
        )

    st.info(
        "These are prototype payment-reliability indicators. "
        "They are not official credit scores or future-default predictions."
    )

    st.markdown("### Accounts Requiring Overdue Review")

    top = profiles.loc[overdue_mask].sort_values(
        [
            "current_overdue_days",
            "current_outstanding_LKR",
        ],
        ascending=False,
    ).head(12)

    columns = [
        "customer_id",
        "account_no",
        "behaviour_classification",
        "recommended_action",
        "current_overdue_days",
        "current_outstanding_LKR",
        "payment_reliability_score",
    ]

    st.dataframe(
        top[columns],
        hide_index=True,
        use_container_width=True,
    )


# ============================================================
# 16. CUSTOMER SEARCH AND PROFILE
# ============================================================

elif section == "Customer Search & Profile":

    st.subheader("Search Customer")

    st.caption(
        "Search using Customer ID or Account Number."
    )

    search = st.text_input(
        "Customer Search",
        placeholder="Example: C10274 or 401934",
    ).strip()

    filter1, filter2, filter3 = st.columns(3)

    behaviours = sorted(
        profiles["behaviour_classification"]
        .dropna()
        .unique()
    )

    selected_behaviour = filter1.selectbox(
        "Payment Behaviour",
        ["All categories"] + behaviours,
    )

    actions = [
        "No follow-up",
        "Normal follow-up",
        "Priority follow-up",
    ]

    selected_action = filter2.selectbox(
        "Collection Recommendation",
        ["All actions"] + actions,
    )

    only_overdue = filter3.checkbox(
        "Currently Overdue Only"
    )

    found = profiles.copy()

    if search:

        matches = (
            found["customer_id"].str.contains(
                search,
                case=False,
                na=False,
                regex=False,
            )
            |
            found["account_no"].str.contains(
                search,
                case=False,
                na=False,
                regex=False,
            )
        )

        found = found.loc[matches]

    if selected_behaviour != "All categories":

        found = found.loc[
            found["behaviour_classification"].eq(
                selected_behaviour
            )
        ]

    if selected_action != "All actions":

        found = found.loc[
            found["recommended_action"].eq(
                selected_action
            )
        ]

    if only_overdue:

        found = found.loc[
            overdue_mask.reindex(
                found.index,
                fill_value=False,
            )
        ]

    st.write(f"Found {len(found)} customer account(s)")

    overview_columns = [
        "customer_id",
        "account_no",
        "customer_type",
        "behaviour_classification",
        "recommended_action",
        "payment_reliability_score",
        "reliability_band",
        "current_overdue_days",
        "current_outstanding_LKR",
    ]

    st.dataframe(
        found[overview_columns],
        hide_index=True,
        use_container_width=True,
    )

    if found.empty:

        st.info(
            "No matching customer found. "
            "Please check the ID or change the filters."
        )

        st.stop()

    options = found["account_no"].tolist()

    captions = {
        str(row.account_no):
        f"{row.customer_id} | "
        f"Account {row.account_no} | "
        f"{row.behaviour_classification}"
        for row in found.itertuples(index=False)
    }

    selected = st.selectbox(
        "Open Customer Profile",
        options,
        format_func=lambda account: captions.get(
            str(account),
            str(account),
        ),
    )

    customer = profiles.loc[
        profiles["account_no"].eq(selected)
    ].iloc[0]

    account_no = str(customer["account_no"])

    st.divider()

    st.subheader(
        f"Customer Profile - {customer['customer_id']}"
    )

    st.caption(
        f"Account: {account_no} | "
        f"{customer['behaviour_classification']}"
    )

    # --------------------------------------------------------
    # CUSTOMER PROFILE SUMMARY
    # --------------------------------------------------------

    m1, m2, m3, m4 = st.columns(4)

    score = customer["payment_reliability_score"]

    m1.metric(
        "Creditworthiness Score",
        (
            "Review needed"
            if pd.isna(score)
            else f"{score:.1f}/100"
        ),
    )

    m2.metric(
        "Outstanding Balance",
        currency(
            customer["current_outstanding_LKR"]
        ),
    )

    m3.metric(
        "Overdue Days",
        int(customer["current_overdue_days"]),
    )

    m4.metric(
        "Unpaid Past-Due Bills",
        int(customer["unpaid_past_due_bills"]),
    )

    st.markdown(
        f"**Payment Reliability Category:** "
        f"{customer['reliability_band']}"
    )

    st.markdown(
        f"**Creditworthiness Explanation:** "
        f"{customer['score_reason']}"
    )

    st.caption(
        "This is a prototype payment-based reliability score, "
        "not an official credit bureau score."
    )

    # --------------------------------------------------------
    # COLLECTION RECOMMENDATION
    # --------------------------------------------------------

    st.markdown("### Collection Recommendation")

    st.write(
        f"**Recommended Action:** "
        f"{customer['recommended_action']}"
    )

    st.write(
        f"**Recommendation Reason:** "
        f"{customer['recommendation_reason']}"
    )

    st.write(
        f"**Applied Rule:** "
        f"{customer['recommendation_rule']}"
    )

    review_required = customer["requires_human_review"]

    st.write(
        "**Human Review:** "
        + (
            "Required"
            if review_required
            else "Business validation required before use"
        )
    )

    st.warning(
        "This recommendation is for human review only. "
        "No automated collection action is performed."
    )

    # --------------------------------------------------------
    # CUSTOMER PROFILE TABS
    # --------------------------------------------------------

    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "Account & Behaviour",
            "Billing History",
            "Payment History",
            "Collection & Suspension",
        ]
    )

    # --------------------------------------------------------
    # TAB 1 - CUSTOMER ACCOUNT DETAILS
    # --------------------------------------------------------

    with tab1:

        left, right = st.columns(2)

        with left:

            st.markdown("#### Account Information")

            fields = [
                ("customer_type", "Customer Type"),
                ("service_type", "Service Type"),
                ("package", "Package"),
                ("region", "Region"),
                (
                    "preferred_payment_method",
                    "Preferred Payment Method",
                ),
            ]

            for field, label in fields:

                if (
                    field in customer.index
                    and pd.notna(customer[field])
                ):
                    st.write(
                        f"**{label}:** {customer[field]}"
                    )

            st.markdown(
                "#### Historical Payment Indicators"
            )

            st.write(
                f"**Bills Settled:** "
                f"{int(customer['settled_bills'])}/"
                f"{int(customer['total_bills'])}"
            )

            st.write(
                f"**Average Payment Delay:** "
                f"{customer['avg_payment_delay_days']:.1f} days"
            )

            st.write(
                f"**Payment Consistency:** "
                f"{customer['payment_consistency']:.0%}"
            )

            st.write(
                f"**Late Payment Ratio:** "
                f"{customer['late_ratio']:.0%}"
            )

            st.write(
                f"**Behaviour Classification Reason:** "
                f"{customer['classification_reason']}"
            )

        with right:

            st.markdown(
                "#### Creditworthiness Score Breakdown"
            )

            points = {
                "Settled Bill History (25)":
                    customer["points_settlement"],

                "Payment Consistency (20)":
                    customer["points_consistency"],

                "Average Payment Delay (15)":
                    customer["points_delay"],

                "Current Arrears (25)":
                    customer["points_current_arrears"],

                "Outstanding Exposure (10)":
                    customer["points_balance_exposure"],

                "Collection History (5)":
                    customer["points_collection_history"],
            }

            st.bar_chart(
                pd.Series(
                    points,
                    name="Points Earned",
                )
            )

            st.info(
                f"Recommended collection action: "
                f"{customer['recommended_action']}"
            )

    # --------------------------------------------------------
    # TAB 2 - BILLING HISTORY
    # --------------------------------------------------------

    with tab2:

        st.markdown("### Customer Billing History")

        billing = data["Billing"].loc[
            data["Billing"]["account_no"].eq(
                account_no
            )
        ].copy()

        billing = billing.sort_values(
            "bill_dtm",
            ascending=False,
        )

        show_columns = [
            "invoice_no",
            "bill_dtm",
            "payment_due_dtm",
            "bill_settled_dtm",
            "invoice_net_amt",
            "invoice_tax_amt",
            "balance_out_amt",
        ]

        st.dataframe(
            billing[
                [
                    c for c in show_columns
                    if c in billing.columns
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

        if (
            not billing.empty
            and {
                "bill_dtm",
                "balance_out_amt",
            }.issubset(billing.columns)
        ):

            st.markdown(
                "#### Historical Outstanding Balances"
            )

            chart_data = (
                billing.sort_values("bill_dtm")
                .set_index("bill_dtm")[
                    "balance_out_amt"
                ]
            )

            st.line_chart(chart_data)

    # --------------------------------------------------------
    # TAB 3 - PAYMENT HISTORY
    # --------------------------------------------------------

    with tab3:

        st.markdown("### Customer Payment History")

        payments = data["Payments"].loc[
            data["Payments"]["account_no"].eq(
                account_no
            )
        ].copy()

        payments = payments.sort_values(
            "payment_dtm",
            ascending=False,
        )

        payment_columns = [
            "payment_id",
            "invoice_no",
            "payment_dtm",
            "payment_amt",
            "payment_method",
        ]

        st.dataframe(
            payments[
                [
                    c for c in payment_columns
                    if c in payments.columns
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            f"{len(payments)} payment transaction(s) found."
        )

    # --------------------------------------------------------
    # TAB 4 - COLLECTION AND SUSPENSION HISTORY
    # --------------------------------------------------------

    with tab4:

        actions = data["Collection_History"].loc[
            data["Collection_History"]["account_no"].eq(
                account_no
            )
        ].copy()

        suspensions = data["Suspension_History"].loc[
            data["Suspension_History"]["account_no"].eq(
                account_no
            )
        ].copy()

        st.markdown(
            f"#### Collection Actions ({len(actions)})"
        )

        action_columns = [
            "action_dtm",
            "action_type",
            "days_past_due",
            "outcome",
        ]

        action_table = actions[
            [
                c for c in action_columns
                if c in actions.columns
            ]
        ]

        if "action_dtm" in action_table.columns:
            action_table = action_table.sort_values(
                "action_dtm",
                ascending=False,
            )

        st.dataframe(
            action_table,
            hide_index=True,
            use_container_width=True,
        )

        st.markdown(
            f"#### Suspension History ({len(suspensions)})"
        )

        suspension_columns = [
            "suspension_dtm",
            "reconnection_dtm",
            "reason",
            "status",
        ]

        suspension_table = suspensions[
            [
                c for c in suspension_columns
                if c in suspensions.columns
            ]
        ]

        if "suspension_dtm" in suspension_table.columns:
            suspension_table = suspension_table.sort_values(
                "suspension_dtm",
                ascending=False,
            )

        st.dataframe(
            suspension_table,
            hide_index=True,
            use_container_width=True,
        )

    # --------------------------------------------------------
    # DOWNLOAD CUSTOMER PROFILE
    # --------------------------------------------------------

    st.download_button(
        "Download Customer Profile (CSV)",
        pd.DataFrame([customer])
        .to_csv(index=False)
        .encode("utf-8-sig"),
        file_name=(
            f"customer_profile_{customer['customer_id']}.csv"
        ),
        mime="text/csv",
    )


# ============================================================
# 17. CREDITWORTHINESS EXPLANATION PAGE
# ============================================================

elif section == "Creditworthiness Method":

    st.subheader("Creditworthiness Analysis Method")

    st.warning(
        "The scoring model is a prototype. "
        "It requires SLT business validation."
    )

    st.markdown("""
### Creditworthiness Scoring Factors

| Feature | Maximum Points |
|---|---:|
| Settled Bill History | 25 |
| Payment Consistency | 20 |
| Average Payment Delay | 15 |
| Current Overdue Bills | 25 |
| Outstanding Exposure | 10 |
| Collection and Suspension History | 5 |
| **Total** | **100** |

### Proposed Creditworthiness Categories

| Score | Classification |
|---|---|
| 80–100 | Strong Payment Reliability |
| 60–79.9 | Moderate Payment Reliability |
| 40–59.9 | Low Payment Reliability |
| Below 40 | Very Low Payment Reliability |

A customer with fewer than three settled bills requires
manual review instead of an automatic score.

These scores measure historical payment behaviour.

They are not official credit bureau scores and do not
establish a customer's financial ability to pay.
""")


# ============================================================
# 18. PROJECT DEVELOPMENT STATUS
# ============================================================

else:

    st.subheader("Project Development Roadmap")

    st.markdown("""
### Implemented

- Customer data validation
- Feature engineering
- Defaulting / overdue customer identification
- Customer payment behaviour classification
- Explainable collection recommendations
- Payment-based creditworthiness scoring
- Searchable customer profiles
- Billing and payment history views
- Collection and suspension history

### Next Development

- Finalize official SLT business rules
- Validate classification and scoring thresholds
- Improve recommendation accuracy
- Review high-risk customer decisions
- Test approved larger historical datasets
- Integrate secure user authentication
- Implement role-based access controls
- Deploy an approved secure website

### Important Note

The earlier Random Forest and XGBoost models were
trained using a different synthetic dataset.

They are not used as the creditworthiness models
in this portal.
""")


# ============================================================
# 19. WEBSITE FOOTER
# ============================================================

st.divider()

st.caption(
    f"SLT Customer Payment Intelligence Portal | "
    f"Phase 1 | Dummy Dataset | "
    f"Snapshot: {snapshot} | "
    f"Internal Prototype Only"
)
