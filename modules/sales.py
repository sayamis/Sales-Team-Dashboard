import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh

from utils.access import get_user_access
from utils.gsheets import load_sales_data_cached, normalize_state, to_date_series
from utils.charts import (
    top10_stacked_chart, top10_sku_by_type_chart, top10_dispatched_mix_chart,
    top10_stacked_chart_qty, top10_sku_by_type_chart_qty,
    top10_loss_chart, top10_loss_sku_by_type_chart,
    sku_customer_penetration_table
)
from utils.ui import render_brand_header, render_page_title


REFRESH_MS = 300000  # 5 min


def show():
    render_brand_header()  # logo + saya
    render_page_title(
        "📊 Sales Dashboard - Data Source : Primary Data (SAP Data)"
    )

    # ✅ email already set by app.py (global login)
    email = st.session_state.get("user_email")
    if not email:
        st.warning("Please login from sidebar to view dashboard.")
        st.stop()

    access = get_user_access(email)
    if access is None:
        st.warning("⚠️ You do not have access to this dashboard.")
        st.stop()

    st_autorefresh(interval=REFRESH_MS, key="gs_refresh")

    # LOAD
    df, _ = load_sales_data_cached(refresh_ms=REFRESH_MS, data_tab="Orders")

    COL_CUST = "Customer/Vendor Name"
    COL_ITEM = "Item No."
    COL_TOTAL = "Total"
    COL_STATE = "State"
    COL_DISP = "Dispatch"
    COL_GOLD = "Golden SKU"
    COL_TPUT = "Throughput Value"
    COL_QTY = "Quantity"
    COL_DATE = "Date"

    # ACCESS
    df["_state_norm"] = df[COL_STATE].apply(normalize_state)
    sales_mode = access["sales_mode"]

    if sales_mode.lower() == "all":
        df_view = df.copy()
        st.caption("Access: FULL")
    elif sales_mode.lower() == "state":
        allowed_states = [s.lower() for s in access["allowed_states"]]
        df_view = df[df["_state_norm"].isin(allowed_states)].copy()
        st.caption(f"Access: States → {', '.join(access['allowed_states'])}")
    else:
        st.warning("⚠️ You do not have access to Sales Dashboard.")
        st.stop()

    # FILTER PREP
    df_view["_date_parsed"] = to_date_series(df_view[COL_DATE])
    df_view["_cust_norm"] = df_view[COL_CUST].astype(str).str.strip()
    df_view["_sku_norm"] = df_view[COL_ITEM].astype(str).str.strip()
    df_view["_state_raw"] = df_view[COL_STATE].astype(str).str.strip()

    cust_opts = sorted([x for x in df_view["_cust_norm"].dropna().unique() if x])
    sku_opts = sorted([x for x in df_view["_sku_norm"].dropna().unique() if x])
    state_opts = sorted([x for x in df_view["_state_raw"].dropna().unique() if x])

    dates_clean = df_view["_date_parsed"].dropna()
    min_d = dates_clean.min() if not dates_clean.empty else None
    max_d = dates_clean.max() if not dates_clean.empty else None

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

            picked = st.date_input("Date Range", value=widget_value, key="date_widget")
            if isinstance(picked, tuple) and len(picked) == 2:
                st.session_state.f_date = (picked[0], picked[1])
            else:
                st.session_state.f_date = (picked, picked)

        with f2:
            st.multiselect("States", options=state_opts, key="f_states", placeholder="Choose options")
        with f3:
            st.multiselect("Customers", options=cust_opts, key="f_customers", placeholder="Choose options")
        with f4:
            st.multiselect("SKU (Item No.)", options=sku_opts, key="f_skus", placeholder="Choose options")

    # APPLY FILTERS
    df_f = df_view.copy()

    date_range = st.session_state.f_date
    if isinstance(date_range, tuple) and len(date_range) == 2 and date_range[0] and date_range[1]:
        d1, d2 = date_range
        df_f = df_f[df_f["_date_parsed"].between(d1, d2, inclusive="both")]

    if st.session_state.f_states:
        df_f = df_f[df_f["_state_raw"].isin(st.session_state.f_states)]
    if st.session_state.f_customers:
        df_f = df_f[df_f["_cust_norm"].isin(st.session_state.f_customers)]
    if st.session_state.f_skus:
        df_f = df_f[df_f["_sku_norm"].isin(st.session_state.f_skus)]

    df_view = df_f
    if df_view.empty:
        st.warning("No rows after applying filters.")
        st.stop()

    # KPI (same)
    def to_num_local(s):
        return pd.to_numeric(s, errors="coerce").fillna(0)

    def norm_yes_local(x):
        return str(x).strip().lower() in ("yes", "y", "true", "1")

    tmp_kpi = df_view[[COL_TOTAL, COL_TPUT, COL_DISP, COL_CUST]].copy()
    tmp_kpi["_total_val"] = to_num_local(tmp_kpi[COL_TOTAL])
    tmp_kpi["_is_dispatch"] = tmp_kpi[COL_DISP].apply(norm_yes_local)

    order_value = float(tmp_kpi["_total_val"].sum())
    sale_value = float(tmp_kpi.loc[tmp_kpi["_is_dispatch"], "_total_val"].sum())
    loss_value = float(tmp_kpi.loc[~tmp_kpi["_is_dispatch"], "_total_val"].sum())
    customers = int(df_view[COL_CUST].astype(str).str.strip().replace("", pd.NA).dropna().nunique())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Order value", f"{order_value:,.2f}")
    k2.metric("Sale Value (Dispatched)", f"{sale_value:,.2f}")
    k3.metric("Loss of Sale", f"{loss_value:,.2f}")
    k4.metric("Customers", f"{customers:,}")

    # TABS (unchanged)
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
            top10_dispatched_mix_chart(df_view, COL_CUST, COL_TOTAL, COL_DISP, COL_GOLD, "Top 10 Customers — Dispatched Mix (Golden vs Regular)")
        with c2:
            top10_dispatched_mix_chart(df_view, COL_ITEM, COL_TOTAL, COL_DISP, COL_GOLD, "Top 10 SKU — Dispatched Mix (Golden vs Regular)")

        c3, c4 = st.columns(2)
        with c3:
            top10_dispatched_mix_chart(df_view, COL_STATE, COL_TOTAL, COL_DISP, COL_GOLD, "Top 10 States — Dispatched Mix (Golden vs Regular)")

    with tab5:
        st.write("### Top 10 Dispatched — Golden vs Regular Mix (Throughput Value)")
        c1, c2 = st.columns(2)
        with c1:
            top10_dispatched_mix_chart(df_view, COL_CUST, COL_TPUT, COL_DISP, COL_GOLD, "Top 10 Customers — Dispatched Mix (Golden vs Regular) • Throughput")
        with c2:
            top10_dispatched_mix_chart(df_view, COL_ITEM, COL_TPUT, COL_DISP, COL_GOLD, "Top 10 SKU — Dispatched Mix (Golden vs Regular) • Throughput")

        c3, c4 = st.columns(2)
        with c3:
            top10_dispatched_mix_chart(df_view, COL_STATE, COL_TPUT, COL_DISP, COL_GOLD, "Top 10 States — Dispatched Mix (Golden vs Regular) • Throughput")

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