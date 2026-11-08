"""Q&A manager page."""

from __future__ import annotations

import streamlit as st

from dashboard import api, theme
from dashboard.common import call


def render() -> None:
    ok, entries = call(api.get, "/owner/qa")
    pills = None
    if ok:
        counts = {s: sum(1 for e in entries if e.get("status") == s) for s in ("draft", "approved", "rejected")}
        pills = [theme.pill(f"{counts['approved']} approved", "ok"),
                 theme.pill(f"{counts['draft']} draft", "plain"),
                 theme.pill(f"{counts['rejected']} rejected", "plain")]
    theme.page_head("Q&A manager", "Answers the sales agent may give. Only approved entries are used.", pills)
    top = st.columns([2, 1, 3])
    status = top[0].selectbox("Status", ["all", "draft", "approved", "rejected"])
    if top[1].button("Generate", help="Draft new entries from the product definition"):
        ok_gen, created = call(api.post, "/owner/qa/generate")
        if ok_gen:
            st.success(f"Created {len(created)} draft entries.")
            ok, entries = call(api.get, "/owner/qa")
    if not ok:
        return
    if status != "all":
        entries = [e for e in entries if e.get("status") == status]
    if not entries:
        st.info("No entries.")
    for e in entries:
        eid = e["id"]
        with st.expander(f"{e.get('status')}  |  {e.get('question')}  |  {e.get('hits', 0)} hits"):
            c1, c2, _ = st.columns([1, 1, 4])
            for col, action, label in ((c1, "approve", "Approve"), (c2, "reject", "Reject")):
                if col.button(label, key=f"{action}_{eid}"):
                    ok_act, _res = call(api.post, f"/owner/qa/{eid}/{action}")
                    if ok_act:
                        st.rerun()
            with st.form(f"edit_{eid}"):
                q = st.text_input("Question", e.get("question", ""))
                a = st.text_area("Answer", e.get("answer", ""))
                v = st.text_area("Variants (one per line)", "\n".join(e.get("variants") or []))
                if st.form_submit_button("Save (returns to draft)"):
                    body = {
                        "question": q,
                        "answer": a,
                        "variants": [x.strip() for x in v.splitlines() if x.strip()],
                    }
                    ok_put, _res = call(api.put, f"/owner/qa/{eid}", body)
                    if ok_put:
                        st.rerun()
