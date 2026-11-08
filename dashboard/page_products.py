"""Owner product intake, draft preview and publishing."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from dashboard import api
from dashboard.common import quiet

QUESTION_BANK_PATH = Path(__file__).resolve().parents[1] / "data" / "intake_questions.json"


@st.cache_data
def _question_bank() -> dict:
    return json.loads(QUESTION_BANK_PATH.read_text(encoding="utf-8"))


def _label(field: str, fallback: str, language: str) -> str:
    return _question_bank().get("questions", {}).get(field, {}).get(language, fallback)


def _blank_product() -> dict:
    return {
        "name": "",
        "tagline": "",
        "description": "",
        "audience": [],
        "price": 0,
        "currency": "TND",
        "plans": [],
        "features": [],
        "faq_seed": [],
        "policies": {},
        "contact": "",
        "tone": "professional",
        "website": "",
        "logo_url": "",
        "use_cases": [],
        "differentiators": [],
        "integrations": [],
        "tech_notes": "",
        "trial_policy": "",
        "support_channels": [],
        "sla": "",
    }


def render() -> None:
    st.title("Products")
    products = quiet(api.get, "/owner/products") or []
    if not products:
        st.info("No SaaS products yet. Add one to start the intake.")
    for row in products:
        col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
        col1.markdown(f"**{row['product'].get('name') or 'Untitled SaaS'}** · `{row['slug']}`")
        col2.write(row["status"].title())
        score = f" · score {row['average_score']}" if row.get("average_score") is not None else ""
        col3.write(f"{row['completeness']}% complete{score}")
        product = row["product"]
        missing = [
            label
            for label, valid in (
                ("name", bool(product.get("name"))),
                ("tagline", bool(product.get("tagline"))),
                ("problem", bool(product.get("description"))),
                ("audience", bool(product.get("audience"))),
                ("feature", bool(product.get("features"))),
                ("pricing", bool(product.get("plans") or product.get("contact"))),
                ("contact", bool(product.get("contact"))),
            )
            if not valid
        ]
        if row["status"] == "draft" or row.get("has_unpublished_changes"):
            confirmed = st.checkbox("Confirm publish", key=f"confirm-publish-{row['id']}")
            if col4.button(
                "Publish",
                key=f"publish-{row['id']}",
                disabled=bool(row["needs_review"] or missing) or not confirmed,
            ):
                try:
                    result = api.post(f"/owner/products/{row['id']}/publish")
                    st.success(f"Published at {result['url']}")
                    st.rerun()
                except api.ApiError as exc:
                    st.error(str(exc))
        else:
            col4.link_button("Open page", f"{api.base_url()}/p/{row['slug']}")
        with st.expander(f"Edit {row['product'].get('name') or row['slug']}"):
            if missing:
                st.write("Required to publish:", ", ".join(missing))
            if row["status"] == "draft" and st.button("Preview draft", key=f"preview-{row['id']}"):
                try:
                    preview, _ = api.download(f"/owner/products/{row['id']}/preview")
                    components.html(preview.decode("utf-8"), height=760, scrolling=True)
                except api.ApiError as exc:
                    st.error(str(exc))
            if row["status"] == "published":
                left, right = st.columns(2)
                if left.button("Unpublish", key=f"unpublish-{row['id']}"):
                    api.post(f"/owner/products/{row['id']}/unpublish")
                    st.rerun()
                if right.button("Archive", key=f"archive-{row['id']}"):
                    api.post(f"/owner/products/{row['id']}/archive")
                    st.rerun()
            _edit_product(row)

    st.divider()
    st.subheader("Add a SaaS")
    st.caption(
        "Optional text PDF prefill. Do not upload confidential or personal information. Scanned PDFs are not supported."
    )
    language = st.selectbox(
        "Question language",
        ["en", "fr"],
        format_func=lambda value: "English" if value == "en" else "Français",
    )
    upload = st.file_uploader(
        "Pitch deck or product document (PDF)", type=["pdf"], key="product_pdf"
    )
    with st.form("new-product"):
        st.markdown("**Identity**")
        name = st.text_input(_label("name", "Product name *", language))
        tagline = st.text_input(_label("tagline", "Tagline *", language))
        website = st.text_input(_label("website", "Website or demo link", language))
        logo_url = st.text_input(_label("logo_url", "Logo URL", language))
        st.markdown("**Problem and solution**")
        description = st.text_area(_label("description", "Problem solved *", language))
        audience = st.text_input(
            _label("audience", "Ideal audience *", language), help="Comma-separated"
        )
        use_cases = st.text_area(
            _label("use_cases", "Key use cases", language), help="One per line"
        )
        st.markdown("**Product**")
        features = st.text_area(
            _label("features", "Main features *", language), help="One feature per line"
        )
        differentiators = st.text_area(
            _label("differentiators", "Differentiators", language), help="One per line"
        )
        integrations = st.text_area(
            _label("integrations", "Integrations", language), help="One per line"
        )
        tech_notes = st.text_area(_label("tech_notes", "Security and compliance notes", language))
        st.markdown("**Commercial**")
        price = st.number_input(
            _label("plans", "Starting price", language), min_value=0.0, step=10.0
        )
        trial_policy = st.text_input(_label("trial_policy", "Trial or refund policy", language))
        st.markdown("**Support and brand**")
        support_channels = st.text_input(
            _label("support_channels", "Support channels", language), help="Comma-separated"
        )
        sla = st.text_input(_label("sla", "Support response time (optional)", language))
        tone = st.text_input(_label("tone", "Brand tone", language), value="professional")
        contact = st.text_input(_label("contact", "Contact email *", language))
        submitted = st.form_submit_button("Save draft")
    if submitted:
        body = {
            "product": {
                **_blank_product(),
                "name": name,
                "tagline": tagline,
                "description": description,
                "audience": [x.strip() for x in audience.split(",") if x.strip()],
                "features": [x.strip() for x in features.splitlines() if x.strip()],
                "price": price,
                "contact": contact,
                "website": website,
                "logo_url": logo_url,
                "use_cases": [x.strip() for x in use_cases.splitlines() if x.strip()],
                "differentiators": [x.strip() for x in differentiators.splitlines() if x.strip()],
                "integrations": [x.strip() for x in integrations.splitlines() if x.strip()],
                "tech_notes": tech_notes,
                "trial_policy": trial_policy,
                "support_channels": [x.strip() for x in support_channels.split(",") if x.strip()],
                "sla": sla,
                "tone": tone,
                "plans": ([{"id": "basic", "name": "Basic", "price": price}] if price else []),
            }
        }
        try:
            row = api.post("/owner/products", json=body)
            st.session_state["active_product_id"] = row["id"]
            st.success("Draft saved. You can now prefill from PDF and continue editing.")
            st.rerun()
        except api.ApiError as exc:
            st.error(str(exc))
    active_id = st.session_state.get("active_product_id")
    if upload and active_id and st.button("Extract PDF fields", key="extract-product-pdf"):
        try:
            result = api.upload(f"/owner/products/{active_id}/pdf", upload.name, upload.getvalue())
            st.session_state["pdf_prefill"] = result
            st.success("PDF text extracted. Review every suggested field before saving.")
        except api.ApiError as exc:
            st.error(str(exc))
    prefill = st.session_state.get("pdf_prefill")
    if prefill:
        st.markdown("**PDF suggestions — please verify**")
        for field, value in prefill.get("fields", {}).items():
            st.caption(
                f"{field} · confidence {prefill['confidence'].get(field, 0):.0%} · please verify"
            )
            key = f"pdf-review-{field}"
            if isinstance(value, list):
                st.session_state.setdefault(key, "\n".join(value))
                st.text_area(field, key=key)
            elif isinstance(value, (int, float)):
                st.session_state.setdefault(key, float(value))
                st.number_input(field, min_value=0.0, key=key)
            else:
                st.session_state.setdefault(key, str(value))
                st.text_input(field, key=key)
            with st.expander("Source snippet"):
                st.code(prefill["source_snippets"].get(field, ""))
        if st.button("Confirm reviewed fields", key="confirm-pdf-fields"):
            try:
                current = api.get(f"/owner/products/{active_id}")
                product = dict(current["product"])
                accepted = {}
                for key, raw in prefill["fields"].items():
                    if key not in product:
                        continue
                    current_value = st.session_state.get(f"pdf-review-{key}", raw)
                    if isinstance(raw, list):
                        current_value = [
                            item.strip() for item in str(current_value).splitlines() if item.strip()
                        ]
                    accepted[key] = current_value
                product.update(accepted)
                api.post(
                    f"/owner/products/{active_id}/confirm",
                    json={
                        "product": product,
                        "source": "mixed",
                        "confidence": prefill.get("confidence", {}),
                        "needs_review": [],
                    },
                )
                st.session_state.pop("pdf_prefill", None)
                st.success("Fields confirmed and saved as a draft.")
                st.rerun()
            except api.ApiError as exc:
                st.error(str(exc))


def _edit_product(row: dict) -> None:
    p = row["product"]
    product_id = row["id"]
    with st.form(f"edit-{product_id}"):
        name = st.text_input("Name", p.get("name", ""), key=f"name-{product_id}")
        tagline = st.text_input("Tagline", p.get("tagline", ""), key=f"tagline-{product_id}")
        description = st.text_area(
            "Problem / description", p.get("description", ""), key=f"description-{product_id}"
        )
        audience = st.text_input(
            "Audience (comma-separated)",
            ", ".join(p.get("audience", [])),
            key=f"audience-{product_id}",
        )
        features = st.text_area(
            "Features (one per line)",
            "\n".join(p.get("features", [])),
            key=f"features-{product_id}",
        )
        price = st.number_input(
            "Price", min_value=0.0, value=float(p.get("price", 0)), key=f"price-{product_id}"
        )
        contact = st.text_input("Contact", p.get("contact", ""), key=f"contact-{product_id}")
        save = st.form_submit_button("Save changes")
    if save:
        changed = {
            **p,
            "name": name,
            "tagline": tagline,
            "description": description,
            "audience": [x.strip() for x in audience.split(",") if x.strip()],
            "features": [x.strip() for x in features.splitlines() if x.strip()],
            "price": price,
            "contact": contact,
        }
        try:
            api.put(f"/owner/products/{product_id}", {"product": changed})
            st.success("Draft updated")
            st.rerun()
        except api.ApiError as exc:
            st.error(str(exc))
