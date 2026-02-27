import streamlit as st
import pandas as pd
from datetime import date, timedelta

from utils.gsheets import (
    get_gs_client, SPREADSHEET_URL,
    open_sheet_by_url_safe, worksheet_safe, get_all_values_safe
)
from utils.access import get_user_access, load_employee_list

# =========================
# CONFIG
# =========================
ATTENDANCE_SHEET_NAME = "Attendance"
CACHE_TTL = 300  # seconds


# =========================
# UI STYLE (clean + professional)
# =========================
st.markdown(
    """
<style>
/* Base */
.stApp { background: #ffffff; }
h1, h2, h3 { color:#0f172a; font-weight:800; }
p, label, div { color:#0f172a; }
.small { color:#64748b; font-size:12px; }

/* KPI Cards */
div[data-testid="metric-container"]{
  background:#f8fafc;
  border:1px solid #e2e8f0;
  padding:12px 14px !important;
  border-radius:14px;
}
div[data-testid="metric-container"] label{
  font-weight:800 !important;
  color:#0f172a !important;
}
div[data-testid="metric-container"] div{
  font-weight:800 !important;
  color:#0f172a !important;
  font-size:26px !important;
}

/* Section cards */
.card {
  background:#ffffff;
  border:1px solid #e2e8f0;
  border-radius:16px;
  padding:12px 14px;
  margin: 10px 0 14px 0;
}

/* Make st.dataframe a bit nicer */
[data-testid="stDataFrame"]{
  border:1px solid #e2e8f0;
  border-radius:16px;
  overflow:hidden;
}
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
        k = _norm(a)
        if k in norm_map:
            return norm_map[k]
    return None

def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)

def _status_norm(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()

def _safe_col(df: pd.DataFrame, col: str | None) -> pd.Series:
    if col and col in df.columns:
        return df[col]
    return pd.Series([""] * len(df))

def _current_month_range():
    today = date.today()
    start = today.replace(day=1)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    end = (pd.to_datetime(next_month) - pd.Timedelta(days=1)).date()
    return start, end

def _days_in_range(d1: date, d2: date) -> int:
    if not d1 or not d2:
        return 0
    if d2 < d1:
        d1, d2 = d2, d1
    return (d2 - d1).days + 1

def _count_sundays(d1: date, d2: date) -> int:
    if not d1 or not d2:
        return 0
    if d2 < d1:
        d1, d2 = d2, d1
    cur = d1
    cnt = 0
    while cur <= d2:
        if cur.weekday() == 6:  # Sunday
            cnt += 1
        cur += timedelta(days=1)
    return cnt

def _money0(x: float) -> str:
    return f"{x:,.0f}"

def _pct(x: float) -> str:
    return f"{x:,.2f}%"


# =========================
# Load Attendance Data
# =========================
@st.cache_data(ttl=CACHE_TTL)
def load_attendance_df():
    client = get_gs_client()
    sh = open_sheet_by_url_safe(client, SPREADSHEET_URL)
    ws = worksheet_safe(sh, ATTENDANCE_SHEET_NAME)
    values = get_all_values_safe(ws)
    if not values or len(values) < 2:
        return pd.DataFrame()

    headers = [str(h).strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    # remove totally blank columns
    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    df.columns = [c.strip() for c in df.columns]

    # parse Date
    col_date = _find_col(df, ["Date"])
    if col_date:
        dt = pd.to_datetime(df[col_date].astype(str).str.strip(), errors="coerce", dayfirst=True)
        df = df[~dt.isna()].copy()
        df[col_date] = dt.dt.date

    # numeric
    col_tc = _find_col(df, ["TC"])
    col_pc = _find_col(df, ["PC"])
    if col_tc:
        df[col_tc] = _to_num(df[col_tc])
    if col_pc:
        df[col_pc] = _to_num(df[col_pc])

    return df


def apply_role_security(df: pd.DataFrame, access: dict) -> pd.DataFrame:
    """
    - personal: Attendance[Employee ErpId] == emp_code
    - team: Attendance employees whose Team (from Employee List) in allowed_teams
    - all: full
    """
    if df is None or df.empty:
        return df

    emp_code = str(access.get("emp_code", "")).strip()
    mode = str(access.get("attendance_mode", "")).strip().lower()
    allowed_teams = access.get("allowed_teams", []) or []

    col_emp = _find_col(df, ["Employee ErpId", "Employee ERPId", "Emp Code", "Employee Code"])
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
        el_emp = _find_col(emp_list, ["Emp Code", "Employee Code", "Employee ErpId"])
        el_team = _find_col(emp_list, ["Team"])
        if not el_emp or not el_team:
            return df2.iloc[0:0]

        m = emp_list[[el_emp, el_team]].copy().rename(columns={el_emp: "__emp__", el_team: "__team__"})
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
    st.markdown("## 🗓️ Attendance Dashboard")
    st.markdown("<div class='small'>Summary + Breakup view (same pattern as Expenses)</div>", unsafe_allow_html=True)

    # auth
    email = st.session_state.get("user_email")
    if not email:
        st.warning("Please login from sidebar to view dashboard.")
        st.stop()

    access = get_user_access(email)
    if access is None:
        st.warning("No access configured.")
        st.stop()

    df = load_attendance_df()
    if df.empty:
        st.warning("No attendance data found in sheet 'Attendance'.")
        st.stop()

    # columns
    col_date = _find_col(df, ["Date"])
    col_emp = _find_col(df, ["Employee ErpId", "Employee ERPId"])
    col_user = _find_col(df, ["User"])
    col_user_status = _find_col(df, ["User Status"])
    col_pos_names = _find_col(df, ["User Position Names"])
    col_tour_plan = _find_col(df, ["Tour Plan"])
    col_type = _find_col(df, ["Type"])
    col_login = _find_col(df, ["Login"])
    col_logout = _find_col(df, ["Logout"])
    col_total_time = _find_col(df, ["Total Time"])
    col_first_call = _find_col(df, ["First Call"])
    col_last_call = _find_col(df, ["Last Call"])
    col_retail_time = _find_col(df, ["Retail Time"])
    col_tc = _find_col(df, ["TC"])
    col_pc = _find_col(df, ["PC"])
    col_productivity = _find_col(df, ["Productivity"])
    col_grade = _find_col(df, ["Retailing Grade"])
    col_status = _find_col(df, ["Status"])

    if not col_date or not col_emp or not col_status:
        st.error(
            "Attendance sheet headers mismatch.\n\n"
            f"Found columns: {list(df.columns)}\n\n"
            "Required at least: Date, Employee ErpId, Status"
        )
        st.stop()

    # security
    df_sec = apply_role_security(df, access)
    if df_sec.empty:
        st.warning("No data available for your access level.")
        st.stop()

    # =========================
    # FILTER (default current month)
    # =========================
    cm_start, cm_end = _current_month_range()
    if "att_date_range" not in st.session_state:
        st.session_state.att_date_range = (cm_start, cm_end)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("### 🗓️ Filter")

    c1, c2, c3 = st.columns([1, 1, 0.8])
    with c1:
        start_d = st.date_input("Start Date", value=st.session_state.att_date_range[0])
    with c2:
        end_d = st.date_input("End Date", value=st.session_state.att_date_range[1])
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Apply", use_container_width=True):
            st.session_state.att_date_range = (start_d, end_d)
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    d1, d2 = st.session_state.att_date_range
    if d2 < d1:
        d1, d2 = d2, d1

    df_f = df_sec[df_sec[col_date].between(d1, d2, inclusive="both")].copy()
    if df_f.empty:
        st.warning("No rows in selected date range.")
        st.stop()

    # =========================
    # TOP KPI (ONLY 3) — calendar based
    # =========================
    total_days = _days_in_range(d1, d2)
    week_off = _count_sundays(d1, d2)
    working_days = max(total_days - week_off, 0)

    k1, k2, k3 = st.columns(3)
    k1.metric("Total Days (Range)", f"{total_days}")
    k2.metric("Working Days", f"{working_days}")
    k3.metric("Week Off (Sundays)", f"{week_off}")

    st.divider()

    # =========================
    # SUMMARY TABLE (all KPI inside table)
    # =========================
    df_sum = df_f.copy()
    df_sum["_user"] = _safe_col(df_sum, col_user).astype(str).str.strip()
    df_sum["_emp"] = _safe_col(df_sum, col_emp).astype(str).str.strip()
    df_sum["_status"] = _status_norm(_safe_col(df_sum, col_status))
    df_sum["_tc"] = _to_num(_safe_col(df_sum, col_tc))
    df_sum["_pc"] = _to_num(_safe_col(df_sum, col_pc))

    grp = df_sum.groupby(["_user", "_emp"], dropna=False)

    summary = grp.agg(
        Total_Days=(col_date, "count"),
        Present_P=("_status", lambda x: (x == "P").sum()),
        HalfDay_HD=("_status", lambda x: (x == "HD").sum()),
        WeekOff_Sunday=("_status", lambda x: (x == "SUNDAY").sum()),
        Absent_A=("_status", lambda x: (x == "A").sum()),
        TC=("_tc", "sum"),
        PC=("_pc", "sum"),
    ).reset_index()

    summary["Productivity %"] = summary.apply(
        lambda r: (r["PC"] / r["TC"] * 100) if r["TC"] else 0.0, axis=1
    )

    summary = summary.rename(columns={"_user": "User", "_emp": "Employee ErpId"})
    summary = summary.sort_values(["TC", "PC"], ascending=False)

    st.subheader("Employee Summary")
    st.caption("KPIs are inside table. Select employee to view breakup.")

    selected_employee = st.selectbox(
        "Select Employee for Breakup",
        summary["User"].unique().tolist() if not summary.empty else []
    )

    st.dataframe(summary, use_container_width=True, hide_index=True)

    # =========================
    # BREAKUP
    # =========================
    if not selected_employee:
        return

    emp_df = df_f[df_f[col_user].astype(str).str.strip() == str(selected_employee).strip()].copy()
    if emp_df.empty:
        st.info("No rows for this employee in selected range.")
        return

    # compute breakup KPIs (same as summary)
    emp_s = _status_norm(emp_df[col_status])
    p_cnt = int((emp_s == "P").sum())
    hd_cnt = int((emp_s == "HD").sum())
    a_cnt = int((emp_s == "A").sum())
    sun_cnt = int((emp_s == "SUNDAY").sum())

    tc_sum = float(emp_df[col_tc].sum()) if col_tc else 0.0
    pc_sum = float(emp_df[col_pc].sum()) if col_pc else 0.0
    prod_pct = (pc_sum / tc_sum * 100) if tc_sum > 0 else 0.0
    emp_total_days = int(emp_df[col_date].count())

    st.divider()
    st.subheader(f"Breakup: {selected_employee}")

    # Breakup KPI row (all details)
    b1, b2, b3, b4, b5, b6, b7, b8 = st.columns(8)
    b1.metric("Total Days", f"{emp_total_days}")
    b2.metric("Present (P)", f"{p_cnt}")
    b3.metric("Half Day (HD)", f"{hd_cnt}")
    b4.metric("Absent (A)", f"{a_cnt}")
    b5.metric("Week Off (Sunday)", f"{sun_cnt}")
    b6.metric("TC", _money0(tc_sum))
    b7.metric("PC", _money0(pc_sum))
    b8.metric("Productivity %", _pct(prod_pct))

    # detailed table (your exact list)
    wanted = [
        (col_date, "Date"),
        (col_emp, "Employee ErpId"),
        (col_user, "User"),
        (col_user_status, "User Status"),
        (col_pos_names, "User Position Names"),
        (col_tour_plan, "Tour Plan"),
        (col_type, "Type"),
        (col_login, "Login"),
        (col_logout, "Logout"),
        (col_total_time, "Total Time"),
        (col_first_call, "First Call"),
        (col_last_call, "Last Call"),
        (col_retail_time, "Retail Time"),
        (col_tc, "TC"),
        (col_pc, "PC"),
        (col_productivity, "Productivity"),
        (col_grade, "Retailing Grade"),
        (col_status, "Status"),
    ]

    keep_cols = []
    rename = {}
    for real_col, label in wanted:
        if real_col and real_col in emp_df.columns:
            keep_cols.append(real_col)
            rename[real_col] = label

    out = emp_df[keep_cols].copy().rename(columns=rename)
    if "Date" in out.columns:
        out = out.sort_values("Date")

    st.dataframe(out, use_container_width=True, hide_index=True)