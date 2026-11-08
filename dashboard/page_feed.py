"""Live feed page: newest audit events, refreshed every 5 s via st.fragment."""

from __future__ import annotations

import streamlit as st

from dashboard import api, theme
from dashboard.common import call, fmt_reasons

_COLUMNS = [
    ("ts", "Time"),
    ("actor", "Actor"),
    ("event_type", "Event"),
    ("decision", "Decision"),
    ("reasons", "Reasons"),
    ("trace_id", "Trace"),
]


@st.fragment(run_every=5)
def _feed() -> None:
    ok, events = call(api.get, "/owner/audit", limit=100)
    if not ok:
        return
    if not events:
        st.info("No audit events yet.")
        return
    flagged = [e for e in events if e.get("decision") in theme.FLAGGED]
    theme.page_head(
        "Live feed",
        "Newest 100 audit events, refreshed every 5 seconds.",
        [theme.pill(f"{len(events)} events", "plain"),
         theme.pill(f"{len(flagged)} flagged", "bad" if flagged else "plain")],
    )
    flagged_only = st.toggle("Show only block and escalate events", value=False)
    shown = flagged if flagged_only else events
    rows = [
        {
            "ts": theme.short_ts(e.get("ts")),
            "actor": e.get("actor"),
            "event_type": e.get("event_type"),
            "decision": theme.decision_cell(e.get("decision")),
            "reasons": fmt_reasons(e.get("reasons")),
            "trace_id": e.get("trace_id"),
            "_id": e.get("id") or e.get("trace_id"),
            "_flag": e.get("decision") in theme.FLAGGED,
        }
        for e in shown
    ]
    if not rows:
        st.info("No flagged events in the last 100.")
        return
    theme.html_table(_COLUMNS, rows, mono={"ts", "actor", "event_type", "trace_id"}, raw={"decision"},
                     flag_if=lambda r: r["_flag"], feed="audit", id_key="_id")


def render() -> None:
    _feed()
