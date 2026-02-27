import streamlit as st
import pandas as pd
from utils.access import get_user_access
from utils.gsheets import get_gs_client, open_sheet_by_url_safe, worksheet_safe, get_all_values_safe


SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1uCHPSSdK4J4Ag-iXq-JkjDCQI8e5hM5OAZa7XUnJywg/edit"
SHEET_NAME = "Sale Expenses"


# -----------------------------
# STRONG UI OVERRIDE
# -----------------------------
st.markdown("""
<style>

/* ===== FORCE PURE BLACK TEXT ===== */
html, body, [class*="css"] {
    color: #000000 !important;
}

/* Headings Bold + Black */
h1, h2, h3, h4 {
    color: #000000 !important;
    font-weight: 800 !important;
}

/* Labels bold */
label {
    font-weight: 700 !important;
    color: #000000 !important;
}


/* Move Apply button slightly up */
div.stButton > button {
    margin-top: 8px !important;
    height: 36px;
}

/* KPI container styling */
div[data-testid="metric-container"] {
    padding: 8px 12px !important;
    background-color: #f5f5f5;
    border-radius: 8px;
}

div[data-testid="metric-container"] {
    padding: 10px 14px !important;
    margin-right: 10px !important;
}

/* KPI label bold */
div[data-testid="metric-container"] label {
    font-weight: 800 !important;
    color: #000000 !important;
}

/* KPI value bold + black */
div[data-testid="metric-container"] div {
    font-weight: 800 !important;
    color: #000000 !important;
    font-size: 22px !important;
}

</style>
""", unsafe_allow_html=True)




# -----------------------------
# Load Data
# -----------------------------
@st.cache_data(ttl=300)
def load_expense_data():
    client = get_gs_client()
    sh = open_sheet_by_url_safe(client, SPREADSHEET_URL)
    ws = worksheet_safe(sh, SHEET_NAME)
    values = get_all_values_safe(ws)
    if not values:
        return pd.DataFrame()

    headers = values[0]

    cleaned_headers = []
    keep_indexes = []

    for i, h in enumerate(headers):
        h_clean = str(h).strip()
        if h_clean != "":
            cleaned_headers.append(h_clean)
            keep_indexes.append(i)

    cleaned_rows = []
    for row in values[1:]:
        new_row = []
        for idx in keep_indexes:
            if idx < len(row):
                new_row.append(row[idx])
            else:
                new_row.append("")
        cleaned_rows.append(new_row)

    df = pd.DataFrame(cleaned_rows, columns=cleaned_headers)

    if df.empty:
        return df

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(
            df["Date"],
            errors="coerce",
            dayfirst=True   # 👈 THIS FIXES dd/mm/yyyy
        )
        df = df.dropna(subset=["Date"])
        df["Date"] = df["Date"].dt.date


    numeric_cols = [
        "Local DA", "Ex-HQ", "Hotel",
        "Fooding", "Local conveyance", "TA Allowed"
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    return df


# -----------------------------
# Main Show Function
# -----------------------------
def show():

    st.title("💰 Sales Expenses Dashboard")

    if "user_email" not in st.session_state:
        st.warning("Please login first.")
        st.stop()

    user_email = st.session_state.user_email
    access = get_user_access(user_email)

    if access is None:
        st.warning("No access configured.")
        st.stop()

    df = load_expense_data()

    if df.empty:
        st.warning("No expense data found.")
        st.stop()

    # -----------------------------
    # Access Control
    # -----------------------------
    view_mode = access["attendance_mode"]
    emp_code = access["emp_code"]

    if view_mode.lower() == "personal":
        df = df[df["Emp Code"].astype(str) == str(emp_code)]

    elif view_mode.lower() == "team":
        allowed_teams = access["allowed_teams"]
        df = df[df["temp_Territory"].isin(allowed_teams)]

    elif view_mode.lower() == "all":
        pass

    else:
        st.warning("You are not allowed to view this dashboard.")
        st.stop()

    if df.empty:
        st.warning("No data available for your access level.")
        st.stop()

    # -----------------------------
    # Compact Date Filter
    # -----------------------------
    min_d = df["Date"].min()
    max_d = df["Date"].max()

    st.markdown("### 📅 Filter")

    # Compact horizontal layout
    col1, col2, col3 = st.columns([1, 1, 0.6])

    with col1:
        start_date = st.date_input("Start Date", value=min_d)

    with col2:
        end_date = st.date_input("End Date", value=max_d)

    with col3:
        st.markdown("<br>", unsafe_allow_html=True)  # vertical alignment
        apply_btn = st.button("Apply", use_container_width=True)

    # Apply filter
    if start_date and end_date:
        df = df[(df["Date"] >= start_date) & (df["Date"] <= end_date)]


    # -----------------------------
    # KPIs
    # -----------------------------
    df["Grand Total"] = (
        df["Local DA"]
        + df["Ex-HQ"]
        + df["Hotel"]
        + df["Fooding"]
        + df["Local conveyance"]
        + df["TA Allowed"]
    )

    total_employees = df["Field User"].nunique()
    total_days = df["Date"].nunique()
    grand_total = df["Grand Total"].sum()

    k1, k2, k3 = st.columns(3)

    k1.metric("Employees", total_employees)
    k2.metric("Days", total_days)
    k3.metric("Grand Total", f"{grand_total:,.1f}")

    st.divider()

    # -----------------------------
    # Employee Summary
    # -----------------------------
    summary = df.groupby("Field User").agg({
        "Local DA": "sum",
        "Ex-HQ": "sum",
        "Hotel": "sum",
        "Fooding": "sum",
        "Local conveyance": "sum",
        "TA Allowed": "sum",
        "Grand Total": "sum"
    }).reset_index()

    summary = summary.sort_values("Grand Total", ascending=False)

    st.subheader("Employee Summary")

    selected_employee = st.selectbox(
        "Select Employee for Breakup",
        summary["Field User"].unique()
    )

    st.dataframe(summary, use_container_width=True)

    # -----------------------------
    # Breakup
    # -----------------------------
    if selected_employee:
        st.divider()
        st.subheader(f"Breakup: {selected_employee}")

        emp_df = df[df["Field User"] == selected_employee]

        emp_days = emp_df["Date"].nunique()
        emp_total = emp_df["Grand Total"].sum()

        b1, b2 = st.columns(2)
        b1.metric("Days", emp_days)
        b2.metric("Grand Total", f"{emp_total:,.1f}")

        st.dataframe(emp_df.sort_values("Date"), use_container_width=True)
