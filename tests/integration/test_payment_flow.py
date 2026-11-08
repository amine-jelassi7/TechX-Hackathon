import logging
import time

import pytest
from sqlmodel import select

from app import audit
from app.agents import payment as payment_agent
from app.agents.base import ToolRefused
from app.agents.payment import PaymentAgent
from app.config import settings
from app.models import ApprovalRequest, AuditEvent, Payment
from app.schemas import AgentMessage, Decision
from app.services import payments_sandbox


def pay(client, plan="std", token="tok_success", sid="pay1"):
    r = client.post("/api/pay", json={"session_id": sid, "plan_id": plan, "test_token": token})
    assert r.status_code == 200, r.text
    return r.json()


def kpis(client, owner):
    return client.get("/owner/kpis", headers=owner).json()


def event_types(db):
    db.expire_all()
    return [e.event_type for e in db.exec(select(AuditEvent).order_by(AuditEvent.seq))]


@pytest.mark.parametrize("token, status", [
    ("tok_success", "succeeded"),
    ("tok_declined", "declined"),
    ("tok_pending", "pending"),
    ("tok_whatever", "declined"),  # unknown but well-formed token: sandbox says invalid_token
])
def test_standard_plan_outcomes(client, owner, db, token, status):
    before = kpis(client, owner)
    out = pay(client, token=token)
    assert out["status"] == status and out["payment_id"] and out["trace_id"]
    after = kpis(client, owner)
    assert after["payments_by_status"].get(status, 0) == before["payments_by_status"].get(status, 0) + 1
    expected = 250.0 if status == "succeeded" else 0.0
    assert after["simulated_revenue"] == before["simulated_revenue"] + expected
    assert "payment_created" in event_types(db)


def test_amount_comes_from_product_not_client(client, db):
    out = pay(client, plan="std")
    db.expire_all()
    assert db.get(Payment, out["payment_id"]).amount == 250.0


@pytest.mark.parametrize("extra", [{"amount": 1}, {"card_number": "4242424242424242"}, {"cvv": "123"}])
def test_extra_fields_rejected_with_422(client, extra):
    r = client.post("/api/pay", json={"session_id": "x", "plan_id": "std", "test_token": "tok_success", **extra})
    assert r.status_code == 422


def test_unknown_plan_and_malformed_token_rejected(client, db):
    assert pay(client, plan="platinum")["status"] == "rejected"
    assert "payment_rejected" in event_types(db)
    # a card number as test_token is refused at the API boundary (422) and stored nowhere
    r = client.post("/api/pay", json={"session_id": "pay1", "plan_id": "std", "test_token": "4242 4242 4242 4242"})
    assert r.status_code == 422
    assert "4242" not in str([e.payload for e in db.exec(select(AuditEvent))])
    assert "4242" not in str([p.test_token for p in db.exec(select(Payment))])


def test_pro_plan_held_then_approved_by_owner(client, owner, db):
    before = kpis(client, owner)
    out = pay(client, plan="pro", sid="big1")
    assert out["status"] == "held_for_approval"
    assert kpis(client, owner)["simulated_revenue"] == before["simulated_revenue"]  # nothing charged yet

    pending = client.get("/owner/approvals?status=pending", headers=owner).json()
    item = next(a for a in pending if a["ref_id"] == out["payment_id"])
    assert item["kind"] == "payment" and "amount_over_threshold" in item["reasons"]
    assert item["payment"]["amount"] == 450.0 and item["payment"]["status"] == "held_for_approval"

    res = client.post(f"/owner/approvals/{item['id']}/approve", headers=owner)
    assert res.status_code == 200
    body = res.json()
    assert body["approval"]["status"] == "approved" and body["payment"]["status"] == "succeeded"
    assert kpis(client, owner)["simulated_revenue"] == before["simulated_revenue"] + 450.0
    types = event_types(db)
    assert "payment_held" in types and "payment_approved" in types
    assert client.post(f"/owner/approvals/{item['id']}/approve", headers=owner).status_code == 409
    db.expire_all()
    assert audit.verify_chain(db)["ok"]


def test_held_payment_rejected_by_owner(client, owner, db):
    out = pay(client, plan="pro", sid="big2")
    item = next(a for a in client.get("/owner/approvals", headers=owner).json() if a["ref_id"] == out["payment_id"])
    body = client.post(f"/owner/approvals/{item['id']}/reject", headers=owner).json()
    assert body["approval"]["status"] == "rejected" and body["payment"]["status"] == "rejected"
    assert kpis(client, owner)["simulated_revenue"] == 0.0
    assert "payment_rejected" in event_types(db)
    assert not [a for a in client.get("/owner/approvals", headers=owner).json() if a["id"] == item["id"]]


def test_burst_of_attempts_is_held(client, owner):
    statuses = [pay(client, token="tok_declined", sid="burst")["status"] for _ in range(5)]
    assert statuses[:2] == ["declined"] * 2  # two declines, then the next attempt trips the fraud rules
    assert statuses[2] == "held_for_approval"
    assert any(a["kind"] == "payment" for a in client.get("/owner/approvals", headers=owner).json())


def test_approval_unknown_id_404(client, owner):
    assert client.post("/owner/approvals/nope/approve", headers=owner).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Owner-Token": "wrong"}])
@pytest.mark.parametrize("path", ["/owner/kpis", "/owner/audit", "/owner/approvals", "/owner/qa"])
def test_owner_requires_token(client, headers, path):
    assert client.get(path, headers=headers).status_code == 401
    assert client.post("/owner/approvals/x/approve", headers=headers).status_code == 401


def test_owner_endpoints_shapes(client, owner):
    pay(client)
    audit_rows = client.get("/owner/audit?limit=3", headers=owner).json()
    assert len(audit_rows) == 3 and set(audit_rows[0]) == {
        "id", "ts", "trace_id", "actor", "event_type", "decision", "reasons", "payload"}
    assert audit_rows[0]["ts"] >= audit_rows[-1]["ts"]
    assert client.get("/owner/audit/verify", headers=owner).json()["ok"] is True
    assert client.get("/owner/alerts", headers=owner).status_code == 200
    assert client.get("/owner/evals", headers=owner).status_code == 200


def test_qa_moderation_flow(client, owner, db):
    gen = client.post("/owner/qa/generate", headers=owner).json()
    assert gen and all(e["status"] == "draft" for e in gen)
    eid = gen[0]["id"]
    assert client.post(f"/owner/qa/{eid}/approve", headers=owner).json()["status"] == "approved"
    assert "qa_approved" in event_types(db)
    edited = client.put(f"/owner/qa/{eid}", headers=owner,
                        json={"question": "q?", "answer": "a", "variants": ["v"]}).json()
    assert edited["status"] == "draft" and edited["question"] == "q?"
    assert client.post(f"/owner/qa/{eid}/reject", headers=owner).json()["status"] == "rejected"
    assert client.post("/owner/qa/missing/approve", headers=owner).status_code == 404


def test_home_page_renders_product(client):
    r = client.get("/")
    assert r.status_code == 200 and "AI Foundations Bootcamp" in r.text


def test_escalation_approvals_have_no_payment(client, owner, db):
    db.add(ApprovalRequest(kind="escalation", ref_id="i1", reasons=["classifier_unavailable"]))
    db.commit()
    item = client.get("/owner/approvals", headers=owner).json()[0]
    assert item["kind"] == "escalation" and item["payment"] is None
    approved = client.post(f"/owner/approvals/{item['id']}/approve", headers=owner).json()
    assert approved["payment"] is None and approved["approval"]["status"] == "approved"


# ---- review fixes: double charge, raw card tokens, approve race, audit gaps ----------------------
def _pay_msg(token="tok_success", trace="t-idem"):
    return AgentMessage(trace_id=trace, sender="orchestrator", recipient="payment", type="payment_request",
                        payload={"prospect_id": "pr1", "plan_id": "std", "test_token": token,
                                 "session_id": "s1"})


def test_slow_payment_past_timeout_is_charged_once(client, db, monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_timeout_s", 0.2)
    real = payments_sandbox.charge
    monkeypatch.setattr(payments_sandbox, "charge", lambda *a, **k: (time.sleep(0.6), real(*a, **k))[1])
    out = pay(client, sid="slow1")
    assert out["status"] == "error"  # timed out: fallback reply, not a retry
    time.sleep(1.0)  # abandoned worker finishes
    db.expire_all()
    assert len(db.exec(select(Payment)).all()) == 1


def test_same_message_twice_creates_one_payment_and_one_charge(db, monkeypatch):
    calls = []
    real = payments_sandbox.charge
    monkeypatch.setattr(payments_sandbox, "charge", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    agent = PaymentAgent(db)
    first, second = agent.handle(_pay_msg()), agent.handle(_pay_msg())
    assert first.payload["payment_id"] == second.payload["payment_id"]
    assert first.payload["status"] == second.payload["status"] == "succeeded"
    assert len(db.exec(select(Payment)).all()) == 1 and len(calls) == 1


@pytest.mark.parametrize("token", ["4111 1111 1111 1111", "4111111111111111", "tok_" + "a" * 40,
                                   "TOK_success", "tok success", "", "x" * 300])
def test_bad_test_token_is_422_and_never_stored(client, db, token):
    r = client.post("/api/pay", json={"session_id": "s", "plan_id": "std", "test_token": token})
    assert r.status_code == 422
    assert not db.exec(select(Payment)).all()


def test_agent_does_not_persist_unvalidated_token(db):
    out = PaymentAgent(db).handle(_pay_msg(token="4111 1111 1111 1111"))
    assert out.payload["status"] == "rejected"
    assert not db.exec(select(Payment)).all()
    assert "4111" not in repr([e.payload for e in db.exec(select(AuditEvent))])


def test_approve_race_second_decider_gets_409_and_no_double_charge(client, owner, db, monkeypatch):
    out = pay(client, plan="pro", sid="race1")
    assert out["status"] == "held_for_approval"
    item = client.get("/owner/approvals", headers=owner).json()[0]
    # A concurrent request already decided it, but this request read the row while still pending.
    real_pending = payment_agent._pending
    stale = real_pending(db, item["id"])
    db.expunge(stale)
    assert client.post(f"/owner/approvals/{item['id']}/reject", headers=owner).status_code == 200
    monkeypatch.setattr(payment_agent, "_pending", lambda s, i: stale)
    charges = []
    monkeypatch.setattr(payments_sandbox, "charge", lambda *a, **k: charges.append(1))
    res = client.post(f"/owner/approvals/{item['id']}/approve", headers=owner)
    assert res.status_code == 409 and not charges
    db.expire_all()
    assert db.get(Payment, out["payment_id"]).status == "rejected"


def test_hold_failure_is_audited(db, monkeypatch):
    agent = PaymentAgent(db)
    real_use_tool = agent.use_tool

    def use_tool(tool, args, ctx, **kw):
        if tool == "approval.create":
            raise ToolRefused(tool, Decision(action="block", reasons=["denied"], risk=1.0))
        return real_use_tool(tool, args, ctx, **kw)

    monkeypatch.setattr(agent, "use_tool", use_tool)
    monkeypatch.setattr(settings, "payment_approval_threshold", 1.0)
    out = agent.handle(_pay_msg())
    assert out.payload["status"] == "rejected"
    evs = [e for e in db.exec(select(AuditEvent)) if e.event_type == "payment_hold_failed"]
    assert len(evs) == 1 and "approval_unavailable" in evs[0].reasons_json


def test_qa_reject_and_update_are_audited(client, owner, db):
    eid = client.post("/owner/qa/generate", headers=owner).json()[0]["id"]
    client.put(f"/owner/qa/{eid}", headers=owner, json={"question": "q?", "answer": "a", "variants": []})
    client.post(f"/owner/qa/{eid}/reject", headers=owner)
    types = event_types(db)
    assert "qa_updated" in types and "qa_rejected" in types


@pytest.mark.parametrize("env, token, status", [
    ("prod", "change-me", 401), ("prod", "", 401), ("production", "change-me", 401),
    ("dev", "", 401), ("dev", "change-me", 200), ("prod", "a-real-secret", 200),
])
def test_default_owner_token_rejected_outside_dev(client, monkeypatch, env, token, status):
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "owner_token", token)
    assert client.get("/owner/kpis", headers={"X-Owner-Token": token}).status_code == status


@pytest.mark.parametrize("env, token, level, word", [
    ("prod", "change-me", logging.ERROR, "OWNER_TOKEN"),
    ("dev", "change-me", logging.WARNING, "OWNER_TOKEN"),
    ("prod", "", logging.ERROR, "OWNER_TOKEN"),
])
def test_startup_warns_about_default_owner_token(caplog, monkeypatch, env, token, level, word):
    from app.main import check_startup
    monkeypatch.setattr(settings, "app_env", env)
    monkeypatch.setattr(settings, "owner_token", token)
    with caplog.at_level(logging.WARNING):
        check_startup()
    recs = [r for r in caplog.records if word in r.getMessage()]
    assert recs and recs[0].levelno == level


def test_startup_quiet_with_real_token(caplog, monkeypatch):
    from app.main import check_startup
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "owner_token", "a-real-secret")
    with caplog.at_level(logging.WARNING):
        check_startup()
    assert not [r for r in caplog.records if "OWNER_TOKEN" in r.getMessage()]


def test_generate_qa_llm_failure_is_503_not_500(client, owner, monkeypatch):
    from app import llm
    from app.services import qa

    def boom(*a, **k):
        raise llm.LLMError("LLM call failed for task qa_generate: Timeout")

    monkeypatch.setattr(qa, "generate_qa", boom)
    r = client.post("/owner/qa/generate", headers=owner)
    assert r.status_code == 503 and "LLM" in r.json()["detail"]
