import streamlit as st
import gspread
import pandas as pd
import hashlib

from google.oauth2.service_account import Credentials

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1uCHPSSdK4J4Ag-iXq-JkjDCQI8e5hM5OAZa7XUnJywg/edit?gid=0#gid=0"

# ----------------------------
# Service Account Loader
# ----------------------------
def load_service_account_creds():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    try:
        sa = st.secrets.get("SERVICE_ACCOUNT_JSON", None)
        if sa:
            return Credentials.from_service_account_info(dict(sa), scopes=scope)
    except Exception:
        pass

    return Credentials.from_service_account_file("service_account.json", scopes=scope)

# ----------------------------
# Google Sheets Client
# ----------------------------
@st.cache_resource
def get_gs_client():
    creds = load_service_account_creds()
    return gspread.authorize(creds)

# ----------------------------
# Sales helpers (moved here)
# ----------------------------
def df_hash(df: pd.DataFrame) -> str:
    raw = df.to_csv(index=False).encode("utf-8")
    return hashlib.md5(raw).hexdigest()

def normalize_state(s: str) -> str:
    return str(s).replace("\u00a0", " ").strip().lower()

def to_date_series(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s.astype(str).str.strip(), errors="coerce", dayfirst=True)
    return dt.dt.date

def fetch_df_smart(data_tab: str = "Orders", spreadsheet_url: str = SPREADSHEET_URL) -> pd.DataFrame:
    client = get_gs_client()
    sh = open_sheet_by_url_safe(client, spreadsheet_url)
    ws = worksheet_safe(sh, data_tab)
    values = get_all_values_safe(ws)

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

    col_idx = {k: find_col(v) for k, v in targets.items()}

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

def load_sales_data_cached(refresh_ms: int = 300000, data_tab: str = "Orders"):
    @st.cache_data(ttl=refresh_ms / 1000)
    def _cached():
        df = fetch_df_smart(data_tab=data_tab)
        return df, df_hash(df)
    return _cached()







import time
import random
import requests
import gspread

# ----------------------------
# Robust Google Sheets Helpers
# ----------------------------
def _is_timeout_err(e: Exception) -> bool:
    msg = str(e).lower()
    return (
        isinstance(e, requests.exceptions.ReadTimeout)
        or "read timed out" in msg
        or "timeout" in msg
        or "timed out" in msg
    )

def _is_retryable_api_err(e: Exception) -> bool:
    msg = str(e).lower()
    # gspread API errors / transient google issues
    return (
        _is_timeout_err(e)
        or "503" in msg
        or "500" in msg
        or "429" in msg
        or "rate limit" in msg
        or "quota" in msg
        or "unavailable" in msg
        or "backend error" in msg
        or "internal error" in msg
        or "connection reset" in msg
        or "remote disconnected" in msg
    )

def _with_retry(fn, *, tries: int = 4, base_sleep: float = 1.2):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if not _is_retryable_api_err(e):
                raise
            # exponential backoff + jitter
            sleep_s = base_sleep * (2 ** i) + random.random() * 0.3
            time.sleep(sleep_s)
    raise last

def open_sheet_by_url_safe(client: gspread.Client, url: str):
    return _with_retry(lambda: client.open_by_url(url))

def open_sheet_by_key_safe(client: gspread.Client, key: str):
    return _with_retry(lambda: client.open_by_key(key))

def worksheet_safe(sh, name: str):
    return _with_retry(lambda: sh.worksheet(name))

def get_all_values_safe(ws):
    return _with_retry(lambda: ws.get_all_values())

def get_all_records_safe(ws):
    return _with_retry(lambda: ws.get_all_records())