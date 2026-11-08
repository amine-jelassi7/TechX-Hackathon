"""SoukSaaS owner console (run: streamlit run dashboard/app.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from dashboard import (
    api,
    page_approvals,
    page_feed,
    page_feedback,
    page_insights,
    page_overview,
    page_products,
    page_qa,
    page_security,
    theme,
)
from dashboard.common import quiet

PAGES = {
    "Products": page_products.render,
    "Inbox": page_feedback.render_inbox,
    "Suggestions": page_feedback.render_suggestions,
    "Overview": page_overview.render,
    "Live feed": page_feed.render,
    "Approvals": page_approvals.render,
    "Q&A manager": page_qa.render,
    "Insights": page_insights.render,
    "Security & Evals": page_security.render,
    "Simulator": page_security.render_simulator,
}


@st.cache_data(ttl=8, show_spinner=False)
def _link_state() -> str:
    ver = quiet(api.get, "/owner/audit/verify")
    if ver is None:
        return "offline"
    return "chain ok" if ver.get("ok") else "chain broken"


def _sidebar() -> str:
    st.sidebar.html(
        '<div class="sc-brand"><b>Souk<span>SaaS</span></b><div>Owner console</div></div>'
    )
    choice = st.sidebar.radio("Page", list(PAGES), label_visibility="collapsed")
    products = quiet(api.get, "/owner/products") or []
    if products:
        selected = st.sidebar.selectbox(
            "Product",
            products,
            index=next(
                (
                    i
                    for i, p in enumerate(products)
                    if p["id"] == st.session_state.get("selected_product_id")
                ),
                0,
            ),
            format_func=lambda p: p["product"].get("name") or p["slug"],
        )
        st.session_state["selected_product_id"] = selected["id"]
        api.set_product(selected["id"])
    else:
        api.set_product(None)
        st.sidebar.caption("Add a SaaS to get started")
    theme.mode_toggle()
    state = _link_state()
    kind = "ok" if state == "chain ok" else "bad"
    st.sidebar.html(
        f'<div class="sc-side-foot">{theme.pill(state, kind)}<br>{theme.esc(api.base_url())}</div>'
    )
    return choice


def main() -> None:
    st.set_page_config(page_title="SoukSaaS owner console", layout="wide")
    theme.init_mode()
    choice = _sidebar()
    theme.inject_css()
    theme.begin_page(choice)
    PAGES[choice]()
    theme.end_page()


main()
