import streamlit as st
import pandas as pd
from datetime import date
import time
import random
import math

from utils.access import get_user_access, load_employee_list
from utils.gsheets import get_gs_client, SPREADSHEET_URL

# =========================
# CONFIG
# =========================
SHEET_NAME = "FA Sales Data"
CACHE_TTL = 300  # seconds


# =========================
# UI (professional + readable)
# =========================
st.markdown(
    """
<style>
.stApp { background:#ffffff; }
h1,h2,h3 { color:#0f172a; font-weight:800; }
.small { color:#64748b; font-size:12px; }

.card {
  background:#ffffff;
  border:1px solid #e2e8f0;
  border-radius:16px;
  padding:12px 14px;
  margin: 10px 0 14px 0;
}

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
  font-size:24px !important;
}

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
def _dedupe_headers(headers: list[str]) -> list[str]:
    out = []
    seen = {}
    for h in headers:
        key = str(h).strip()
        if key == "":
            key = "BLANK"
        if key not in seen:
            seen[key] = 1
            out.append(key)
        else:
            seen[key] += 1
            out.append(f"{key}__{seen[key]}")
    return out

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

def _safe_series(df: pd.DataFrame, col: str | None) -> pd.Series:
    if not col or col not in df.columns:
        return pd.Series([""] * len(df))
    x = df[col]
    if isinstance(x, pd.DataFrame):
        return x.iloc[:, 0]
    return x

import re

def _to_num(s: pd.Series) -> pd.Series:
    """
    Robust numeric parser for Google Sheets:
    - handles commas: 1,23,456.78
    - handles currency symbols: ₹, Rs, etc.
    - handles accounting negatives: (123.45)
    - removes NBSP and stray spaces
    """
    if s is None:
        return pd.Series([], dtype="float64")

    x = s.astype(str)

    # normalize spaces
    x = x.str.replace("\u00a0", " ", regex=False).str.strip()

    # accounting negative: (123.45) -> -123.45
    x = x.str.replace(r"^\((.*)\)$", r"-\1", regex=True)

    # remove currency / letters, keep digits, dot, minus, comma
    x = x.str.replace(r"[^0-9\-,\.]", "", regex=True)

    # remove commas
    x = x.str.replace(",", "", regex=False)

    # fix multiple minus signs (just in case)
    x = x.str.replace(r"^-+", "-", regex=True)

    out = pd.to_numeric(x, errors="coerce").fillna(0.0)
    return out

def _current_month_range():
    today = date.today()
    start = today.replace(day=1)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    end = (pd.to_datetime(next_month) - pd.Timedelta(days=1)).date()
    return start, end

def _clamp_range(d1: date, d2: date):
    if d2 < d1:
        return d2, d1
    return d1, d2

def _safe_str(x) -> str:
    return str(x).replace("\u00a0", " ").strip()

def _retry(fn, tries: int = 4, base_sleep: float = 1.2):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e).lower()
            retryable = any(k in msg for k in [
                "timed out", "timeout", "read timed out", "503", "500", "429",
                "unavailable", "backend error", "connection reset", "remote disconnected"
            ])
            if not retryable:
                raise
            time.sleep(base_sleep * (2 ** i) + random.random() * 0.3)
    raise last

def _is_golden(val: str) -> bool:
    return _safe_str(val).lower() == "yes"

def _channel(val: str) -> str:
    v = _safe_str(val)
    if not v:
        return "Uncategorized"
    vv = v.lower()
    if vv == "dd":
        return "DD"
    if vv == "retailer":
        return "Retailer"
    return v

def _money(x: float) -> str:
    return f"{x:,.2f}"

def _pct_str(x: float) -> str:
    try:
        return f"{float(x):.1f}%"
    except Exception:
        return "0.0%"


# =========================
# Load Data
# =========================
@st.cache_data(ttl=CACHE_TTL)
def load_fa_sales_df():
    client = get_gs_client()

    def _load():
        sh = client.open_by_url(SPREADSHEET_URL)
        ws = sh.worksheet(SHEET_NAME)
        return ws.get_all_values()

    values = _retry(_load)

    if not values or len(values) < 2:
        return pd.DataFrame()

    raw_headers = [str(h).strip() for h in values[0]]
    headers = _dedupe_headers(raw_headers)
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    df = df.loc[:, [c for c in df.columns if str(c).strip() != ""]]
    df.columns = [c.strip() for c in df.columns]

    col_net = _find_col(df, ["Net Value"])
    col_tp = _find_col(df, ["Throughput Points"])
    col_qty = _find_col(df, ["Qty ( Unit )", "Qty(Unit)", "Qty", "Gross Qty"])

    if col_net:
        df[col_net] = _to_num(_safe_series(df, col_net))
    if col_tp:
        df[col_tp] = _to_num(_safe_series(df, col_tp))
    if col_qty:
        df[col_qty] = _to_num(_safe_series(df, col_qty))

    if col_net:
        # TEMP DEBUG: count weird values
        bad_raw = _safe_series(df, col_net).astype(str).str.contains(r"\(|\)|₹|,|[A-Za-z]", regex=True, na=False).sum()
        neg_cnt = (df[col_net] < 0).sum()
        if bad_raw > 0 or neg_cnt > 0:
            st.caption(f"Debug: Net Value formatted rows={bad_raw}, negatives={neg_cnt}")

    # parse Order Date (force dd/mm parsing + keep valid only)
    col_date = _find_col(df, ["Order Date"])
    if col_date:
        dts = pd.to_datetime(
            _safe_series(df, col_date).astype(str).str.strip(),
            errors="coerce",
            dayfirst=True
        )
        df = df[~dts.isna()].copy()
        df[col_date] = dts.dt.date

    return df


def apply_role_security(df: pd.DataFrame, access: dict) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    mode = str(access.get("attendance_mode", "")).strip().lower()
    emp_code = str(access.get("emp_code", "")).strip()
    allowed_teams = access.get("allowed_teams", []) or []

    col_emp = _find_col(df, ["Emp Code"])
    if not col_emp:
        return df if mode == "all" else df.iloc[0:0]

    df2 = df.copy()
    df2[col_emp] = _safe_series(df2, col_emp).astype(str).str.strip()

    if mode == "personal":
        return df2[df2[col_emp] == emp_code]

    if mode == "team":
        emp_list = load_employee_list()
        if emp_list is None or emp_list.empty:
            return df2.iloc[0:0]

        emp_list.columns = [c.strip() for c in emp_list.columns]
        el_emp = _find_col(emp_list, ["Emp Code"])
        el_team = _find_col(emp_list, ["Team"])
        if not el_emp or not el_team:
            return df2.iloc[0:0]

        m = emp_list[[el_emp, el_team]].copy().rename(columns={el_emp: "__emp__", el_team: "__team__"})
        m["__emp__"] = m["__emp__"].astype(str).str.strip()
        m["__team__"] = m["__team__"].astype(str).str.strip()

        team_map = dict(zip(m["__emp__"], m["__team__"]))
        df2["__team__"] = df2[col_emp].map(team_map)

        allowed = set([str(x).strip() for x in allowed_teams if str(x).strip()])
        return df2[df2["__team__"].isin(allowed)]

    if mode == "all":
        return df2

    return df2.iloc[0:0]


# =========================
# Charts (Altair)
# =========================
# You asked to change bar colors -> define a cleaner palette.
# (These apply to both SKU Mix and Retail/DD Mix segments.)
SEGMENT_PALETTE = [
    "#2563eb",  # blue
    "#f59e0b",  # amber
    "#44ef86",  # red
    "#78dcfa",  # green
    "#ee55b3",  # purple
    "#06b6d4",  # cyan
]

def _stacked_two_bars_chart(df: pd.DataFrame, dim_col: str, metric_col: str, title: str, top_n: int = 10):
    import plotly.graph_objects as go

    if df.empty or dim_col not in df.columns or metric_col not in df.columns:
        st.info("No data / missing columns.")
        return

    col_golden = _find_col(df, ["Golden SKU"])
    col_channel = _find_col(df, ["Retail/DD"])

    d = df.copy()
    d[dim_col] = _safe_series(d, dim_col).astype(str).str.replace("\u00a0", " ").str.strip()
    d = d[d[dim_col] != ""].copy()
    if d.empty:
        st.info("No data after cleaning blank labels.")
        return

    # Top N by total metric
    top = (
        d.groupby(dim_col, dropna=False)[metric_col]
        .sum()
        .sort_values(ascending=False)
        .head(top_n)
        .index.tolist()
    )
    d = d[d[dim_col].isin(top)].copy()
    if d.empty:
        st.info("No data for Top items.")
        return

    pieces = []

    # -------------------------
    # SKU Mix (Golden vs Regular)
    # -------------------------
    if col_golden and col_golden in d.columns:
        d["_sku_type"] = _safe_series(d, col_golden).apply(lambda x: "Golden" if _is_golden(x) else "Regular")
        sku = (
            d.groupby([dim_col, "_sku_type"], dropna=False)[metric_col]
            .sum().reset_index()
            .rename(columns={"_sku_type": "Segment"})
        )
        sku["Bar"] = "SKU Mix"
        pieces.append(sku)

    # -------------------------
    # Retail/DD Mix
    # -------------------------
    if col_channel and col_channel in d.columns:
        d["_channel"] = _safe_series(d, col_channel).apply(_channel)
        ch = (
            d.groupby([dim_col, "_channel"], dropna=False)[metric_col]
            .sum().reset_index()
            .rename(columns={"_channel": "Segment"})
        )
        ch["Bar"] = "Retail/DD Mix"
        pieces.append(ch)

    if not pieces:
        st.info("Missing Golden SKU / Retail-DD columns for mix charts.")
        return

    plot = pd.concat(pieces, ignore_index=True)
    plot = plot.rename(columns={dim_col: "Label", metric_col: "Value"})

    # % within each (Label, Bar)
    totals = plot.groupby(["Label", "Bar"], dropna=False)["Value"].sum().reset_index().rename(columns={"Value": "Total"})
    plot = plot.merge(totals, on=["Label", "Bar"], how="left")
    plot["Pct"] = plot.apply(lambda r: (r["Value"] / r["Total"] * 100) if r["Total"] and abs(r["Total"]) > 1e-9 else 0.0, axis=1)
    plot["PctLabel"] = plot["Pct"].map(_pct_str)

    # Order labels by overall metric total
    order = d.groupby(dim_col)[metric_col].sum().sort_values(ascending=False).index.tolist()
    label_order = [str(x) for x in order]

    # ---- Color map (stable) ----
    # IMPORTANT: This covers BOTH bars. Add more if you have other channel values.
    color_map = {
        "Golden": "#f59e0b",
        "Regular": "#22c55e",
        "DD": "#2563eb",
        "Retailer": "#78dcfa",
        "Uncategorized": "#ee55b3",
    }

    # Ensure stable segment ordering
    seg_order = ["DD", "Retailer", "Uncategorized", "Golden", "Regular"]
    existing_segments = [s for s in seg_order if s in set(plot["Segment"])]
    # append any unseen segments at end
    for s in sorted(set(plot["Segment"])):
        if s not in existing_segments:
            existing_segments.append(s)

    bars = ["SKU Mix", "Retail/DD Mix"]  # keep fixed order

    fig = go.Figure()

    # Build stacked traces per Segment, separated by Bar via offsetgroup
    for seg in existing_segments:
        for bar in bars:
            p = plot[(plot["Segment"] == seg) & (plot["Bar"] == bar)].copy()

            # Align to label order (fill missing with 0)
            m = {str(r["Label"]): r for _, r in p.iterrows()}
            y = []
            t = []
            hover_pct = []
            hover_val = []
            hover_total = []
            for lab in label_order:
                if lab in m:
                    y.append(float(m[lab]["Value"]))
                    t.append(str(m[lab]["PctLabel"]))
                    hover_pct.append(float(m[lab]["Pct"]))
                    hover_val.append(float(m[lab]["Value"]))
                    hover_total.append(float(m[lab]["Total"]))
                else:
                    y.append(0.0)
                    t.append("")          # nothing to show
                    hover_pct.append(0.0)
                    hover_val.append(0.0)
                    hover_total.append(0.0)

            fig.add_trace(
                go.Bar(
                    name=seg,
                    x=label_order,
                    y=y,
                    offsetgroup=bar,          # ✅ makes 2 bars side-by-side
                    legendgroup=seg,          # ✅ keep 1 legend item per segment
                    showlegend=(bar == bars[0]),  # ✅ show legend only once
                    marker_color=color_map.get(seg, "#94a3b8"),
                    text=t,                   # ✅ percent text
                    textposition="inside",    # ✅ always tries inside
                    insidetextanchor="middle",
                    textangle=270,            # ✅ vertical like your screenshot
                    cliponaxis=False,
                    customdata=list(zip(hover_pct, hover_total)),
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        f"Bar: {bar}<br>"
                        f"Segment: {seg}<br>"
                        "Value: %{y:,.2f}<br>"
                        "Percent: %{customdata[0]:.2f}%<br>"
                        "Bar Total: %{customdata[1]:,.2f}<extra></extra>"
                    ),
                )
            )

    fig.update_layout(
        barmode="stack",
        title=f"<b>{title}</b>",
        height=520,
        margin=dict(l=20, r=20, t=70, b=120),
        legend=dict(font=dict(size=12)),
    )
    fig.update_xaxes(
        tickangle=45,
        categoryorder="array",
        categoryarray=label_order,
        tickfont=dict(size=11),
    )
    fig.update_yaxes(
        gridcolor="rgba(0,0,0,0.08)",
        zerolinecolor="rgba(0,0,0,0.20)",
    )

    st.plotly_chart(fig, use_container_width=True)


def _area_trend_chart(df: pd.DataFrame, date_col: str, metric_col: str, metric_label: str):
    """
    Fix trend date issue:
    - Build a proper datetime64 column for x-axis (Altair uses it correctly)
    - Bucket daily or monthly based on span
    """
    import altair as alt

    tr = df.copy()
    if date_col not in tr.columns:
        st.info("Order Date column not found for trend.")
        return

    # Convert to datetime64 strictly
    tr["_dt"] = pd.to_datetime(_safe_series(tr, date_col), errors="coerce")
    tr = tr[~tr["_dt"].isna()].copy()
    if tr.empty:
        st.info("No valid date data.")
        return

    min_d = tr["_dt"].min()
    max_d = tr["_dt"].max()
    span = int((max_d - min_d).days) if pd.notna(min_d) and pd.notna(max_d) else 0

    if span > 62:
        # Month bucket (datetime)
        tr["_bucket"] = tr["_dt"].dt.to_period("M").dt.to_timestamp()
        x_title = "Month"
        axis_fmt = "%b %Y"
    else:
        # Day bucket (datetime)
        tr["_bucket"] = tr["_dt"].dt.floor("D")
        x_title = "Date"
        axis_fmt = "%d-%b"

    ts = (
        tr.groupby("_bucket", dropna=False)[metric_col]
        .sum()
        .reset_index()
        .rename(columns={"_bucket": x_title, metric_col: metric_label})
    )

    base = alt.Chart(ts).encode(
        x=alt.X(
            f"{x_title}:T",
            title=None,
            axis=alt.Axis(format=axis_fmt, labelAngle=0)
        ),
        y=alt.Y(f"{metric_label}:Q", title=None),
        tooltip=[
            alt.Tooltip(f"{x_title}:T", title=x_title, format=axis_fmt),
            alt.Tooltip(f"{metric_label}:Q", title=metric_label, format=",.2f"),
        ],
    )

    area = base.mark_area(opacity=0.22)
    line = base.mark_line(strokeWidth=2.5)

    st.altair_chart((area + line).properties(height=340), use_container_width=True)


# =========================
# Top 20% outlets table (by COUNT)
# =========================
def _top_20_outlets_by_count(df: pd.DataFrame, outlet_col: str, metric_col: str):
    col_golden = _find_col(df, ["Golden SKU"])
    if df.empty or outlet_col not in df.columns or metric_col not in df.columns:
        return pd.DataFrame()

    d = df.copy()
    d[outlet_col] = _safe_series(d, outlet_col).astype(str).str.strip()
    d = d[d[outlet_col] != ""].copy()
    if d.empty:
        return pd.DataFrame()

    total_metric = float(d[metric_col].sum())
    if total_metric <= 0:
        return pd.DataFrame()

    g_total = (
        d.groupby(outlet_col, dropna=False)[metric_col]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
        .rename(columns={metric_col: "Total"})
    )

    if col_golden and col_golden in d.columns:
        d["_is_golden"] = _safe_series(d, col_golden).apply(_is_golden)
        g_golden = (
            d[d["_is_golden"]]
            .groupby(outlet_col, dropna=False)[metric_col]
            .sum()
            .reset_index()
            .rename(columns={metric_col: "Golden Value"})
        )
    else:
        g_golden = pd.DataFrame({outlet_col: [], "Golden Value": []})

    out = g_total.merge(g_golden, on=outlet_col, how="left")
    out["Golden Value"] = out["Golden Value"].fillna(0.0)
    out["Golden %"] = out.apply(lambda r: (r["Golden Value"] / r["Total"] * 100) if r["Total"] else 0.0, axis=1)
    out["% in Total"] = out["Total"] / total_metric * 100
    out = out.rename(columns={outlet_col: "Outlet Name"})

    unique_outlets = int(out["Outlet Name"].nunique())
    n_show = int(math.ceil(unique_outlets * 0.20)) if unique_outlets > 0 else 0
    n_show = max(n_show, 1)

    out = out.head(n_show).reset_index(drop=True)

    # ✅ Ensure % sign everywhere in table
    out["Golden %"] = out["Golden %"].map(_pct_str)
    out["% in Total"] = out["% in Total"].map(_pct_str)

    return out


def _searchable_table(df_table: pd.DataFrame, label: str, key_prefix: str):
    if df_table is None or df_table.empty:
        st.info("No data.")
        return

    q = st.text_input("Search", value="", key=f"{key_prefix}_search", placeholder=f"Search in {label}...")
    view = df_table.copy()

    if q and q.strip():
        qq = q.strip().lower()

        def row_match(r):
            for v in r.values:
                if qq in str(v).lower():
                    return True
            return False

        mask = view.apply(row_match, axis=1)
        view = view[mask].copy()

    st.dataframe(view, use_container_width=True, hide_index=True)


# =========================
# Render one metric tab
# =========================
def _render_metric_tab(df: pd.DataFrame, metric_col: str, metric_label: str):
    col_super = _find_col(df, ["SuperStockist"])
    col_dist = _find_col(df, ["Distributor"])
    col_state = _find_col(df, ["State"])
    col_city = _find_col(df, ["City"])
    col_beat = _find_col(df, ["Beat"])
    col_div = _find_col(df, ["ProductDivision", "Product Division"])
    col_sec = _find_col(df, ["SecondaryCategory", "Secondary Category"])
    col_prod = _find_col(df, ["Product ErpId"])
    col_outlet = _find_col(df, ["Outlet Name"])
    col_date = _find_col(df, ["Order Date"])

    total_metric = float(_safe_series(df, metric_col).sum()) if not df.empty else 0.0
    col_order = _find_col(df, ["Order No"])
    total_orders = int(_safe_series(df, col_order).nunique()) if col_order else 0

    k1, k2, k3 = st.columns(3)
    k1.metric(f"Total {metric_label}", _money(total_metric))
    k2.metric("Orders", f"{total_orders:,}")
    k3.metric("Rows", f"{len(df):,}")

    # Full width charts: one per row
    dims = [
        (col_super, "Top 10 SuperStockist"),
        (col_dist, "Top 10 Distributor"),
        (col_state, "Top 10 States"),
        (col_city, "Top 10 City"),
        (col_beat, "Top 10 Beat"),
        (col_div, "Top 10 Product Division"),
        (col_sec, "Top 10 Secondary Category"),
        (col_prod, "Top 10 Items Sold"),
    ]

    for dim_col, title in dims:
        if dim_col and dim_col in df.columns:
            _stacked_two_bars_chart(df, dim_col, metric_col, title=title, top_n=10)
        else:
            st.info(f"Missing column for: {title}")

    st.divider()

    st.subheader("Trend Analysis")
    if col_date and col_date in df.columns:
        _area_trend_chart(df, col_date, metric_col, metric_label)
    else:
        st.info("Order Date column not found for trend.")

    st.divider()

    st.subheader("Top 20% Outlet Table (by Outlet Count)")
    if col_outlet and col_outlet in df.columns:
        out20 = _top_20_outlets_by_count(df, col_outlet, metric_col)
        if out20.empty:
            st.info("No outlet data for top 20% table.")
        else:
            _searchable_table(out20, "Top 20% Outlet Table", key_prefix=f"outlet_{metric_col}")
    else:
        st.info("Outlet Name column not found.")


# =========================
# MAIN
# =========================
def show():
    st.markdown("## 📈 FA Sales Dashboard")
    st.markdown("<div class='small'>Net Value + Throughput Points + User-wise Summary</div>", unsafe_allow_html=True)

    email = st.session_state.get("user_email")
    if not email:
        st.warning("Please login from sidebar to view dashboard.")
        st.stop()

    access = get_user_access(email)
    if access is None:
        st.warning("No access configured.")
        st.stop()

    df = load_fa_sales_df()
    if df.empty:
        st.warning(f"No data found in sheet '{SHEET_NAME}'.")
        st.stop()

    col_date = _find_col(df, ["Order Date"])
    col_emp = _find_col(df, ["Emp Code"])
    col_user = _find_col(df, ["User"])

    col_state = _find_col(df, ["State"])
    col_super = _find_col(df, ["SuperStockist"])
    col_dist = _find_col(df, ["Distributor"])
    col_div = _find_col(df, ["ProductDivision", "Product Division"])
    col_sec = _find_col(df, ["SecondaryCategory", "Secondary Category"])

    col_net = _find_col(df, ["Net Value"])
    col_tp = _find_col(df, ["Throughput Points"])
    col_qty = _find_col(df, ["Qty ( Unit )", "Qty(Unit)", "Qty", "Gross Qty"])
    col_order = _find_col(df, ["Order No"])

    if not col_date or not col_emp:
        st.error(
            "Headers mismatch in FA Sales Data.\n\n"
            f"Found columns: {list(df.columns)}\n\n"
            "Required at least: Order Date, Emp Code"
        )
        st.stop()

    df_sec = apply_role_security(df, access)
    if df_sec.empty:
        st.warning("No data available for your access level.")
        st.stop()

    cm_start, cm_end = _current_month_range()
    if "fa_date_range" not in st.session_state:
        st.session_state.fa_date_range = (cm_start, cm_end)

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("### 🗓️ Filter")

    c1, c2, c3 = st.columns([1, 1, 0.8])
    with c1:
        start_d = st.date_input("Start Date", value=st.session_state.fa_date_range[0], key="fa_start")
    with c2:
        end_d = st.date_input("End Date", value=st.session_state.fa_date_range[1], key="fa_end")
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Apply", use_container_width=True, key="fa_apply"):
            st.session_state.fa_date_range = _clamp_range(start_d, end_d)
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    d1, d2 = _clamp_range(*st.session_state.fa_date_range)
    df_f = df_sec[_safe_series(df_sec, col_date).between(d1, d2, inclusive="both")].copy()
    if df_f.empty:
        st.warning("No rows in selected date range.")
        st.stop()

    with st.expander("🔎 Advanced Filters", expanded=False):
        f1, f2, f3, f4 = st.columns(4)

        def _opts(col):
            if not col or col not in df_f.columns:
                return []
            s = _safe_series(df_f, col).astype(str).str.strip()
            return sorted([x for x in s.unique().tolist() if x])

        with f1:
            pick_user = st.multiselect("User", options=_opts(col_user)) if col_user else []
        with f2:
            pick_state = st.multiselect("State", options=_opts(col_state)) if col_state else []
        with f3:
            pick_super = st.multiselect("SuperStockist", options=_opts(col_super)) if col_super else []
        with f4:
            pick_dist = st.multiselect("Distributor", options=_opts(col_dist)) if col_dist else []

        f5, f6 = st.columns(2)
        with f5:
            pick_div = st.multiselect("Product Division", options=_opts(col_div)) if col_div else []
        with f6:
            pick_sec = st.multiselect("Secondary Category", options=_opts(col_sec)) if col_sec else []

        if pick_user and col_user:
            df_f = df_f[_safe_series(df_f, col_user).astype(str).str.strip().isin(pick_user)]
        if pick_state and col_state:
            df_f = df_f[_safe_series(df_f, col_state).astype(str).str.strip().isin(pick_state)]
        if pick_super and col_super:
            df_f = df_f[_safe_series(df_f, col_super).astype(str).str.strip().isin(pick_super)]
        if pick_dist and col_dist:
            df_f = df_f[_safe_series(df_f, col_dist).astype(str).str.strip().isin(pick_dist)]
        if pick_div and col_div:
            df_f = df_f[_safe_series(df_f, col_div).astype(str).str.strip().isin(pick_div)]
        if pick_sec and col_sec:
            df_f = df_f[_safe_series(df_f, col_sec).astype(str).str.strip().isin(pick_sec)]

    if df_f.empty:
        st.warning("No rows after applying filters.")
        st.stop()

    tab1, tab2, tab3 = st.tabs(["Net Value", "Throughput Points", "User-wise Summary"])

    with tab1:
        if not col_net:
            st.error("Net Value column not found.")
        else:
            _render_metric_tab(df_f, metric_col=col_net, metric_label="Net Value")

    with tab2:
        if not col_tp:
            st.error("Throughput Points column not found.")
        else:
            _render_metric_tab(df_f, metric_col=col_tp, metric_label="Throughput Points")

    with tab3:
        st.subheader("User-wise Summary")

        if not col_user:
            st.error("User column not found.")
        else:
            d = df_f.copy()
            if col_net:
                d[col_net] = _to_num(_safe_series(d, col_net))
            if col_tp:
                d[col_tp] = _to_num(_safe_series(d, col_tp))
            if col_qty:
                d[col_qty] = _to_num(_safe_series(d, col_qty))

            col_beat = _find_col(d, ["Beat"])
            col_dist2 = _find_col(d, ["Distributor"])
            col_super2 = _find_col(d, ["SuperStockist"])

            gb = d.groupby(col_user, dropna=False)

            out = pd.DataFrame({"User": gb.size().index.astype(str)})

            def _agg_sum(colname):
                if colname and colname in d.columns:
                    return gb[colname].sum().values
                return [0] * len(out)

            def _agg_nunique(colname):
                if colname and colname in d.columns:
                    return gb[colname].nunique().values
                return [0] * len(out)

            out["Net Value"] = _agg_sum(col_net)
            out["Orders"] = _agg_nunique(col_order)
            out["Sold Units"] = _agg_sum(col_qty)
            out["Throughput Points"] = _agg_sum(col_tp)
            out["Unique Beats"] = _agg_nunique(col_beat)
            out["Unique Distributors"] = _agg_nunique(col_dist2)
            out["Unique SuperStockist"] = _agg_nunique(col_super2)

            if "Net Value" in out.columns:
                out = out.sort_values("Net Value", ascending=False)

            _searchable_table(out, "User-wise Summary", key_prefix="user_summary")