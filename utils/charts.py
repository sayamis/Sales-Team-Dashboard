import pandas as pd
import streamlit as st
import plotly.graph_objects as go

# ---------------------------
# Helpers (needed by charts)
# ---------------------------
def norm_yes(x) -> bool:
    return str(x).strip().lower() in ("yes", "y", "true", "1")

def to_num(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)

# ============================
# PLOTLY STYLE
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

# ---- keep your existing chart functions below (unchanged) ----
# top10_stacked_chart
# top10_sku_by_type_chart
# top10_dispatched_mix_chart
# top10_stacked_chart_qty
# top10_sku_by_type_chart_qty
# top10_loss_chart
# top10_loss_sku_by_type_chart
# sku_customer_penetration_table





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