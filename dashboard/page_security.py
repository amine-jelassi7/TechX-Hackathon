"""Security and evals page, plus the simulator page."""

from __future__ import annotations

from collections import Counter

import streamlit as st

from dashboard import api, theme, three_scene
from dashboard.common import call


def _pct(v) -> str:
    return f"{v * 100:.0f}" if isinstance(v, int | float) and v <= 1 else str(v)


def _details_table(details: list[dict]) -> None:
    keys = list(dict.fromkeys(k for d in details for k in d))
    rows = []
    for d in details:
        row = {k: d.get(k) for k in keys}
        if "ok" in d:
            row["ok"] = theme.pill("pass" if d["ok"] else "fail", "ok" if d["ok"] else "bad")
        row["_bad"] = d.get("ok") is False
        rows.append(row)
    theme.html_table([(k, k) for k in keys], rows, raw={"ok"}, mono=set(keys) - {"ok", "question"},
                     flag_if=lambda r: r["_bad"])


def _evals(ev: dict) -> None:
    theme.section("Evaluation results")
    if not ev:
        st.info("No eval run yet.")
        return
    qa = ev.get("qa") if isinstance(ev.get("qa"), dict) else ev
    atk = ev.get("attacks") if isinstance(ev.get("attacks"), dict) else ev
    blocked, total = atk.get("blocked"), atk.get("total", atk.get("n"))
    c = st.columns(3)
    with c[0]:
        theme.kpi("Q&A accuracy", _pct(qa.get("accuracy", qa.get("qa_accuracy", "n/a"))), unit="%",
                  sub=f"{qa.get('total', '?')} questions", meter=qa.get("accuracy"))
    with c[1]:
        theme.kpi("Correct refusals", _pct(qa.get("refusal_rate", qa.get("correct_refusal_rate", "n/a"))),
                  unit="%", sub="out-of-scope questions refused", meter=qa.get("refusal_rate"))
    with c[2]:
        missed = blocked is not None and total is not None and blocked < total
        theme.kpi("Attacks blocked", f"{blocked}/{total}" if blocked is not None else "n/a",
                  sub=f"run {theme.short_ts(ev.get('run_at'))} / llm {ev.get('llm_mode', '?')}",
                  alert=missed, meter=(blocked / total) if blocked is not None and total else None)
    for title, block in (("Q&A cases", qa), ("Attack cases", atk)):
        details = block.get("details") or block.get("results")
        if details and isinstance(details, list):
            st.caption(title)
            _details_table(details)
    with st.expander("Raw eval JSON"):
        st.json(ev)


def _integrity(ver: dict, alerts: list[dict] | None) -> None:
    left, right = st.columns([1, 2])
    with left:
        theme.embed_html(three_scene.integrity_ring_html(ver, theme.mode()), 250)
    with right:
        bad = not ver.get("ok")
        c = st.columns(3)
        with c[0]:
            theme.kpi("Log integrity", "TAMPERED" if bad else "OK", "hash chain verification", alert=bad)
        with c[1]:
            theme.kpi("Events checked", ver.get("checked", 0), "from the first block")
        with c[2]:
            theme.kpi("First bad event", ver.get("first_bad_id") or "none", "where the chain breaks", alert=bad)
        if alerts is not None:
            sev = Counter(a.get("severity", "?") for a in alerts)
            st.html(f'<div class="m" style="font:400 .74rem var(--mono);color:var(--dim);margin-top:.7rem">'
                    f'{len(alerts)} alerts in view / '
                    f'{", ".join(f"{n} {s}" for s, n in sev.most_common()) or "none"}</div>')


def render() -> None:
    ok_v, ver = call(api.get, "/owner/audit/verify")
    ok_a, alerts = call(api.get, "/owner/alerts", limit=100)
    chain = [theme.pill("chain ok" if ver.get("ok") else "chain broken", "ok" if ver.get("ok") else "bad")] if ok_v else None
    theme.page_head("Security and evals", "Integrity of the audit log, alerts raised, and how the defences score.", chain)
    if ok_v:
        _integrity(ver, alerts if ok_a else None)
    if ok_a:
        theme.section("Alerts by type")
        counts = Counter(a.get("alert_type", "?") for a in alerts)
        theme.plot(theme.bar_chart(list(counts), list(counts.values()), flagged=set(counts), height=240),
                   key="sec_alerts")
        theme.section("Latest alerts")
        if alerts:
            rows = [{**a, "ts": theme.short_ts(a.get("ts"))} for a in alerts]
            theme.html_table(
                [("ts", "Time"), ("alert_type", "Type"), ("severity", "Severity"), ("detail", "Detail"),
                 ("trace_id", "Trace")], rows, mono={"ts", "alert_type", "severity", "trace_id"},
                flag_if=lambda r: r.get("severity") == "high")
        else:
            st.info("No alerts.")
    ok_e, ev = call(api.get, "/owner/evals")
    if ok_e:
        _evals(ev)


def render_simulator() -> None:
    theme.page_head("Simulator", "Runs a scripted scenario against the live backend.")
    c1, c2, _ = st.columns([1, 1, 3])
    for col, scenario, label in (
        (c1, "demo", "Run demo scenario"),
        (c2, "attack", "Run attack scenario"),
    ):
        if col.button(label):
            ok, res = call(api.post, "/owner/simulate", scenario=scenario)
            if ok:
                st.session_state["sim"] = (scenario, res)
    if "sim" in st.session_state:
        scenario, res = st.session_state["sim"]
        theme.section(f"Steps ({scenario})")
        for i, s in enumerate(res.get("steps", []), 1):
            with st.expander(f"{i}. {s.get('step')}", expanded=True):
                st.json(s.get("result"))
