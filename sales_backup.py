import json
import secrets
import hashlib
import urllib.parse
import time
import hmac
import base64
import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import gspread
from google.oauth2.service_account import Credentials
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from streamlit_autorefresh import st_autorefresh

from streamlit_cookies_manager import EncryptedCookieManager


# ============================
# CONFIG
# ============================
SERVICE_ACCOUNT_FILE = "service_account.json"
OAUTH_CLIENT_FILE = "oauth_client.json"

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1uCHPSSdK4J4Ag-iXq-JkjDCQI8e5hM5OAZa7XUnJywg/edit?gid=0#gid=0"
DATA_TAB = "Sheet1"
ACCESS_TAB = "Sheet2"

REFRESH_MS = 300000  # 5 min


STATE_MAX_AGE = 10 * 60             # 10 minutes (OAuth state)
LOGIN_TTL_SECONDS = 7 * 24 * 3600   # 7 days

COOKIE_NAME = "tppl_auth"

# ============================
# ENV / SECRETS LOADER (Local + Streamlit Cloud)
# ============================
import os

def get_redirect_uri() -> str:
    """
    Local run: http://localhost:8501
    Cloud run: Streamlit Cloud ka public URL (secrets me set hoga)
    """
    try:
        v = st.secrets.get("REDIRECT_URI", None)
        if v:
            return str(v).strip()
    except Exception:
        pass
    return "http://localhost:8501"

REDIRECT_URI = get_redirect_uri()

def load_service_account_creds():
    """
    Priority:
      1) Streamlit Cloud secrets (SERVICE_ACCOUNT_JSON as dict)
      2) Local file: service_account.json
    """
    scope = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    # 1) cloud secrets
    try:
        sa = st.secrets.get("SERVICE_ACCOUNT_JSON", None)
        if sa:
            return Credentials.from_service_account_info(dict(sa), scopes=scope)
    except Exception:
        pass

    # 2) local file
    return Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scope)


def load_oauth_client_cfg() -> dict:
    """
    Priority:
      1) Streamlit Cloud secrets (OAUTH_CLIENT as dict)
      2) Local file: oauth_client.json
    """
    # 1) cloud secrets
    try:
        oc = st.secrets.get("OAUTH_CLIENT", None)
        if oc:
            web = dict(oc).get("web", dict(oc))
            return {
                "client_id": web["client_id"],
                "client_secret": web["client_secret"],
                "auth_uri": web.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth"),
                "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
            }
    except Exception:
        pass

    # 2) local file
    with open(OAUTH_CLIENT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    web = data.get("web", data)
    return {
        "client_id": web["client_id"],
        "client_secret": web["client_secret"],
        "auth_uri": web.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth"),
        "token_uri": web.get("token_uri", "https://oauth2.googleapis.com/token"),
    }



# ============================
# QUERY PARAM HELPERS
# ============================
def qp_get(name: str):
    try:
        val = st.query_params.get(name)
        if isinstance(val, list):
            return val[0] if val else None
        return val
    except Exception:
        pass

    try:
        d = st.experimental_get_query_params()
        v = d.get(name, [None])
        return v[0] if v else None
    except Exception:
        return None


def qp_clear(keys=None):
    if keys is None:
        try:
            st.query_params.clear()
            return
        except Exception:
            pass
        try:
            st.experimental_set_query_params()
        except Exception:
            pass
        return

    try:
        for k in keys:
            if k in st.query_params:
                del st.query_params[k]
        return
    except Exception:
        pass

    try:
        d = st.experimental_get_query_params()
        for k in keys:
            d.pop(k, None)
        st.experimental_set_query_params(**d)
    except Exception:
        pass


# ============================
# SECRETS
# ============================
def _get_state_secret() -> bytes:
    s = None
    try:
        s = st.secrets.get("STATE_SECRET", None)
    except Exception:
        s = None

    if not s:
        s = "CHANGE_ME__SET_STATE_SECRET_IN_STREAMLIT_SECRETS"

    return str(s).encode("utf-8")


# ============================
# COOKIE MANAGER (Persistent login)
# ============================
cookies = EncryptedCookieManager(
    prefix="tppl/",
    password=st.secrets.get("COOKIE_PASSWORD", "CHANGE_ME_COOKIE_PASSWORD_32CHARS_MIN"),
)

if not cookies.ready():
    st.stop()


def make_signed_login_token(email: str, ttl_seconds: int = LOGIN_TTL_SECONDS) -> str:
    exp = int(time.time()) + int(ttl_seconds)
    rnd = secrets.token_urlsafe(12)
    payload = f"{email}|{exp}|{rnd}".encode("utf-8")
    sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()

    p_b64 = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    s_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{p_b64}.{s_b64}"


def verify_login_token(token: str) -> str | None:
    try:
        p_b64, s_b64 = token.split(".", 1)

        def pad(x): return x + "=" * (-len(x) % 4)

        payload = base64.urlsafe_b64decode(pad(p_b64).encode("utf-8"))
        sig = base64.urlsafe_b64decode(pad(s_b64).encode("utf-8"))

        expected_sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return None

        txt = payload.decode("utf-8")
        email, exp_str, _ = txt.split("|", 2)
        exp = int(exp_str)

        if time.time() > exp:
            return None

        return email.strip().lower()
    except Exception:
        return None


# ============================
# OAuth STATE SIGNING
# ============================
def make_signed_state() -> str:
    ts = int(time.time())
    rnd = secrets.token_urlsafe(16)
    payload = f"{ts}:{rnd}".encode("utf-8")
    sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
    p_b64 = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    s_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{p_b64}.{s_b64}"


def verify_signed_state(state: str, max_age_sec: int = STATE_MAX_AGE) -> bool:
    try:
        p_b64, s_b64 = state.split(".", 1)

        def pad(x): return x + "=" * (-len(x) % 4)

        payload = base64.urlsafe_b64decode(pad(p_b64).encode("utf-8"))
        sig = base64.urlsafe_b64decode(pad(s_b64).encode("utf-8"))

        expected_sig = hmac.new(_get_state_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return False

        ts_str = payload.decode("utf-8").split(":", 1)[0]
        ts = int(ts_str)
        if abs(int(time.time()) - ts) > max_age_sec:
            return False

        return True
    except Exception:
        return False


# ============================
# AUTH: Service Account for Sheets
# ============================
@st.cache_resource
def get_gs_client():
    creds = load_service_account_creds()
    return gspread.authorize(creds)



@st.cache_resource
def load_oauth_client():
    return load_oauth_client_cfg()



def build_google_auth_url(client_id: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def exchange_code_for_tokens(code: str, client_id: str, client_secret: str, token_uri: str) -> dict:
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": get_redirect_uri(),
        "grant_type": "authorization_code",
    }
    resp = requests.post(
        token_uri,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=25,
    )
    resp.raise_for_status()
    return resp.json()


def verify_google_id_token(id_token_jwt: str, client_id: str) -> dict:
    req = google_requests.Request()
    return google_id_token.verify_oauth2_token(id_token_jwt, req, audience=client_id)


# ============================
# Common utils
# ============================
def norm_yes(x) -> bool:
    return str(x).strip().lower() in ("yes", "y", "true", "1")


def to_num(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)


def df_hash(df: pd.DataFrame) -> str:
    raw = df.to_csv(index=False).encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def normalize_state(s: str) -> str:
    return str(s).replace("\u00a0", " ").strip().lower()

def to_date_series(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s.astype(str).str.strip(), errors="coerce", dayfirst=True)
    return dt.dt.date




def parse_states(cell) -> set[str]:
    txt = str(cell).strip()
    if not txt:
        return set()
    parts = [p.strip() for p in txt.split(",") if p.strip()]
    norm = {normalize_state(p) for p in parts}
    if "all" in norm or "full" in norm:
        return {"__all__"}
    return norm


# ============================
# Sheet2: Access map
# ============================
@st.cache_data(ttl=60)
def load_access_map():
    client = get_gs_client()
    sh = client.open_by_url(SPREADSHEET_URL)
    ws = sh.worksheet(ACCESS_TAB)

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return {}

    rows = values[1:]
    access = {}
    for r in rows:
        email = (r[0] if len(r) > 0 else "").strip().lower()
        states_cell = (r[1] if len(r) > 1 else "").strip()
        if email:
            access[email] = parse_states(states_cell)
    return access


# ============================
# Sheet1 loader
# ============================
def fetch_df_smart():
    client = get_gs_client()
    sh = client.open_by_url(SPREADSHEET_URL)
    ws = sh.worksheet(DATA_TAB)

    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()

    def norm_cell(x):
        return str(x).replace("\u00a0", " ").strip().lower()

    targets = {
        "Customer/Vendor Name": ["customer/vendor name", "customer vendor name", "customer name", "party name"],
        "Item No.": ["item no.", "item no", "itemno", "item"],
        "Total": ["total"],
        "State": ["state"],
        "Dispatch": ["dispatch"],
        "Golden SKU": ["golden sku", "goldensku", "golden"],
        "Throughput Value": ["throughput value", "throughputvalue", "throughput val"],
        "Quantity": ["quantity", "qty", "qnty", "quant"],
        "Date": ["date", "order date", "created date"],


    }

    header_row_idx = None
    header = None
    for i in range(min(15, len(values))):
        row = [norm_cell(c) for c in values[i]]
        hit = 0
        for aliases in targets.values():
            if any(a in row for a in aliases):
                hit += 1
        if hit >= 3:
            header_row_idx = i
            header = row
            break

    if header_row_idx is None:
        header_row_idx = 0
        header = [norm_cell(c) for c in values[0]]

    def find_col(aliases):
        for a in aliases:
            if a in header:
                return header.index(a)
        return None

    col_idx = {
        "Customer/Vendor Name": find_col(targets["Customer/Vendor Name"]),
        "Item No.": find_col(targets["Item No."]),
        "Total": find_col(targets["Total"]),
        "State": find_col(targets["State"]),
        "Dispatch": find_col(targets["Dispatch"]),
        "Golden SKU": find_col(targets["Golden SKU"]),
        "Throughput Value": find_col(targets["Throughput Value"]),
        "Quantity": find_col(targets["Quantity"]),
        "Date": find_col(targets["Date"]),
    }

    critical = ["Customer/Vendor Name", "Item No.", "Total", "State", "Dispatch"]
    if any(col_idx[c] is None for c in critical):
        col_idx = {
            "Customer/Vendor Name": 3,
            "Date": 4,
            "Item No.": 6,
            "Quantity": 8,
            "Total": 9,
            "State": 17,
            "Dispatch": 18,
            "Golden SKU": 19,
            "Throughput Value": 21,
            
        }

    data_rows = values[header_row_idx + 1:]
    out = []
    max_needed = max(col_idx.values())

    for r in data_rows:
        if not any(str(x).strip() for x in r):
            continue
        if len(r) <= max_needed:
            r = r + [""] * (max_needed + 1 - len(r))

        out.append({
            "Customer/Vendor Name": r[col_idx["Customer/Vendor Name"]],
            "Item No.": r[col_idx["Item No."]],
            "Total": r[col_idx["Total"]],
            "State": r[col_idx["State"]],
            "Dispatch": r[col_idx["Dispatch"]],
            "Golden SKU": r[col_idx["Golden SKU"]],
            "Throughput Value": r[col_idx["Throughput Value"]],
            "Quantity": r[col_idx["Quantity"]],
            "Date": r[col_idx["Date"]],

        })

    return pd.DataFrame(out)


@st.cache_data(ttl=REFRESH_MS / 1000)
def load_data_cached():
    df = fetch_df_smart()
    return df, df_hash(df)


# ============================
# PLOTLY STYLE (black + readable labels)
# ============================
def apply_plot_style(fig: go.Figure, title: str):
    fig.update_layout(
        title=f"<b>{title}</b>",
        height=520,
        margin=dict(l=20, r=20, t=70, b=20),
        font=dict(color="black", size=13),
        legend=dict(font=dict(color="black", size=13)),
    )
    fig.update_xaxes(
        tickfont=dict(color="black", size=12),
        title_font=dict(color="black", size=13),
        showgrid=False,
    )
    fig.update_yaxes(
        tickfont=dict(color="black", size=12),
        title_font=dict(color="black", size=13),
        gridcolor="rgba(0,0,0,0.08)",
        zerolinecolor="rgba(0,0,0,0.20)",
    )
    return fig


# ============================
# Charts (UPDATED: % on bars + hover shows label/value/%)
# ============================
def top10_stacked_chart(df, group_col, value_col, dispatch_col, title):
    """
    Top 10 stacked chart:
    - Total = sum(value_col)
    - Dispatched = sum(value_col) where dispatch_col is YES/True/1
    - Pending = Total - Dispatched (>=0)
    Shows % on bars + hover with value/%/total
    """

    # ✅ Work on copy + normalize df columns (strip spaces)
    if df is None or getattr(df, "empty", True):
        st.info(f"{title}: No data to plot (after filters).")
        return

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # ✅ normalize input col names
    group_col = str(group_col).strip()
    value_col = str(value_col).strip()
    dispatch_col = str(dispatch_col).strip()

    # ✅ required columns check
    missing = [c for c in [group_col, dispatch_col, value_col] if c not in df.columns]
    if missing:
        st.warning(f"{title}: Missing columns {missing}. Available: {list(df.columns)}")
        return

    # ✅ select only needed cols
    tmp = df[[group_col, dispatch_col, value_col]].copy()

    # ✅ rename to internal names
    tmp = tmp.rename(columns={
        group_col: "__group__",
        dispatch_col: "__dispatch__",
        value_col: "__value__",
    })

    # ✅ cleanup group labels (avoid blank keys)
    tmp["__group__"] = tmp["__group__"].astype(str).str.replace("\u00a0", " ").str.strip()
    tmp = tmp[tmp["__group__"] != ""]
    if tmp.empty:
        st.info(f"{title}: No data to plot (after filters).")
        return

    # ✅ numeric + dispatch flag
    tmp["__value__"] = to_num(tmp["__value__"])
    tmp["__is_dispatch__"] = tmp["__dispatch__"].apply(norm_yes)

    # ✅ aggregates
    g_total = tmp.groupby("__group__", dropna=False)["__value__"].sum()
    g_dispatch = tmp[tmp["__is_dispatch__"]].groupby("__group__", dropna=False)["__value__"].sum()

    out = pd.DataFrame({"Total": g_total, "Dispatched": g_dispatch}).fillna(0)
    out["Pending"] = (out["Total"] - out["Dispatched"]).clip(lower=0)

    # ✅ Top 10 by Total
    out = out.sort_values("Total", ascending=False).head(10)

    if out.empty or float(out["Total"].sum()) == 0:
        st.info(f"{title}: No data to plot (after filters).")
        return

    # ✅ build arrays
    x = out.index.astype(str).tolist()
    total = out["Total"].astype(float).values
    disp = out["Dispatched"].astype(float).values
    pend = out["Pending"].astype(float).values

    # ✅ Percent of total per group (safe)
    disp_pct = []
    pend_pct = []
    for d, p, t in zip(disp, pend, total):
        if t and t > 0:
            disp_pct.append((d / t) * 100.0)
            pend_pct.append((p / t) * 100.0)
        else:
            disp_pct.append(0.0)
            pend_pct.append(0.0)

    disp_text = [f"{p:.1f}%" for p in disp_pct]
    pend_text = [f"{p:.1f}%" for p in pend_pct]

    # ✅ Plot
    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            name="Dispatched (Sale)",
            x=x,
            y=disp,
            text=disp_text,
            textposition="inside",
            insidetextanchor="middle",
            customdata=list(zip(disp_pct, total)),
            hovertemplate="<b>%{x}</b><br>"
                          "Value: %{y:,.2f}<br>"
                          "Percent: %{customdata[0]:.2f}%<br>"
                          "Total: %{customdata[1]:,.2f}<extra></extra>",
        )
    )

    fig.add_trace(
        go.Bar(
            name="Not Dispatched",
            x=x,
            y=pend,
            text=pend_text,
            textposition="inside",
            insidetextanchor="middle",
            customdata=list(zip(pend_pct, total)),
            hovertemplate="<b>%{x}</b><br>"
                          "Value: %{y:,.2f}<br>"
                          "Percent: %{customdata[0]:.2f}%<br>"
                          "Total: %{customdata[1]:,.2f}<extra></extra>",
        )
    )

    fig.update_layout(barmode="stack")
    apply_plot_style(fig, title)
    st.plotly_chart(fig, use_container_width=True)

    


def top10_sku_by_type_chart(df, item_col, golden_col, dispatch_col, value_col, sku_type: str):
    tmp = df[[item_col, golden_col, dispatch_col, value_col]].copy()
    tmp = tmp.rename(columns={item_col: "__sku__", golden_col: "__gold__", dispatch_col: "__dispatch__", value_col: "__value__"})
    tmp["__value__"] = to_num(tmp["__value__"])
    is_golden = tmp["__gold__"].apply(norm_yes)

    if sku_type.lower() == "golden":
        tmp = tmp[is_golden]
        title = f"Top 10 GOLDEN SKU — {value_col} (Total vs Dispatched)"
    else:
        tmp = tmp[~is_golden]
        title = f"Top 10 REGULAR SKU — {value_col} (Total vs Dispatched)"

    tmp["__sku__"] = tmp["__sku__"].astype(str).str.strip()
    tmp = tmp[tmp["__sku__"] != ""]
    if tmp.empty:
        st.info("No SKU rows after filters.")
        return

    tmp2 = tmp.rename(columns={"__sku__": "SKU", "__dispatch__": "DispatchTmp", "__value__": "ValueTmp"})
    top10_stacked_chart(tmp2, "SKU", "ValueTmp", "DispatchTmp", title)
    


def top10_dispatched_mix_chart(df, group_col, value_col, dispatch_col, golden_col, title):
    """
    Top 10 by DISPATCHED VALUE only.
    Stacked = Golden vs Regular within dispatched.
    Bars show % (within dispatched for that group)
    Hover shows label + value + %.
    """
    tmp = df[[group_col, dispatch_col, value_col, golden_col]].copy()
    tmp = tmp.rename(columns={
        group_col: "__group__",
        dispatch_col: "__dispatch__",
        value_col: "__value__",
        golden_col: "__gold__",
    })

    tmp["__value__"] = to_num(tmp["__value__"])
    tmp["__is_dispatch__"] = tmp["__dispatch__"].apply(norm_yes)
    tmp["__is_golden__"] = tmp["__gold__"].apply(norm_yes)

    # ✅ only dispatched rows
    tmp = tmp[tmp["__is_dispatch__"]].copy()

    if tmp.empty:
        st.info("No dispatched data to plot (after filters).")
        return

    # group totals by golden/regular
    g_golden = tmp[tmp["__is_golden__"]].groupby("__group__", dropna=False)["__value__"].sum()
    g_regular = tmp[~tmp["__is_golden__"]].groupby("__group__", dropna=False)["__value__"].sum()

    out = pd.DataFrame({"Golden": g_golden, "Regular": g_regular}).fillna(0)
    out["DispatchedTotal"] = out["Golden"] + out["Regular"]

    # top 10 by dispatched total
    out = out.sort_values("DispatchedTotal", ascending=False).head(10)

    if out.empty:
        st.info("No data to plot (after filters).")
        return

    x = out.index.astype(str).tolist()
    total = out["DispatchedTotal"].values
    golden = out["Golden"].values
    regular = out["Regular"].values

    # % within dispatched total
    golden_pct = [(0 if t == 0 else (g / t * 100)) for g, t in zip(golden, total)]
    regular_pct = [(0 if t == 0 else (r / t * 100)) for r, t in zip(regular, total)]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Golden",
        x=x,
        y=golden,
        text=[f"{p:.1f}%" for p in golden_pct],
        textposition="inside",
        insidetextanchor="middle",
        customdata=list(zip(golden_pct, total)),
        hovertemplate="<b>%{x}</b><br>"
                      "Golden Value: %{y:,.2f}<br>"
                      "Golden %: %{customdata[0]:.2f}%<br>"
                      "Dispatched Total: %{customdata[1]:,.2f}<extra></extra>",
    ))

    fig.add_trace(go.Bar(
        name="Regular",
        x=x,
        y=regular,
        text=[f"{p:.1f}%" for p in regular_pct],
        textposition="inside",
        insidetextanchor="middle",
        customdata=list(zip(regular_pct, total)),
        hovertemplate="<b>%{x}</b><br>"
                      "Regular Value: %{y:,.2f}<br>"
                      "Regular %: %{customdata[0]:.2f}%<br>"
                      "Dispatched Total: %{customdata[1]:,.2f}<extra></extra>",
    ))

    fig.update_layout(barmode="stack")
    apply_plot_style(fig, title)
    st.plotly_chart(fig, use_container_width=True)

def top10_stacked_chart_qty(df, group_col, value_col, dispatch_col, title):
    """
    Quantity version of Top 10 stacked chart:
    - Total Qty = sum(value_col)
    - Dispatched Qty = sum(value_col) where dispatch_col is YES/True/1
    - Pending Qty = Total - Dispatched (>=0)
    """

    # 1) empty guard
    if df is None or getattr(df, "empty", True):
        st.info(f"{title}: No data to plot (after filters).")
        return

    # 2) copy + normalize column names
    df = df.copy()
    df.columns = [str(c).replace("\u00a0", " ").strip() for c in df.columns]

    # 3) normalize input col names
    group_col = str(group_col).replace("\u00a0", " ").strip()
    value_col = str(value_col).replace("\u00a0", " ").strip()
    dispatch_col = str(dispatch_col).replace("\u00a0", " ").strip()

    # 4) required columns check
    missing = [c for c in [group_col, dispatch_col, value_col] if c not in df.columns]
    if missing:
        st.warning(f"{title}: Missing columns {missing}. Available: {list(df.columns)}")
        return

    # 5) select needed cols
    tmp = df[[group_col, dispatch_col, value_col]].copy()

    # 6) rename to internal names
    tmp = tmp.rename(columns={
        group_col: "__group__",
        dispatch_col: "__dispatch__",
        value_col: "__value__",
    })

    # 7) validate rename happened
    if "__group__" not in tmp.columns or "__dispatch__" not in tmp.columns or "__value__" not in tmp.columns:
        st.warning(f"{title}: Column rename failed. Columns now: {list(tmp.columns)}")
        return

    # 8) clean group labels (avoid blank keys)
    tmp["__group__"] = tmp["__group__"].astype(str).replace("nan", "").str.strip()
    tmp = tmp[tmp["__group__"] != ""]
    if tmp.empty:
        st.info(f"{title}: No data to plot (after filters).")
        return

    # 9) numeric + dispatch flag
    tmp["__value__"] = to_num(tmp["__value__"])
    tmp["__is_dispatch__"] = tmp["__dispatch__"].apply(norm_yes)

    # 10) aggregates
    g_total = tmp.groupby("__group__", dropna=False)["__value__"].sum()
    g_dispatch = tmp[tmp["__is_dispatch__"]].groupby("__group__", dropna=False)["__value__"].sum()

    out = pd.DataFrame({"Total": g_total, "Dispatched": g_dispatch}).fillna(0)
    out["Pending"] = (out["Total"] - out["Dispatched"]).clip(lower=0)

    out = out.sort_values("Total", ascending=False).head(10)

    if out.empty or float(out["Total"].sum()) == 0:
        st.info(f"{title}: No data to plot (after filters).")
        return

    x = out.index.astype(str).tolist()
    total = out["Total"].astype(float).values
    disp = out["Dispatched"].astype(float).values
    pend = out["Pending"].astype(float).values

    disp_pct, pend_pct = [], []
    for d, p, t in zip(disp, pend, total):
        if t and t > 0:
            disp_pct.append((d / t) * 100.0)
            pend_pct.append((p / t) * 100.0)
        else:
            disp_pct.append(0.0)
            pend_pct.append(0.0)

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Dispatched (Qty)",
        x=x,
        y=disp,
        text=[f"{p:.1f}%" for p in disp_pct],
        textposition="inside",
        insidetextanchor="middle",
        customdata=list(zip(disp_pct, total)),
        hovertemplate="<b>%{x}</b><br>"
                      "Qty: %{y:,.0f}<br>"
                      "Percent: %{customdata[0]:.2f}%<br>"
                      "Total Qty: %{customdata[1]:,.0f}<extra></extra>",
    ))

    fig.add_trace(go.Bar(
        name="Not Dispatched (Qty)",
        x=x,
        y=pend,
        text=[f"{p:.1f}%" for p in pend_pct],
        textposition="inside",
        insidetextanchor="middle",
        customdata=list(zip(pend_pct, total)),
        hovertemplate="<b>%{x}</b><br>"
                      "Qty: %{y:,.0f}<br>"
                      "Percent: %{customdata[0]:.2f}%<br>"
                      "Total Qty: %{customdata[1]:,.0f}<extra></extra>",
    ))

    fig.update_layout(barmode="stack")
    apply_plot_style(fig, title)
    st.plotly_chart(fig, use_container_width=True)


def top10_sku_by_type_chart_qty(df, item_col, golden_col, dispatch_col, qty_col, sku_type: str):
    tmp = df[[item_col, golden_col, dispatch_col, qty_col]].copy()
    tmp = tmp.rename(columns={item_col: "__sku__", golden_col: "__gold__", dispatch_col: "__dispatch__", qty_col: "__qty__"})
    tmp["__qty__"] = to_num(tmp["__qty__"])
    is_golden = tmp["__gold__"].apply(norm_yes)

    if sku_type.lower() == "golden":
        tmp = tmp[is_golden]
        title = "Top 10 GOLDEN Items — Quantity (Dispatched vs Not)"
    else:
        tmp = tmp[~is_golden]
        title = "Top 10 REGULAR Items — Quantity (Dispatched vs Not)"

    tmp2 = tmp.rename(columns={"__sku__": "SKU", "__dispatch__": "DispatchTmp", "__qty__": "QtyTmp"})
    top10_stacked_chart_qty(tmp2, "SKU", "QtyTmp", "DispatchTmp", title)

def top10_loss_chart(df, group_col, value_col, dispatch_col, title):
    """
    LOSS = Not Dispatched value only (Dispatch != YES)
    Shows Top10 groups by Loss value.
    Single bar (loss) with % of total loss share.
    Hover shows label + loss value + % + overall loss total.
    """
    tmp = df[[group_col, dispatch_col, value_col]].copy()
    tmp = tmp.rename(columns={group_col: "__group__", dispatch_col: "__dispatch__", value_col: "__value__"})
    tmp["__value__"] = to_num(tmp["__value__"])
    tmp["__is_dispatch__"] = tmp["__dispatch__"].apply(norm_yes)

    # ✅ only not dispatched rows = LOSS rows
    tmp = tmp[~tmp["__is_dispatch__"]].copy()
    if tmp.empty:
        st.info("No loss data (after filters).")
        return

    g_loss = tmp.groupby("__group__", dropna=False)["__value__"].sum().sort_values(ascending=False).head(10)

    if g_loss.empty:
        st.info("No loss data (after filters).")
        return

    overall_loss = float(g_loss.sum())
    x = g_loss.index.astype(str).tolist()
    y = g_loss.values

    # % share of total loss (within top10 display)
    pct = [(0 if overall_loss == 0 else (v / overall_loss * 100)) for v in y]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Loss (Not Dispatched)",
        x=x,
        y=y,
        text=[f"{v:,.0f} ({p:.1f}%)" for v, p in zip(y, pct)],
        textposition="inside",
        insidetextanchor="middle",
        cliponaxis=False,
        customdata=list(zip(pct, [overall_loss]*len(y))),
        hovertemplate="<b>%{x}</b><br>"
                      "Loss Value: %{y:,.2f}<br>"
                    "Share: %{customdata[0]:.2f}%<br>"
                    "Top10 Loss Total: %{customdata[1]:,.2f}<extra></extra>",
    ))


    apply_plot_style(fig, title)
    st.plotly_chart(fig, use_container_width=True)

def top10_loss_sku_by_type_chart(df, item_col, golden_col, dispatch_col, value_col, sku_type: str):
    """
    LOSS only (not dispatched).
    Shows Top10 SKU for Golden OR Regular based on Golden SKU flag.
    """
    tmp = df[[item_col, golden_col, dispatch_col, value_col]].copy()
    tmp = tmp.rename(columns={
        item_col: "__sku__",
        golden_col: "__gold__",
        dispatch_col: "__dispatch__",
        value_col: "__value__",
    })
    tmp["__value__"] = to_num(tmp["__value__"])
    tmp["__is_dispatch__"] = tmp["__dispatch__"].apply(norm_yes)
    tmp["__is_golden__"] = tmp["__gold__"].apply(norm_yes)

    # ✅ only loss rows
    tmp = tmp[~tmp["__is_dispatch__"]].copy()

    if sku_type.lower() == "golden":
        tmp = tmp[tmp["__is_golden__"]]
        title = f"Top 10 LOSS — GOLDEN SKU • {value_col}"
    else:
        tmp = tmp[~tmp["__is_golden__"]]
        title = f"Top 10 LOSS — REGULAR SKU • {value_col}"

    if tmp.empty:
        st.info("No loss data for this SKU type (after filters).")
        return

    # reuse loss chart
    tmp2 = tmp.rename(columns={"__sku__": "SKU", "__dispatch__": "DispatchTmp", "__value__": "ValueTmp"})
    top10_loss_chart(tmp2, "SKU", "ValueTmp", "DispatchTmp", title)





def sku_customer_penetration_table(df, item_col, cust_col):
    tmp = df[[item_col, cust_col]].copy()
    tmp = tmp.rename(columns={item_col: "__item__", cust_col: "__cust__"})
    tmp["__cust__"] = tmp["__cust__"].astype(str).str.strip()

    total_customers = tmp["__cust__"].nunique(dropna=True)
    if total_customers == 0:
        st.info("No customers found.")
        return

    sku_unique = tmp.groupby("__item__", dropna=False)["__cust__"].nunique().reset_index(name="Unique Customers")
    sku_unique["% Customers"] = (sku_unique["Unique Customers"] / total_customers * 100).round(2)
    sku_unique = sku_unique.sort_values(["Unique Customers", "__item__"], ascending=[False, True])
    sku_unique = sku_unique.rename(columns={"__item__": "Item No."})
    st.dataframe(sku_unique, use_container_width=True)


# ============================
# APP START
# ============================
st.set_page_config(page_title="Sales Dashboard - Data Source:primary", layout="wide")

if "user_email" not in st.session_state:
    st.session_state.user_email = None

oauth = load_oauth_client()
access_map = load_access_map()

# ✅ Restore login from cookie (refresh won't logout)
if st.session_state.user_email is None:
    tok = cookies.get(COOKIE_NAME)
    if tok:
        email_from_cookie = verify_login_token(tok)
        if email_from_cookie and (email_from_cookie in access_map):
            st.session_state.user_email = email_from_cookie

# ---- OAuth callback ----
code = qp_get("code")
state_qp = qp_get("state")

if code and st.session_state.user_email is None:
    try:
        if not state_qp or not verify_signed_state(state_qp):
            st.error("OAuth state mismatch / expired. Please login again.")
            qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
        else:
            tokens = exchange_code_for_tokens(code, oauth["client_id"], oauth["client_secret"], oauth["token_uri"])
            idt = tokens.get("id_token")
            if not idt:
                st.error("No id_token received from Google.")
                qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
            else:
                claims = verify_google_id_token(idt, oauth["client_id"])
                email = str(claims.get("email", "")).lower().strip()
                verified = bool(claims.get("email_verified", False))

                if not verified:
                    st.error("Your Google email is not verified.")
                    qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                elif email not in access_map:
                    st.error("Access denied: your email is not present in Sheet2.")
                    qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                else:
                    st.session_state.user_email = email
                    cookies[COOKIE_NAME] = make_signed_login_token(email)
                    cookies.save()

                    qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
                    st.rerun()
    except Exception as e:
        st.error(f"Login failed: {e}")
        qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])

# ---- Sidebar ----
with st.sidebar:
    st.subheader("Google Login")

    if st.session_state.user_email:
        st.success(f"Logged in as: {st.session_state.user_email}")

        if st.button("Logout"):
            st.session_state.user_email = None

            if cookies.get(COOKIE_NAME):
                del cookies[COOKIE_NAME]
                cookies.save()

            st.cache_data.clear()
            qp_clear(keys=["code", "state", "scope", "authuser", "prompt"])
            st.rerun()
    else:
        st.info("Please sign in with Google.")
        if st.button("Sign in with Google"):
            signed_state = make_signed_state()
            url = build_google_auth_url(oauth["client_id"], signed_state)
            st.link_button("➡️ Continue with Google", url)
            st.stop()

    st.divider()
    st.subheader("Refresh")
    if st.button("🔄 Refresh now"):
        st.cache_data.clear()
        st.rerun()

st.title("📊 Sales Dashboard - Data Source : Primary Data (SAP Data)")

if not st.session_state.user_email:
    st.warning("Please login from the left sidebar to view dashboard.")
    st.stop()

st_autorefresh(interval=REFRESH_MS, key="gs_refresh")

# ============================
# LOAD + FILTER
# ============================
df, h = load_data_cached()

COL_CUST = "Customer/Vendor Name"
COL_ITEM = "Item No."
COL_TOTAL = "Total"
COL_STATE = "State"
COL_DISP = "Dispatch"
COL_GOLD = "Golden SKU"
COL_TPUT = "Throughput Value"
COL_QTY = "Quantity"
COL_DATE = "Date"


df["_state_norm"] = df[COL_STATE].apply(normalize_state)
allowed_states = access_map.get(st.session_state.user_email, set())

if "__all__" in allowed_states:
    df_view = df.copy()
    st.caption(f"Access: ALL • Rows: {len(df_view):,} • Hash: `{h}`")
else:
    df_view = df[df["_state_norm"].isin(allowed_states)].copy()
    st.caption(f"Access states: {', '.join(sorted(allowed_states))} • Rows: {len(df_view):,} • Hash: `{h}`")

if df_view.empty:
    st.warning("No rows after state filtering. Check Sheet2 states vs Sheet1 State values.")
    st.stop()

# ============================
# GLOBAL FILTERS (Date + Customer + SKU + State)
# ============================
df_view["_date_parsed"] = to_date_series(df_view[COL_DATE])
df_view["_cust_norm"] = df_view[COL_CUST].astype(str).str.strip()
df_view["_sku_norm"] = df_view[COL_ITEM].astype(str).str.strip()
df_view["_state_raw"] = df_view[COL_STATE].astype(str).str.strip()

# Options
cust_opts = sorted([x for x in df_view["_cust_norm"].dropna().unique() if x])
sku_opts = sorted([x for x in df_view["_sku_norm"].dropna().unique() if x])
state_opts = sorted([x for x in df_view["_state_raw"].dropna().unique() if x])

dates_clean = df_view["_date_parsed"].dropna()
min_d = dates_clean.min() if not dates_clean.empty else None
max_d = dates_clean.max() if not dates_clean.empty else None


# --- Filter session keys (MUST be before widgets)
if "f_date" not in st.session_state:
    st.session_state.f_date = None
if "f_states" not in st.session_state:
    st.session_state.f_states = []
if "f_customers" not in st.session_state:
    st.session_state.f_customers = []
if "f_skus" not in st.session_state:
    st.session_state.f_skus = []


from datetime import date, datetime

def _as_pydate(x):
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    try:
        return pd.to_datetime(x, errors="coerce").date()
    except Exception:
        return None


with st.expander("🔎 Filters", expanded=True):
    b1, _ = st.columns([0.22, 0.78])
    with b1:
        if st.button("♻️ Reset Filters"):
            if "f_date" in st.session_state:
                del st.session_state["f_date"]
            if "date_widget" in st.session_state:
                del st.session_state["date_widget"]

            st.session_state.f_states = []
            st.session_state.f_customers = []
            st.session_state.f_skus = []
            st.rerun()

    f1, f2, f3, f4 = st.columns([1.2, 1, 1, 1])

    # --- Date Range (inside f1)
    with f1:
        dmin = _as_pydate(min_d) or date.today()
        dmax = _as_pydate(max_d) or dmin

        cur = st.session_state.get("f_date", None)

        if isinstance(cur, (list, tuple)) and len(cur) == 2:
            s = _as_pydate(cur[0])
            e = _as_pydate(cur[1])
            widget_value = (s, e) if (s and e) else (dmin, dmax)
        else:
            widget_value = (dmin, dmax)

        picked = st.date_input(
            "Date Range",
            value=widget_value,
            key="date_widget",
        )

        # store back
        if isinstance(picked, tuple) and len(picked) == 2:
            st.session_state.f_date = (picked[0], picked[1])
        else:
            st.session_state.f_date = (picked, picked)

    # --- Other filters (inside f2 f3 f4)
    with f2:
        st.multiselect(
            "States",
            options=state_opts,
            key="f_states",
            placeholder="Choose options",
        )

    with f3:
        st.multiselect(
            "Customers",
            options=cust_opts,
            key="f_customers",
            placeholder="Choose options",
        )

    with f4:
        st.multiselect(
            "SKU (Item No.)",
            options=sku_opts,
            key="f_skus",
            placeholder="Choose options",
        )


date_range = st.session_state.f_date
sel_states = st.session_state.f_states
sel_customers = st.session_state.f_customers
sel_skus = st.session_state.f_skus






# Apply filters
df_f = df_view.copy()

# Apply filters
df_f = df_view.copy()

date_range = st.session_state.f_date
sel_states = st.session_state.f_states
sel_customers = st.session_state.f_customers
sel_skus = st.session_state.f_skus

# Date filter
if isinstance(date_range, tuple) and len(date_range) == 2 and date_range[0] and date_range[1]:
    d1, d2 = date_range
    df_f = df_f[df_f["_date_parsed"].between(d1, d2, inclusive="both")]


# Dropdown filters
if sel_states:
    df_f = df_f[df_f["_state_raw"].isin(sel_states)]

if sel_customers:
    df_f = df_f[df_f["_cust_norm"].isin(sel_customers)]

if sel_skus:
    df_f = df_f[df_f["_sku_norm"].isin(sel_skus)]

df_view = df_f



if df_view.empty:
    st.warning("No rows after applying filters.")
    st.stop()


# ============================
# KPI STRIP (4 KPIs only)
# ============================
tmp_kpi = df_view[[COL_TOTAL, COL_TPUT, COL_DISP, COL_CUST]].copy()
tmp_kpi["_total_val"] = to_num(tmp_kpi[COL_TOTAL])
tmp_kpi["_tput_val"] = to_num(tmp_kpi[COL_TPUT])
tmp_kpi["_is_dispatch"] = tmp_kpi[COL_DISP].apply(norm_yes)

order_value = float(tmp_kpi["_total_val"].sum())
sale_value = float(tmp_kpi.loc[tmp_kpi["_is_dispatch"], "_total_val"].sum())  # dispatched order value
loss_value = float(tmp_kpi.loc[~tmp_kpi["_is_dispatch"], "_total_val"].sum())  # not dispatched order value
customers = int(df_view[COL_CUST].astype(str).str.strip().replace("", pd.NA).dropna().nunique())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Order value", f"{order_value:,.2f}")
k2.metric("Sale Value (Dispatched)", f"{sale_value:,.2f}")
k3.metric("Loss of Sale", f"{loss_value:,.2f}")
k4.metric("Customers", f"{customers:,}")


# ============================
# DASHBOARD
# ============================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Order Value",
    "Throughput Points",
    "SKU Penetration Table",
    "Dispatched  — Order Value",
    "Dispatched — Throughput Points",
    "Quantity (Dispatched vs Not)",
    "LOSS — Order Value",
    "LOSS — Throughput Points",
])





with tab1:
    c1, c2 = st.columns(2)
    with c1:
        top10_stacked_chart(df_view, COL_CUST, COL_TOTAL, COL_DISP, "Top 10 Customers — Order Value vs Dispatched")
    with c2:
        top10_stacked_chart(df_view, COL_ITEM, COL_TOTAL, COL_DISP, "Top 10 SKU — Order Value vs Dispatched")

    c3, c4 = st.columns(2)
    with c3:
        top10_stacked_chart(df_view, COL_STATE, COL_TOTAL, COL_DISP, "Top 10 States — Order Value vs Dispatched")
    with c4:
        top10_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TOTAL, "Golden")
    c5, c6 = st.columns(2)
    with c5:    
        top10_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TOTAL, "Regular")

with tab2:
    c1, c2 = st.columns(2)
    with c1:
        top10_stacked_chart(df_view, COL_CUST, COL_TPUT, COL_DISP, "Top 10 Customers — Throughput vs Dispatched")
    with c2:
        top10_stacked_chart(df_view, COL_ITEM, COL_TPUT, COL_DISP, "Top 10 SKU — Throughput vs Dispatched")

    c3, c4 = st.columns(2)
    with c3:
        top10_stacked_chart(df_view, COL_STATE, COL_TPUT, COL_DISP, "Top 10 States — Throughput vs Dispatched")
    with c4:
        st.write("### Golden vs Regular (Throughput Value)")
        top10_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TPUT, "Golden")
        top10_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TPUT, "Regular")

with tab3:
    st.write("### SKU customer penetration")
    sku_customer_penetration_table(df_view, COL_ITEM, COL_CUST)

with tab4:
    st.write("### Top 10 Dispatched — Golden vs Regular Mix")

    c1, c2 = st.columns(2)
    with c1:
        top10_dispatched_mix_chart(
            df_view,
            group_col=COL_CUST,
            value_col=COL_TOTAL,
            dispatch_col=COL_DISP,
            golden_col=COL_GOLD,
            title="Top 10 Customers — Dispatched Mix (Golden vs Regular)"
        )
    with c2:
        top10_dispatched_mix_chart(
            df_view,
            group_col=COL_ITEM,
            value_col=COL_TOTAL,
            dispatch_col=COL_DISP,
            golden_col=COL_GOLD,
            title="Top 10 SKU — Dispatched Mix (Golden vs Regular)"
        )

    c3, c4 = st.columns(2)
    with c3:
        top10_dispatched_mix_chart(
            df_view,
            group_col=COL_STATE,
            value_col=COL_TOTAL,
            dispatch_col=COL_DISP,
            golden_col=COL_GOLD,
            title="Top 10 States — Dispatched Mix (Golden vs Regular)"
        )


with tab5:
    st.write("### Top 10 Dispatched — Golden vs Regular Mix (Throughput Value)")

    c1, c2 = st.columns(2)
    with c1:
        top10_dispatched_mix_chart(
            df_view,
            group_col=COL_CUST,
            value_col=COL_TPUT,      # ✅ throughput here
            dispatch_col=COL_DISP,
            golden_col=COL_GOLD,
            title="Top 10 Customers — Dispatched Mix (Golden vs Regular) • Throughput"
        )
    with c2:
        top10_dispatched_mix_chart(
            df_view,
            group_col=COL_ITEM,
            value_col=COL_TPUT,      # ✅ throughput here
            dispatch_col=COL_DISP,
            golden_col=COL_GOLD,
            title="Top 10 SKU — Dispatched Mix (Golden vs Regular) • Throughput"
        )

    c3, c4 = st.columns(2)
    with c3:
        top10_dispatched_mix_chart(
            df_view,
            group_col=COL_STATE,
            value_col=COL_TPUT,      # ✅ throughput here
            dispatch_col=COL_DISP,
            golden_col=COL_GOLD,
            title="Top 10 States — Dispatched Mix (Golden vs Regular) • Throughput"
        )

with tab6:
    st.write("### Quantity — Dispatched vs Not Dispatched (Top 10)")

    c1, c2 = st.columns(2)
    with c1:
        top10_stacked_chart_qty(df_view, COL_CUST, COL_QTY, COL_DISP, "Top 10 Customers — Quantity (Dispatched vs Not)")
    with c2:
        top10_stacked_chart_qty(df_view, COL_ITEM, COL_QTY, COL_DISP, "Top 10 SKU — Quantity (Dispatched vs Not)")

    c3, c4 = st.columns(2)
    with c3:
        top10_stacked_chart_qty(df_view, COL_STATE, COL_QTY, COL_DISP, "Top 10 States — Quantity (Dispatched vs Not)")
    with c4:
        top10_sku_by_type_chart_qty(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_QTY, "Golden")

    c5, c6 = st.columns(2)
    with c5:
        top10_sku_by_type_chart_qty(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_QTY, "Regular")

with tab7:
    st.write("### LOSS Analysis — Order Value (Not Dispatched only)")

    c1, c2 = st.columns(2)
    with c1:
        top10_loss_chart(df_view, COL_CUST, COL_TOTAL, COL_DISP, "Top 10 LOSS Customers — Order Value")
    with c2:
        top10_loss_chart(df_view, COL_ITEM, COL_TOTAL, COL_DISP, "Top 10 LOSS SKU — Order Value")

    c3, c4 = st.columns(2)
    with c3:
        top10_loss_chart(df_view, COL_STATE, COL_TOTAL, COL_DISP, "Top 10 LOSS States — Order Value")
    with c4:
        top10_loss_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TOTAL, "Golden")

    c5, c6 = st.columns(2)
    with c5:
        top10_loss_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TOTAL, "Regular")

with tab8:
    st.write("### LOSS Analysis — Throughput Points (Not Dispatched only)")

    c1, c2 = st.columns(2)
    with c1:
        top10_loss_chart(df_view, COL_CUST, COL_TPUT, COL_DISP, "Top 10 LOSS Customers — Throughput Points")
    with c2:
        top10_loss_chart(df_view, COL_ITEM, COL_TPUT, COL_DISP, "Top 10 LOSS SKU — Throughput Points")

    c3, c4 = st.columns(2)
    with c3:
        top10_loss_chart(df_view, COL_STATE, COL_TPUT, COL_DISP, "Top 10 LOSS States — Throughput Points")
    with c4:
        top10_loss_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TPUT, "Golden")

    c5, c6 = st.columns(2)
    with c5:
        top10_loss_sku_by_type_chart(df_view, COL_ITEM, COL_GOLD, COL_DISP, COL_TPUT, "Regular")
