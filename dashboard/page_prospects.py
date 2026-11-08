"""Research and rank likely buyers from the selected SaaS idea profile."""

from __future__ import annotations

import json

import streamlit as st

from dashboard import api, theme
from dashboard.common import call


def render() -> None:
    theme.page_head(
        "Buyer discovery",
        "Research companies that match the selected SaaS idea and show recent buying signals.",
    )
    st.caption(
        "This run uses Tavily web search to find companies with recent buying signals. "
        "Hunter contact enrichment is optional; without it, public company contact pages are checked. "
        "The run may consume provider credits. Set TAVILY_API_KEY in .env; add HUNTER_API_KEY for enrichment. "
        "Only public business information is shown; LinkedIn is not scraped and no messages are sent."
    )
    with st.form("buyer_research"):
        location = st.text_input("Target location (optional)", placeholder="Tunisia, France, or leave blank")
        limit = st.selectbox("Shortlist size", [5, 10, 15], index=2)
        confirm_usage = st.checkbox("I understand this run may use search and enrichment API credits.")
        submitted = st.form_submit_button("Research likely buyers", type="primary")

    if submitted:
        if not api.selected_product_id:
            st.warning("Select a product in the sidebar first.")
        elif not confirm_usage:
            st.warning("Confirm possible API usage before starting research.")
        else:
            st.session_state.pop("buyer_research_result", None)
            ok, result = call(
                api.post,
                "/owner/prospects/discover",
                json={
                    "product_id": api.selected_product_id,
                    "location": location.strip(),
                    "limit": limit,
                },
            )
            if ok:
                st.session_state["buyer_research_result"] = result

    result = st.session_state.get("buyer_research_result")
    if not result:
        st.info("Run research to build a shortlist from the selected product profile.")
        return

    st.metric("Companies with a recent signal and contact path", result.get("count", 0))
    st.caption(
        f"Requested up to {result.get('target_count', 15)} companies. Scores rank discovered evidence; "
        "they are not a guarantee that a company will buy."
    )
    with st.expander("Search queries built from the idea profile"):
        for query in result.get("queries", []):
            st.write(f"- {query}")

    companies = result.get("companies") or []
    if not companies:
        st.warning(
            "No companies had both a recent, source-backed activity signal and a public contact path. "
            "Try a broader location or review the product audience and features."
        )
        return

    st.download_button(
        "Download shortlist JSON",
        data=json.dumps(companies, ensure_ascii=False, indent=2),
        file_name="buyer-shortlist.json",
        mime="application/json",
    )
    for index, company in enumerate(companies, 1):
        with st.container(border=True):
            name_col, score_col = st.columns([4, 1])
            name_col.subheader(company.get("company_name") or "Company")
            score_col.metric("Likelihood", f"{company.get('likelihood_score', 0)} / 100")
            if company.get("website"):
                st.link_button("Company website", company["website"], key=f"buyer-web-{index}")
            st.write(company.get("why_likely_buyer", ""))

            st.markdown("**Recent activity evidence**")
            for signal in company.get("signals", []):
                st.write(f"{signal.get('date')} · {signal.get('type', '').title()} · {signal.get('title')}")
                if signal.get("source_url"):
                    st.link_button(
                        "Open source",
                        signal["source_url"],
                        key=f"buyer-evidence-{index}-{signal.get('type')}",
                    )

            st.markdown("**Decision-maker contacts**")
            contacts = company.get("contacts") or []
            if contacts:
                for contact_index, contact in enumerate(contacts, 1):
                    who, email_col, source_col = st.columns([2, 3, 2])
                    who.write(" ".join(value for value in (contact.get("name"), contact.get("role")) if value) or "Name/role not listed")
                    email_col.write(contact.get("email") or "Email not found")
                    label = contact.get("source_label") or "scraped, unverified"
                    confidence = contact.get("confidence")
                    source_col.caption(f"{label}" + (f" · confidence {confidence}%" if confidence is not None else ""))
                    if contact.get("source_url"):
                        st.link_button(
                            "Contact source",
                            contact["source_url"],
                            key=f"buyer-contact-{index}-{contact_index}",
                        )
            else:
                st.write("No named decision-maker email found; use the public contact page below.")

            if company.get("contact_page_url"):
                st.link_button(
                    "Company contact page",
                    company["contact_page_url"],
                    key=f"buyer-contact-page-{index}",
                )
            phone = company.get("phone")
            if phone:
                st.write(f"**{phone.get('label', 'Company main line')}:** {phone.get('value')}")
                if phone.get("source_url"):
                    st.caption(f"Source: {phone['source_url']}")
