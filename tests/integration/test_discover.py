from sqlmodel import select

from app.models import AuditEvent

ANSWERS = {
    "goal": "build-ai-products",
    "level": "beginner",
    "budget": "low",
    "format": "live-cohort",
    "audience": "student",
    "pace": "part-time",
}


def test_discover_page_renders(client):
    res = client.get("/discover")
    assert res.status_code == 200
    body = res.text
    assert "Find the product" in body
    assert "/api/recommend" in body
    assert "Guided discovery" in body


def test_product_page_links_to_discovery(client):
    body = client.get("/").text
    assert 'href="/discover"' in body
    assert "Find your fit" in body


def test_recommend_returns_ranked_results(client):
    res = client.post("/api/recommend", json=ANSWERS)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["trace_id"]
    results = data["results"]
    assert len(results) == 3
    assert results[0]["id"] == "prod_ai_bootcamp"
    assert results[0]["fit"] == 100
    assert results[0]["band"] == "Strong fit"
    assert [r["fit"] for r in results] == sorted([r["fit"] for r in results], reverse=True)
    required = {"id", "name", "tagline", "description", "audience", "price", "currency",
                "plans", "features", "contact", "fit", "band", "reasons"}
    assert required <= set(results[0])


def test_recommend_rejects_unknown_option(client):
    res = client.post("/api/recommend", json={**ANSWERS, "goal": "world-domination"})
    assert res.status_code == 422


def test_recommend_rejects_extra_field(client):
    res = client.post("/api/recommend", json={**ANSWERS, "email": "prospect@example.test"})
    assert res.status_code == 422


def test_recommend_is_written_to_the_audit_chain(client, db):
    res = client.post("/api/recommend", json=ANSWERS)
    trace_id = res.json()["trace_id"]
    db.expire_all()
    rows = db.exec(select(AuditEvent).where(AuditEvent.event_type == "product_recommended")).all()
    assert len(rows) == 1
    assert rows[0].trace_id == trace_id
    assert rows[0].actor == "discovery"
    assert rows[0].decision == "allow"
    assert "prod_ai_bootcamp" in rows[0].payload["top"]
