import time

from sqlmodel import select

from app import audit
from app.config import settings
from app.models import AuditEvent, Interaction, Lead, OutreachDraft


def chat(client, text, sid="flow1"):
    r = client.post("/api/chat", json={"session_id": sid, "message": text})
    assert r.status_code == 200, r.text
    return r.json()


def events(db, event_type):
    db.expire_all()
    return [e for e in db.exec(select(AuditEvent).order_by(AuditEvent.seq)) if e.event_type == event_type]


def add_leads(db):
    db.add_all([Lead(id="lc1", first_name="Amine", email="a@x.test", interest="AI chatbots", source="referral",
                     consent=True),
                Lead(id="lc2", first_name="Sara", email="s@x.test", interest="python and AI", source="event",
                     consent=True),
                Lead(id="ln1", first_name="Nope", email="n@x.test", interest="AI bootcamp", source="referral",
                     consent=False)])
    db.commit()


# ---- full flow: chat -> interest -> pay -> KPIs -> recommendations ----------------------------------
def test_full_flow_to_recommendations_with_valid_evidence(client, owner, db):
    chat(client, "It is too expensive for me")
    assert chat(client, "I want to join the bootcamp")["interest_level"] == "high"
    pay = client.post("/api/pay", json={"session_id": "flow1", "plan_id": "std", "test_token": "tok_success"}).json()
    assert pay["status"] == "succeeded"

    k = client.get("/owner/kpis", headers=owner).json()
    assert k["paid_prospects"] == 1 and k["simulated_revenue"] == 250.0 and k["interactions_count"] >= 2

    res = client.post("/owner/insights/run?mode=analyze", headers=owner)
    assert res.status_code == 200
    recs = res.json()["recommendations"]
    assert recs
    db.expire_all()
    ids = {i.id for i in db.exec(select(Interaction))}
    for r in recs:
        assert r["evidence"]["interaction_ids"] and set(r["evidence"]["interaction_ids"]) <= ids
    assert [r["id"] for r in client.get("/owner/recommendations", headers=owner).json()]

    # visible collaboration: orchestrator -> insights, payment -> insights after the successful payment
    msgs = [(e.payload["sender"], e.payload["recipient"], e.payload["type"]) for e in events(db, "agent_message")]
    assert ("orchestrator", "insights", "insights_analyze") in msgs
    assert ("insights", "orchestrator", "insights_analyze_result") in msgs
    assert ("payment", "insights", "payment_result") in msgs
    assert audit.verify_chain(db)["ok"]


def test_owner_insights_requires_token_and_valid_mode(client, owner):
    assert client.post("/owner/insights/run?mode=analyze").status_code == 401
    assert client.post("/owner/insights/run?mode=nope", headers=owner).status_code == 422
    assert client.post("/owner/simulate?scenario=nope", headers=owner).status_code == 422


# ---- outreach ---------------------------------------------------------------------------------
def test_outreach_consent_opt_out_dedupe_mark_sent(client, owner, db):
    add_leads(db)
    first = client.post("/owner/insights/run?mode=opportunities", headers=owner).json()
    assert {d["lead_id"] for d in first} == {"lc1", "lc2"}  # non-consented lead never drafted
    drafts = client.get("/owner/drafts", headers=owner).json()
    assert len(drafts) == 2 and all(d["status"] == "draft" for d in drafts)
    assert all(d["draft_message"].rstrip().endswith("Reply STOP to opt out.") for d in drafts)
    assert "ln1" not in {d["lead_id"] for d in drafts}

    again = client.post("/owner/insights/run?mode=opportunities", headers=owner).json()
    assert again == []  # dedupe: leads with an open draft get no second one
    db.expire_all()
    assert len(list(db.exec(select(OutreachDraft)))) == 2

    sent = client.post(f"/owner/drafts/{drafts[0]['id']}/mark_sent", headers=owner)
    assert sent.status_code == 200 and sent.json()["status"] == "sent_manually"
    assert client.post("/owner/drafts/nope/mark_sent", headers=owner).status_code == 404
    assert events(db, "draft_marked_sent")


# ---- agent failure: slow insights agent -------------------------------------------------------------
def test_insights_timeout_is_not_retried_logged_and_workflow_continues(client, owner, db, monkeypatch):
    calls = []

    def slow(session, n=50, **kw):
        calls.append(1)
        time.sleep(0.4)

    monkeypatch.setattr("app.agents.insights.service.analyze", slow)
    monkeypatch.setattr(settings, "orchestrator_timeout_s", 0.05)
    monkeypatch.setattr(settings, "orchestrator_retries", 1)

    res = client.post("/owner/insights/run?mode=analyze", headers=owner)
    assert res.status_code == 200 and res.json()["recommendations"] == []  # fallback, no crash
    err = events(db, "agent_error")
    assert len(err) == 1 and err[0].payload["attempts"] == 1 and err[0].payload["recipient"] == "insights"

    # the rest of the workflow is unaffected
    assert chat(client, "How much does it cost?")["reply"]
    pay = client.post("/api/pay", json={"session_id": "after", "plan_id": "std", "test_token": "tok_success"})
    assert pay.json()["status"] == "succeeded"
    time.sleep(0.9)  # let abandoned workers finish before the fixture closes the connection
    assert len(calls) == 1  # a handler that writes rows is never re-run (no duplicate recommendations)


# ---- simulator --------------------------------------------------------------------------------
def test_simulate_demo_twice(client, owner, db):
    for _ in range(2):
        res = client.post("/owner/simulate?scenario=demo", headers=owner)
        assert res.status_code == 200
        steps = {s["step"]: s["result"] for s in res.json()["steps"]}
        assert len(steps) >= 8
        q = [r for n, r in steps.items() if n.startswith("2.")]
        assert [x["source"] for x in q].count("qa") == 2 and q[2]["source"] != "qa"
        assert steps["3 prompt injection"]["blocked"] is True
        assert steps["4 pay standard plan (tok_success)"]["status"] == "succeeded"
        assert steps["5 second prospect: burst then pro plan"]["status"] == "held_for_approval"
        assert steps["6b insights: analyze"]["ok"] and steps["6b insights: analyze"]["recommendations"]
        assert steps["8 audit chain"]["ok"] is True
    pending = client.get("/owner/approvals", headers=owner).json()
    assert len(pending) == 4  # per run: the 3rd attempt after two declines + the pro plan are held


def test_simulate_attack_decisions(client, owner):
    res = client.post("/owner/simulate?scenario=attack", headers=owner)
    assert res.status_code == 200
    steps = res.json()["steps"]
    detail = [s["result"] for s in steps[:-1]]
    assert len(detail) == 13 and all("got" in d and "expected" in d for d in detail)
    assert all(d["ok"] for d in detail), [d for d in detail if not d["ok"]]
    assert next(d for d in detail if d["id"] == "atk_08")["layer"] == "schema_422"
    assert steps[-1]["result"]["audit_chain"]["ok"] is True


def test_three_scene_actor_mapping():
    from dashboard.three_scene import canonical_actor

    assert canonical_actor("owner") == "orchestrator"
    assert canonical_actor("sales", "gateway_block") == "gateway"
    assert canonical_actor("insights", "permission_denied") == "gateway"
    for a in ("orchestrator", "sales", "payment", "insights"):
        assert canonical_actor(a, "agent_message") == a


def test_generated_ids_never_look_like_card_numbers():
    import re

    from app.gateway import filters
    from app.models import new_id

    for _ in range(3000):
        i = new_id()
        assert max(map(len, re.findall(r"\d+", i)), default=0) < 13
        assert not filters.find_card_spans(i)
