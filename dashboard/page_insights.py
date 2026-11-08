"""Insights page: analysis, opportunities, outreach drafts."""

from __future__ import annotations

import streamlit as st

from dashboard import api, theme
from dashboard.common import call


def _recommendation(r: dict) -> None:
    ev = r.get("evidence") or {}
    ids = ", ".join(ev.get("interaction_ids") or []) or "none"
    st.html(
        f'<div class="sc-card"><div class="t">{theme.esc(r.get("title"))}</div>'
        f'<div class="m"><span class="sc-chip hi">impact {theme.esc(r.get("impact"))}</span>'
        f'<span class="sc-chip">effort {theme.esc(r.get("effort"))}</span></div>'
        f'<div class="b">{theme.esc(r.get("action", ""))}</div>'
        f'<div class="m" style="margin-top:.5rem">{theme.esc(r.get("rationale", ""))}</div>'
        f'<div class="m">evidence: {theme.esc(ev.get("kpi"))} / interactions {theme.esc(ids)}</div></div>'
    )


def _analysis(res: dict) -> None:
    theme.section("Needs and objections")
    left, right = st.columns(2)
    needs, objs = res.get("needs", []), res.get("objections", [])
    with left:
        st.caption("What prospects ask for")
        theme.plot(theme.bar_chart([n.get("label") for n in needs], [n.get("count") for n in needs],
                                   horizontal=True, height=200), key="in_needs")
    with right:
        st.caption("What stops them")
        theme.plot(theme.bar_chart([o.get("label") for o in objs], [o.get("count") for o in objs],
                                   horizontal=True, height=200, flagged={o.get("label") for o in objs}),
                   key="in_objs")
    for o in objs:
        ids = ", ".join(o.get("example_interaction_ids") or [])
        st.html(f'<div class="m" style="font:400 .74rem var(--mono);color:var(--dim)">'
                f'{theme.esc(o.get("label"))} examples: {theme.esc(ids or "none")}</div>')
    theme.section("Recommendations")
    for r in res.get("recommendations", []):
        _recommendation(r)


def _drafts() -> None:
    theme.section("Outreach drafts")
    ok, drafts = call(api.get, "/owner/drafts")
    if not ok:
        return
    if not drafts:
        st.info("No drafts yet.")
    for d in drafts:
        sent = d.get("status") == "sent_manually"
        score = d.get("score")
        st.html(
            f'<div class="sc-card"><div class="t">{theme.esc(d.get("first_name"))}</div>'
            f'<div class="m"><span class="sc-chip hi">interest {theme.esc(d.get("interest"))}</span>'
            f'<span class="sc-chip">score {theme.esc(score)}</span>'
            f'<span class="sc-chip">{theme.esc(d.get("status"))}</span></div>'
            f'<div class="b">{theme.esc(d.get("reason", ""))}</div></div>'
        )
        st.code(d.get("draft_message", ""), language=None, wrap_lines=True)
        if not sent and st.button("Mark as sent manually", key=f"sent_{d['id']}"):
            ok_sent, _res = call(api.post, f"/owner/drafts/{d['id']}/mark_sent")
            if ok_sent:
                st.rerun()


def render() -> None:
    theme.page_head(
        "Insights",
        "Nothing is ever sent by the system. Copy a draft and send it yourself, then mark it as sent.",
    )
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Run analysis"):
        ok, res = call(api.post, "/owner/insights/run", mode="analyze")
        if ok:
            st.session_state["analysis"] = res
    if c2.button("Find opportunities"):
        ok, res = call(api.post, "/owner/insights/run", mode="opportunities")
        if ok:
            st.success(f"{len(res)} opportunity drafts created.")
    if "analysis" in st.session_state:
        _analysis(st.session_state["analysis"])
    _drafts()
