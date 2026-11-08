"""Product feedback inbox and evidence-backed improvement actions."""

from __future__ import annotations

import streamlit as st

from dashboard import api
from dashboard.common import quiet


def _product_selector() -> dict | None:
    products = quiet(api.get, "/owner/products") or []
    if not products:
        st.info("Add a SaaS product before viewing feedback.")
        return None
    selected = st.selectbox(
        "Product", products, format_func=lambda p: p["product"].get("name") or p["slug"]
    )
    return selected


def render_inbox() -> None:
    st.title("Inbox")
    row = _product_selector()
    if not row:
        return
    stats = quiet(api.get, f"/owner/products/{row['id']}/feedback") or {}
    if not stats.get("response_count"):
        st.info("No feedback responses yet.")
    else:
        st.metric(
            "Average customer score",
            f"{stats['average']}/100",
            f"{stats['response_count']} responses",
        )
        st.caption(
            f"Overall label: {stats.get('label', 'not available')} · NPS: {stats.get('nps')}"
        )
        if not stats.get("ready"):
            st.caption("Not enough data yet to generate recommendations (minimum 5 responses).")
        else:
            st.bar_chart(stats.get("dimensions", {}))
            if stats.get("trend"):
                st.line_chart({"score": [item["score"] for item in stats["trend"]]})
            st.write("Score distribution", stats.get("distribution", {}))
            if stats.get("themes"):
                st.write("Review themes", stats["themes"])
    notifications = quiet(api.get, f"/owner/products/{row['id']}/notifications") or []
    if not notifications:
        st.caption("No reports or suggestions have been delivered yet.")
    for note in notifications:
        with st.container(border=True):
            st.subheader(note["title"])
            st.write(note["body"])
            try:
                content, _ = api.download(f"/owner/notifications/{note['id']}/report")
                st.download_button(
                    "Download report",
                    content,
                    file_name=f"feedback-{row['slug']}.md",
                    mime="text/markdown",
                    key=f"report-{note['id']}",
                )
            except api.ApiError as exc:
                st.caption(str(exc))


def render_suggestions() -> None:
    st.title("Suggestions")
    row = _product_selector()
    if not row:
        return
    suggestions = quiet(api.get, f"/owner/products/{row['id']}/suggestions") or []
    changes = quiet(api.get, "/owner/page-changes", product_id=row["id"]) or []
    if not suggestions:
        st.info("No evidence-backed suggestions yet. Five feedback responses are required.")
    for item in suggestions:
        with st.container(border=True):
            st.subheader(item["title"])
            st.write(item["rationale"])
            st.caption(f"{item['type']} · {item['priority']} priority · {item['effort']} effort")
            st.write("Evidence", item["evidence"])
            if item["status"] == "open":
                a, d, done = st.columns(3)
                if a.button("Accept", key=f"accept-{item['id']}"):
                    result = quiet(api.post, f"/owner/suggestions/{item['id']}/accept")
                    if result:
                        st.rerun()
                if d.button("Dismiss", key=f"dismiss-{item['id']}"):
                    quiet(api.post, f"/owner/suggestions/{item['id']}/dismiss")
                    st.rerun()
                if done.button("Mark done", key=f"done-{item['id']}"):
                    quiet(api.post, f"/owner/suggestions/{item['id']}/done")
                    st.rerun()
    st.subheader("Pending page changes")
    if not changes:
        st.caption("No page changes await approval.")
    for change in changes:
        if change["status"] != "pending":
            continue
        with st.container(border=True):
            st.write(f"**{change['target_section']}**")
            st.code(f"- {change['before']}\n+ {change['after']}")
            if st.button("Approve change for draft", key=f"change-{change['id']}"):
                quiet(api.post, f"/owner/page-changes/{change['id']}/approve")
                st.success("Draft updated. Publish it from Products when ready.")
                st.rerun()
