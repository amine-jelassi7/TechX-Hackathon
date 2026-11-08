from streamlit.testing.v1 import AppTest

from dashboard import api


def _app_test():
    def run_products_page():
        from dashboard.page_products import render

        render()

    return AppTest.from_function(run_products_page)


def test_products_page_empty_state(monkeypatch):
    monkeypatch.setattr(api, "get", lambda path, **params: [] if path == "/owner/products" else {})
    app = _app_test().run()
    assert any("No SaaS products yet" in item.value for item in app.info)
    assert any(item.label == "Save draft" for item in app.button)


def test_products_page_shows_publish_checklist_and_pdf_prefill(monkeypatch):
    row = {
        "id": "draft_test",
        "slug": "draft-test",
        "status": "draft",
        "needs_review": ["name"],
        "completeness": 0,
        "average_score": None,
        "has_unpublished_changes": False,
        "product": {},
    }
    monkeypatch.setattr(
        api, "get", lambda path, **params: [row] if path == "/owner/products" else {}
    )
    app = _app_test()
    app.session_state["active_product_id"] = "draft_test"
    app.session_state["pdf_prefill"] = {
        "fields": {"name": "Synthetic SaaS"},
        "confidence": {"name": 0.75},
        "source_snippets": {"name": "Product name: Synthetic SaaS"},
    }
    app.run()
    assert any("Required to publish" in item.value for item in app.markdown)
    assert any("please verify" in item.value.lower() for item in app.caption)
    assert any(item.label == "Confirm reviewed fields" for item in app.button)
