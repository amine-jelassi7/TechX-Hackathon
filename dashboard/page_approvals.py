"""Approvals page."""

from __future__ import annotations

import streamlit as st

from dashboard import api, theme
from dashboard.common import call, fmt_reasons


def _card(a: dict) -> None:
    pay = a.get("payment") or {}
    risk = pay.get("risk_score")
    risky = isinstance(risk, int | float) and risk >= 0.7
    kv = ""
    if pay:
        kv = (
            '<div class="sc-kv">'
            f'<div>amount<b>{theme.esc(pay.get("amount"))} {theme.esc(pay.get("currency"))}</b></div>'
            f'<div>risk score<b style="color:{"var(--alarm-ink)" if risky else "var(--text)"}">{theme.esc(risk)}</b></div>'
            f'<div>plan<b>{theme.esc(pay.get("plan_id"))}</b></div>'
            f'<div>prospect<b>{theme.esc(pay.get("prospect_id"))}</b></div>'
            f'<div>payment status<b>{theme.esc(pay.get("status"))}</b></div></div>'
        )
    st.html(
        f'<div class="sc-card{" flag" if risky else ""}"><div class="t">{theme.esc(a.get("kind"))} approval</div>'
        f'<div class="m">{theme.esc(a.get("id"))} / ref {theme.esc(a.get("ref_id"))} / '
        f'created {theme.esc(a.get("created_at"))}</div>'
        f'<div class="b">Reasons: {theme.esc(fmt_reasons(a.get("reasons")))}</div>{kv}</div>'
    )


def render() -> None:
    ok, items = call(api.get, "/owner/approvals", status="pending")
    theme.page_head(
        "Approvals",
        "Payments the gateway held for a human decision. Nothing moves until you choose.",
        [theme.pill(f"{len(items)} pending", "bad" if items else "plain")] if ok else None,
    )
    if not ok:
        return
    if not items:
        st.success("No pending approvals.")
    for a in items:
        with st.container(key=f"appr_{a['id']}"):  # key -> CSS hook for the decision feedback
            _card(a)
            c1, c2, _ = st.columns([1, 1, 5])
            for col, action, label in ((c1, "approve", "Approve"), (c2, "reject", "Reject")):
                if col.button(label, key=f"{action}_{a['id']}"):
                    ok, res = call(api.post, f"/owner/approvals/{a['id']}/{action}")
                    if ok:
                        st.session_state["approval_result"] = res
                        st.rerun()
    if "approval_result" in st.session_state:
        theme.section("Last result")
        st.json(st.session_state["approval_result"])
