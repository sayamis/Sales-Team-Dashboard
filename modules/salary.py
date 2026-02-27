import streamlit as st
import pandas as pd

from utils.gsheets import (
    get_gs_client, SPREADSHEET_URL,
    open_sheet_by_url_safe, worksheet_safe, get_all_values_safe
)
from utils.access import get_user_access, load_employee_list

# =========================
# CONFIG
# =========================
SALARY_SHEET_NAME = "Salary"
CACHE_TTL = 300  # seconds

# =========================
# UI STYLE (simple + clean)
# =========================
st.markdown(
    """
<style>
.stApp { background: #ffffff; }
.hdr {
  display:flex; align-items:center; justify-content:space-between;
  margin: 6px 0 14px 0;
}
.hdr h1{
  font-size: 34px; margin:0; font-weight: 800; color:#0f172a;
}
.card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  padding: 12px 14px;
}
.small { color:#6b7280; font-size:12px; }
</style>
""",
    unsafe_allow_html=True,
)

# =========================
# Helpers
# =========================
def _norm(s: str) -> str:
    return str(s).replace("\u00a0", " ").strip().lower()

def _find_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    norm_map = {_norm(c): c for c in df.columns}
    for a in aliases:
        key = _norm(a)
        if key in norm_map:
            return norm_map[key]
    return None

def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)

def _money(x: float) -> str:
    return f"₹{x:,.2f}"

# =========================
# Load Salary Data
# =========================
@st.cache_data(ttl=CACHE_TTL)
def load_salary_df():
    client = get_gs_client()
    sh = open_sheet_by_url_safe(client, SPREADSHEET_URL)
    ws = worksheet_safe(sh, SALARY_SHEET_NAME)
    values = get_all_values_safe(ws)
    if not values or len(values) < 2:
        return pd.DataFrame()

    headers = [str(h).strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    # remove totally blank columns
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    df.columns = [c.strip() for c in df.columns]
    return df

def apply_role_security(df: pd.DataFrame, access: dict) -> pd.DataFrame:
    """
    Employee List columns:
    [Emp Code, Employee Name, Mail, Attendance/Salary View Mode, Team, Allowed Team, Sales Dashboard View Mode, Allowed States]

    Rules:
    - personal: Salary[Employee Code] == emp_code
    - team: Salary employees whose Team (from Employee List) in allowed_teams list
    - all: full
    """
    if df is None or df.empty:
        return df

    emp_code = str(access.get("emp_code", "")).strip()
    mode = str(access.get("attendance_mode", "")).strip().lower()
    allowed_teams = access.get("allowed_teams", []) or []

    col_emp = _find_col(df, ["Employee Code", "Emp Code", "EmployeeCode"])
    if not col_emp:
        return df if mode == "all" else df.iloc[0:0]

    df2 = df.copy()
    df2[col_emp] = df2[col_emp].astype(str).str.strip()

    if mode == "personal":
        df2 = df2[df2[col_emp] == emp_code]

    elif mode == "team":
        emp_list = load_employee_list()
        if emp_list is None or emp_list.empty:
            return df2.iloc[0:0]

        emp_list.columns = [c.strip() for c in emp_list.columns]
        el_emp = _find_col(emp_list, ["Emp Code", "Employee Code", "EmpCode"])
        el_team = _find_col(emp_list, ["Team"])
        if not el_emp or not el_team:
            return df2.iloc[0:0]

        m = (
            emp_list[[el_emp, el_team]]
            .copy()
            .rename(columns={el_emp: "__emp__", el_team: "__team__"})
        )
        m["__emp__"] = m["__emp__"].astype(str).str.strip()
        m["__team__"] = m["__team__"].astype(str).str.strip()

        team_map = dict(zip(m["__emp__"], m["__team__"]))
        df2["__team__"] = df2[col_emp].map(team_map)

        allowed = set([str(x).strip() for x in allowed_teams if str(x).strip()])
        df2 = df2[df2["__team__"].isin(allowed)]

    elif mode == "all":
        pass
    else:
        df2 = df2.iloc[0:0]

    return df2

# =========================
# MAIN
# =========================
def show():
    # ---- auth check ----
    if "user_email" not in st.session_state:
        st.warning("Please login first.")
        st.stop()

    email = st.session_state.user_email
    access = get_user_access(email)
    if access is None:
        st.warning("No access configured.")
        st.stop()

    # ---- load data ----
    df = load_salary_df()
    if df.empty:
        st.warning("No salary data found in sheet 'Salary'.")
        st.stop()

    # ---- detect columns ----
    col_emp = _find_col(df, ["Employee Code", "Emp Code"])
    col_month = _find_col(df, ["Month", "Month & Year", "Month & Year (AL)"])

    col_payable = _find_col(df, [
        "Gross Salary Payable incl the paid leave utilization (total hrs payable +(paid leave utilized *8.5)) / (8.5*E1) x employee monthly salary",
        "Gross Salary Payable",
        "Salary Payable",
        "Net Salary Payable",
    ])

    if not col_emp or not col_month or not col_payable:
        st.error(
            "Salary sheet headers mismatch.\n\n"
            f"Found columns: {list(df.columns)}\n\n"
            "Required: Employee Code, Month (or Month & Year), and Gross Salary Payable."
        )
        st.stop()

    # normalize
    df[col_emp] = df[col_emp].astype(str).str.strip()
    df[col_month] = df[col_month].astype(str).str.strip()
    df[col_payable] = _to_num(df[col_payable])

    # ---- role security ----
    df_sec = apply_role_security(df, access)
    if df_sec.empty:
        st.warning("No data available for your access level.")
        st.stop()

    # =========================
    # HEADER
    # =========================
    st.markdown('<div class="hdr"><h1>Salary Dashboard</h1></div>', unsafe_allow_html=True)

    # =========================
    # ONLY ONE FILTER: Month
    # =========================
    months = sorted([x for x in df_sec[col_month].dropna().unique() if str(x).strip()])
    month_options = ["All Months"] + months

    if "sal_month_only" not in st.session_state:
        st.session_state.sal_month_only = "All Months"

    st.markdown('<div class="card">', unsafe_allow_html=True)
    # st.markdown("**Quick Filter**  \n<span class='small'>Month & Year (AL)</span>", unsafe_allow_html=True)
    pickm = st.selectbox(
        "Month & Year (AL)",
        options=month_options,
        index=month_options.index(st.session_state.sal_month_only) if st.session_state.sal_month_only in month_options else 0,
    )
    st.session_state.sal_month_only = pickm
    st.markdown("</div>", unsafe_allow_html=True)

    # apply filter
    df_f = df_sec.copy()
    if pickm != "All Months":
        df_f = df_f[df_f[col_month] == pickm]

    # =========================
    # KPIs (3 only)
    # =========================
    total_salary = float(df_f[col_payable].sum())
    employees = int(df_f[col_emp].astype(str).replace("", pd.NA).dropna().nunique())
    avg_salary = (total_salary / employees) if employees > 0 else 0.0

    k1, k2, k3 = st.columns(3)
    k1.metric("Total Salary", _money(total_salary))
    k2.metric("Employees", f"{employees:,}")
    k3.metric("Average Salary", _money(avg_salary))

    st.write("")

    # =========================
    # TABLE (no popup) + SEARCH
    # =========================
    st.subheader("Filtered Details")
    st.caption("Search works across Employee Code / Name / Branch / Department / Month etc.")

    search = st.text_input("Search", placeholder="Type to search... (e.g., emp code, name, branch, dept)")

    # Build display table columns (as per your earlier list) — but safely (only cols that exist)
    col_name = _find_col(df_f, ["NAME", "Employee Name", "Name"])
    col_join = _find_col(df_f, ["Joining Date", "Join Date"])
    col_branch = _find_col(df_f, ["Branch"])
    col_cat = _find_col(df_f, ["STAFF / LABOUR/ SALES", "STAFF / LABOUR / SALES", "Category", "Staff / Labour/ Sales"])
    col_work_hrs = _find_col(df_f, ["Employee Working Hrs", "Working Hrs"])
    col_monthly_gross = _find_col(df_f, ['Monthly Gross Salary', 'Monthly Gross \nSalary', 'Monthly Gross Salary '])
    col_present = _find_col(df_f, ["Present days", "Present Days"])
    col_leave = _find_col(df_f, ["Total Leave Days", "Leave Days"])
    col_sun = _find_col(df_f, ["Sunday/Holiday", "Sunday/\nHoliday", "Sunday / Holiday"])
    col_total_days = _find_col(df_f, ["Total days (inc. Sunday/Holidays", "Total days (inc. Sunday/Holidays"])
    col_books = _find_col(df_f, ["Gross Salary as per Books"])
    col_req_hrs = _find_col(df_f, ["No of hrs required as per present days", "Required Hrs"])
    col_actual_hrs = _find_col(df_f, ["Actual Working Hrs. (Calculate total of Hrs only on Present Days)", "Actual Working Hrs"])
    col_payable_hrs_wo_ot = _find_col(df_f, ["Payable Hrs without OT"])
    col_sun_hrs = _find_col(df_f, ["Sunday / Holiday\nHrs Given", "Sunday / Holiday Hrs Given", "Sunday/Holiday Hrs Given"])
    col_ot = _find_col(df_f, ["Approved Overtime", "Overtime"])
    col_total_hrs_payable = _find_col(df_f, ["Total Hrs payable (Actual Working hrs of present days + short leave + Sunday hrs - Penalty of No Show)", "Total Hrs payable"])
    col_dept = _find_col(df_f, ["Department", "Dept"])

    popup_cols = [
        (col_emp, "Employee Code"),
        (col_name, "NAME"),
        (col_join, "Joining Date"),
        (col_branch, "Branch"),
        (col_cat, "STAFF / LABOUR/ SALES"),
        (col_work_hrs, "Employee Working Hrs"),
        (col_monthly_gross, "Monthly Gross Salary"),
        (col_present, "Present days"),
        (col_leave, "Total Leave Days"),
        (col_sun, "Sunday/Holiday"),
        (col_total_days, "Total days (inc. Sunday/Holidays"),
        (col_books, "Gross Salary as per Books"),
        (col_req_hrs, "Required Hrs (as per present days)"),
        (col_actual_hrs, "Actual Working Hrs (Present Days)"),
        (col_payable_hrs_wo_ot, "Payable Hrs without OT"),
        (col_sun_hrs, "Sunday / Holiday Hrs Given"),
        (col_ot, "Approved Overtime"),
        (col_total_hrs_payable, "Total Hrs payable"),
        (col_payable, "Gross Salary Payable"),
        (col_dept, "Department"),
        (col_month, "Month"),
    ]

    keep_cols = []
    rename = {}
    for real_col, label in popup_cols:
        if real_col and real_col in df_f.columns:
            keep_cols.append(real_col)
            rename[real_col] = label

    details_df = df_f[keep_cols].copy().rename(columns=rename)

    # search filter
    if search and not details_df.empty:
        s = search.strip().lower()

        # join all cols into a single searchable string per row
        search_blob = details_df.astype(str).apply(
            lambda r: " | ".join([x.lower() for x in r.values.tolist()]),
            axis=1
        )
        details_df = details_df[search_blob.str.contains(s, na=False)]

    st.dataframe(details_df, use_container_width=True, hide_index=True)