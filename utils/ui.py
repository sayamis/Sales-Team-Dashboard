import os
import streamlit as st

COMPANY_NAME_DEFAULT = "Saya Stationeries Pvt Ltd"

def apply_global_title_style():
    st.markdown(
        """
        <style>
        .brand-row{
            height: 52px;              /* match logo width=52 */
            display: flex;
            align-items: center;        /* ✅ vertical center */
        }

        .tppl-title{
            font-size: 28px;
            font-weight: 800;
            line-height: 1.2;
            margin: 6px 0 12px 0;
        }
        .tppl-sub{
            font-size: 14px;
            color: rgba(49, 51, 63, 0.75);
            margin-top: -6px;
            margin-bottom: 6px;
        }
        .brand-wrap{
            display:flex;
            align-items:center;     /* ✅ true vertical center */
            gap:16px;
            margin: 0 0 10px 0;
        }
        .brand-logo{
            height: 52px;
            width: 52px;
            object-fit: contain;
            border-radius: 10px;
            display:block;
        }
        .brand-name{
            font-size: 36px;
            font-weight: 800;
            letter-spacing: 0.3px;
            line-height: 1;         /* ✅ no baseline drop */
            margin: 0;
            padding: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def render_brand_header(company_name: str = None, logo_path: str = "assets/logo.jpg"):
    apply_global_title_style()
    company_name = company_name or COMPANY_NAME_DEFAULT

    # convert local image to base64 so HTML <img> can render it reliably
    import base64
    img_html = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        # detect mime
        mime = "image/png" if logo_path.lower().endswith(".png") else "image/jpeg"
        img_html = f'<img src="data:{mime};base64,{b64}" class="brand-logo" alt="logo" />'

    st.markdown(
        f"""
        <div class="brand-wrap">
            {img_html}
            <div class="brand-name">{company_name}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_page_title(title: str, subtitle: str = ""):
    apply_global_title_style()
    st.markdown(f'<div class="tppl-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="tppl-sub">{subtitle}</div>', unsafe_allow_html=True)