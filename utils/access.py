import pandas as pd
import streamlit as st
from utils.gsheets import get_gs_client, SPREADSHEET_URL, open_sheet_by_url_safe, worksheet_safe, get_all_records_safe


EMPLOYEE_SHEET_NAME = "Employee List"


# -----------------------------
# Helper: split comma values
# -----------------------------
def _split_comma_values(val):
    if not val:
        return []
    return [x.strip() for x in str(val).split(",") if x.strip()]


# -----------------------------
# Load Employee List
# -----------------------------
@st.cache_data(ttl=60)
def load_employee_list():
    client = get_gs_client()
    sh = open_sheet_by_url_safe(client, SPREADSHEET_URL)
    ws = worksheet_safe(sh, EMPLOYEE_SHEET_NAME)
    data = get_all_records_safe(ws)
    df = pd.DataFrame(data)

    # Normalize column names (important)
    df.columns = [c.strip() for c in df.columns]

    return df


# -----------------------------
# Get Access For Logged User
# -----------------------------
def get_user_access(user_email: str):
    df = load_employee_list()

    if df.empty:
        return None

    df["Mail"] = df["Mail"].astype(str).str.strip().str.lower()

    user_row = df[df["Mail"] == user_email.lower()]

    if user_row.empty:
        return None

    row = user_row.iloc[0]

    return {
        "emp_code": str(row.get("Emp Code", "")).strip(),
        "attendance_mode": str(row.get("Attendance/Salary View Mode", "")).strip(),
        "allowed_teams": _split_comma_values(row.get("Allowed Team", "")),
        "sales_mode": str(row.get("Sales Dashboard View Mode", "")).strip(),
        "allowed_states": _split_comma_values(row.get("Allowed States", "")),
    }
