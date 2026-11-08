"""Overview page: agent network scene, KPI cards and charts."""

from __future__ import annotations

import streamlit as st

from dashboard import api, theme, three_scene
from dashboard.common import bucket_series, call, quiet

_FLAGGED = theme.FLAGGED


def _pct(num: float, den: float) -> float:
    return num / den if den else 0.0


def _kpis(k: dict, events: list[dict], pending: int | None) -> None:
    inter, interested, paid = (k.get(x, 0) for x in ("interactions_count", "interested_prospects", "paid_prospects"))
    flagged = lambda e: e.get("decision") in _FLAGGED
    c = st.columns(4)
    with c[0]:
        theme.kpi("Interactions", inter, f"{len(events)} audit events in view", series=bucket_series(events))
    with c[1]:
        theme.kpi("Interested", interested, f"{_pct(interested, inter) * 100:.0f}% of interactions",
                  meter=_pct(interested, inter))
    with c[2]:
        theme.kpi("Paid", paid, f"{_pct(paid, interested) * 100:.0f}% of interested", meter=_pct(paid, interested))
    with c[3]:
        theme.kpi("Conversion", f"{k.get('conversion_rate', 0) * 100:.1f}", "paid / interested", unit="%",
                  meter=min(1.0, k.get("conversion_rate", 0) * 5))
    c = st.columns(4)
    sat = k.get("satisfaction_avg")
    alerts = k.get("security_alerts_count", 0)
    with c[0]:
        theme.kpi("Revenue", f"{k.get('simulated_revenue', 0):,.2f}", "simulated, sandbox only", unit="TND")
    with c[1]:
        theme.kpi("Satisfaction", "n/a" if sat is None else f"{sat:.2f}", "average rating out of 5",
                  pips=0 if sat is None else round(sat))
    with c[2]:
        theme.kpi("Security alerts", alerts, "blocked or escalated", alert=alerts > 0,
                  series=bucket_series(events, flagged))
    with c[3]:
        theme.kpi("Awaiting your decision", "n/a" if pending is None else pending, "pending approvals",
                  alert=bool(pending))


def _charts(k: dict) -> None:
    theme.section("Conversion and payments")
    left, right = st.columns(2)
    with left:
        theme.plot(theme.funnel_chart(
            ["Interactions", "Interested", "Paid"],
            [k.get("interactions_count", 0), k.get("interested_prospects", 0), k.get("paid_prospects", 0)],
        ), key="ov_funnel")
    pay = k.get("payments_by_status") or {}
    with right:
        theme.plot(theme.bar_chart(list(pay), list(pay.values()), flagged={"failed", "rejected"}),
                   key="ov_pay")
    theme.section("Objections and alerts")
    left, right = st.columns(2)
    obj = [o for o in (k.get("top_objections") or []) if isinstance(o, dict)]
    with left:
        theme.plot(theme.bar_chart([o.get("label", "?") for o in obj], [o.get("count", 0) for o in obj],
                                   horizontal=True), key="ov_obj")
    al = k.get("security_alerts_by_type") or {}
    with right:
        theme.plot(theme.bar_chart(list(al), list(al.values()), flagged=set(al)), key="ov_alerts")


def _recommendations(k: dict) -> None:
    theme.section("Latest recommendations")
    recs = k.get("latest_recommendations") or []
    if not recs:
        st.info("No recommendations yet. Run an analysis on the Insights page.")
    for r in recs:
        chips = (f'<span class="sc-chip hi">impact {theme.esc(r.get("impact", "?"))}</span>'
                 f'<span class="sc-chip">effort {theme.esc(r.get("effort", "?"))}</span>') if r.get("impact") else ""
        st.html(f'<div class="sc-card"><div class="t">{theme.esc(r.get("title", "?"))}</div>'
                f'<div class="m">{chips}</div><div class="b">{theme.esc(r.get("action", ""))}</div></div>')
    theme.section("Narrative summary")
    if st.button("Generate narrative summary"):
        ok, n = call(api.get, "/owner/kpis/narrative")
        if ok:
            st.html(f'<div class="sc-card"><div class="b" style="margin:0">{theme.esc(n.get("text", ""))}</div></div>')


def render() -> None:
    ok, k = call(api.get, "/owner/kpis")
    if not ok:
        theme.page_head("Overview", "Mission control for the agent team.")
        return
    events = quiet(api.get, "/owner/audit", limit=100) or []
    ver = quiet(api.get, "/owner/audit/verify")
    approvals = quiet(api.get, "/owner/approvals", status="pending")
    chain = [] if ver is None else [theme.pill("audit chain ok" if ver.get("ok") else "audit chain broken",
                                               "ok" if ver.get("ok") else "bad")]
    theme.page_head("Overview", "Mission control for the agent team. Rotate the network by dragging.", chain)
    theme.embed_html(three_scene.agent_network_html(events, k, theme.mode()), 500)
    theme.section("Key figures")
    _kpis(k, events, None if approvals is None else len(approvals))
    _charts(k)
    _recommendations(k)
