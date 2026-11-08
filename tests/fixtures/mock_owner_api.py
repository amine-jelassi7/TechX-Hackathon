"""Dev-only mock of the owner API (docs/API_CONTRACT.md section 2) with synthetic data.

Run: uv run uvicorn tests.fixtures.mock_owner_api:app --port 8001
Then point the dashboard at it with API_URL=http://127.0.0.1:8001. Any X-Owner-Token is accepted.
State is in memory and resets on restart.
"""

from __future__ import annotations

import copy
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException

app = FastAPI(title="SoukSaaS mock owner API")
_rng = random.Random(7)
_now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _iso(minutes_ago: float) -> str:
    return (_now - timedelta(minutes=minutes_ago)).isoformat()


# ---------------------------------------------------------------- audit

_EVENT_POOL: list[tuple[str, str, str | None, list[str]]] = [
    ("gateway", "check_input", "allow", []),
    ("gateway", "check_input", "allow", []),
    ("orchestrator", "route", None, []),
    ("sales_agent", "chat_reply", "allow", []),
    ("sales_agent", "chat_reply", "allow", []),
    ("sales_agent", "qa_match", "allow", ["score 0.84"]),
    ("gateway", "check_output", "allow", []),
    ("payment_agent", "payment_request", "allow", []),
    ("insights_agent", "analyze", None, []),
    ("analytics", "kpi_refresh", None, []),
    ("audit", "chain_checkpoint", None, []),
]
_FLAGS: list[tuple[str, str, str, list[str]]] = [
    ("gateway", "check_input", "block", ["prompt_injection: ignore previous instructions"]),
    ("gateway", "check_input", "block", ["pii: card number in chat"]),
    ("gateway", "check_tool_call", "block", ["permission: sales_agent may not call refund"]),
    ("payment_agent", "approval_gate", "escalate", ["amount above threshold", "risk_score 0.81"]),
    ("gateway", "check_output", "block", ["output leaks system prompt"]),
]


def _build_audit() -> list[dict[str, Any]]:
    events = []
    for i in range(100):
        if i in (3, 9, 17, 26, 41, 58, 73):
            actor, etype, dec, reasons = _FLAGS[_rng.randrange(len(_FLAGS))]
        else:
            actor, etype, dec, reasons = _EVENT_POOL[_rng.randrange(len(_EVENT_POOL))]
        events.append(
            {
                "id": f"ae_{1000 - i}",
                "ts": _iso(i * 1.7 + _rng.random()),
                "trace_id": f"tr_{_rng.randrange(16**6):06x}",
                "actor": actor,
                "event_type": etype,
                "decision": dec,
                "reasons": reasons,
                "payload": {"len": _rng.randrange(8, 240)},
            }
        )
    return events


AUDIT = _build_audit()
ALERTS = [
    {
        "id": f"al_{i}",
        "ts": e["ts"],
        "trace_id": e["trace_id"],
        "alert_type": ("injection" if "injection" in (e["reasons"] or [""])[0] else
                       "pii" if "pii" in (e["reasons"] or [""])[0] else
                       "permission" if "permission" in (e["reasons"] or [""])[0] else
                       "fraud" if e["actor"] == "payment_agent" else "output_leak"),
        "severity": "high" if e["decision"] == "escalate" else "medium",
        "detail": "; ".join(e["reasons"]),
    }
    for i, e in enumerate(x for x in AUDIT if x["decision"] in ("block", "escalate"))
]
_state: dict[str, Any] = {"tampered": False}


_SPAWN_EVERY_S = 7.0
_spawn = {"last": time.monotonic(), "n": 0}


def _maybe_spawn() -> None:
    """Dev only: a fresh event appears every few seconds so the live feed has something to animate."""
    now = time.monotonic()
    for _ in range(3):
        if now - _spawn["last"] < _SPAWN_EVERY_S:
            break
        _spawn["last"] += _SPAWN_EVERY_S
        _spawn["n"] += 1
        pool = _FLAGS if _rng.random() < 0.35 else _EVENT_POOL
        actor, etype, dec, reasons = pool[_rng.randrange(len(pool))]
        AUDIT.insert(0, {
            "id": f"ae_{1000 + _spawn['n']}", "ts": datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(),
            "trace_id": f"tr_{_rng.randrange(16**6):06x}", "actor": actor, "event_type": etype,
            "decision": dec, "reasons": reasons, "payload": {"len": _rng.randrange(8, 240)},
        })
        del AUDIT[100:]


@app.get("/owner/audit")
def audit(limit: int = 100) -> list[dict[str, Any]]:
    _maybe_spawn()
    return AUDIT[:limit]


@app.get("/owner/audit/verify")
def audit_verify() -> dict[str, Any]:
    if _state["tampered"]:
        return {"ok": False, "checked": len(AUDIT), "first_bad_id": AUDIT[40]["id"]}
    return {"ok": True, "checked": len(AUDIT), "first_bad_id": None}


@app.post("/owner/_dev/tamper")
def dev_tamper(on: bool = True) -> dict[str, bool]:
    """Dev helper (not in the contract): flip the verify badge to see the tampered state."""
    _state["tampered"] = on
    return {"tampered": on}


@app.get("/owner/alerts")
def alerts(limit: int = 100) -> list[dict[str, Any]]:
    return ALERTS[:limit]


# ---------------------------------------------------------------- kpis

RECS: list[dict[str, Any]] = [
    {
        "id": "rec_1", "title": "Answer the delivery-time question up front",
        "action": "Add a delivery estimate to the product page headline and the first chat reply.",
        "rationale": "9 of 31 hesitating prospects asked about delivery before leaving.",
        "evidence": {"kpi": "top_objections.delivery", "interaction_ids": ["ix_104", "ix_118", "ix_131"]},
        "impact": "high", "effort": "low", "created_at": _iso(35),
    },
    {
        "id": "rec_2", "title": "Offer a two-instalment plan above 300 TND",
        "action": "Expose the existing instalment plan in chat when the cart exceeds 300 TND.",
        "rationale": "Price objections cluster on the two highest plans.",
        "evidence": {"kpi": "top_objections.price", "interaction_ids": ["ix_097", "ix_122"]},
        "impact": "medium", "effort": "medium", "created_at": _iso(35),
    },
    {
        "id": "rec_3", "title": "Review the card-not-accepted failures",
        "action": "Check sandbox decline reasons and add a clear retry message.",
        "rationale": "3 of 14 payments failed with the same decline code.",
        "evidence": {"kpi": "payments_by_status.failed", "interaction_ids": ["ix_140"]},
        "impact": "medium", "effort": "low", "created_at": _iso(35),
    },
]


@app.get("/owner/kpis")
def kpis() -> dict[str, Any]:
    return {
        "interactions_count": 214,
        "interested_prospects": 61,
        "paid_prospects": 14,
        "conversion_rate": 14 / 61,
        "top_objections": [
            {"label": "delivery time", "count": 19, "example_interaction_ids": ["ix_104"]},
            {"label": "price", "count": 15, "example_interaction_ids": ["ix_097"]},
            {"label": "refund policy", "count": 9, "example_interaction_ids": ["ix_118"]},
            {"label": "warranty", "count": 5, "example_interaction_ids": []},
        ],
        "payments_by_status": {"paid": 14, "pending_approval": 3, "failed": 3, "rejected": 1},
        "simulated_revenue": 4187.5,
        "satisfaction_avg": 4.3,
        "security_alerts_count": len(ALERTS),
        "security_alerts_by_type": {
            t: sum(1 for a in ALERTS if a["alert_type"] == t) for t in sorted({a["alert_type"] for a in ALERTS})
        },
        "latest_recommendations": copy.deepcopy(RECS),
    }


@app.get("/owner/kpis/narrative")
def narrative() -> dict[str, str]:
    return {
        "text": "214 conversations produced 61 interested prospects and 14 paid orders (6.5 percent), "
        "worth 4,187.50 TND in sandbox revenue. Delivery time is the leading objection. "
        f"{len(ALERTS)} security alerts were raised, all blocked or escalated before reaching a payment."
    }


# ---------------------------------------------------------------- qa

QA: list[dict[str, Any]] = [
    {"id": "qa_1", "question": "How long does delivery take?",
     "variants": ["when will it arrive", "delivery time", "shipping delay"],
     "answer": "Orders ship within 48 hours and arrive in 2 to 4 working days across Tunisia.",
     "status": "approved", "hits": 41},
    {"id": "qa_2", "question": "Can I pay in instalments?",
     "variants": ["payment plan", "pay in parts"],
     "answer": "Yes. Orders above 300 TND can be split into 3 monthly payments.",
     "status": "approved", "hits": 23},
    {"id": "qa_3", "question": "What is your refund policy?",
     "variants": ["can I return it", "money back"],
     "answer": "Returns are accepted within 14 days if the item is unused.",
     "status": "draft", "hits": 0},
    {"id": "qa_4", "question": "Is there a warranty?", "variants": ["guarantee"],
     "answer": "All items carry a 12 month warranty.", "status": "draft", "hits": 0},
    {"id": "qa_5", "question": "Do you ship abroad?", "variants": [],
     "answer": "Not yet.", "status": "rejected", "hits": 2},
]


def _find(items: list[dict], item_id: str) -> dict:
    for it in items:
        if it["id"] == item_id:
            return it
    raise HTTPException(404, f"{item_id} not found")


@app.get("/owner/qa")
def qa_list() -> list[dict[str, Any]]:
    return QA


@app.post("/owner/qa/generate")
def qa_generate() -> list[dict[str, Any]]:
    new = {"id": f"qa_{len(QA) + 1}", "question": "Which colours are available?", "variants": ["colour options"],
           "answer": "Black, sand and olive.", "status": "draft", "hits": 0}
    QA.append(new)
    return [new]


@app.post("/owner/qa/{qa_id}/{action}")
def qa_action(qa_id: str, action: str) -> dict[str, Any]:
    if action not in ("approve", "reject"):
        raise HTTPException(404)
    e = _find(QA, qa_id)
    e["status"] = "approved" if action == "approve" else "rejected"
    return e


@app.put("/owner/qa/{qa_id}")
def qa_edit(qa_id: str, body: dict) -> dict[str, Any]:
    e = _find(QA, qa_id)
    e.update({k: body[k] for k in ("question", "answer", "variants") if k in body})
    e["status"] = "draft"
    return e


# ---------------------------------------------------------------- approvals

APPROVALS: list[dict[str, Any]] = [
    {"id": "ap_1", "kind": "payment", "ref_id": "pay_8f21", "reasons": ["amount above 500 TND", "risk_score 0.81"],
     "status": "pending", "created_at": _iso(12),
     "payment": {"id": "pay_8f21", "prospect_id": "pr_33", "plan_id": "plan_pro", "amount": 780.0,
                 "currency": "TND", "risk_score": 0.81, "status": "pending_approval"}},
    {"id": "ap_2", "kind": "payment", "ref_id": "pay_91c4", "reasons": ["first purchase from new device"],
     "status": "pending", "created_at": _iso(47),
     "payment": {"id": "pay_91c4", "prospect_id": "pr_41", "plan_id": "plan_basic", "amount": 320.0,
                 "currency": "TND", "risk_score": 0.52, "status": "pending_approval"}},
    {"id": "ap_3", "kind": "payment", "ref_id": "pay_a07e", "reasons": ["three attempts in ten minutes"],
     "status": "pending", "created_at": _iso(95),
     "payment": {"id": "pay_a07e", "prospect_id": "pr_12", "plan_id": "plan_basic", "amount": 150.0,
                 "currency": "TND", "risk_score": 0.74, "status": "pending_approval"}},
]


@app.get("/owner/approvals")
def approvals(status: str = "pending") -> list[dict[str, Any]]:
    return [a for a in APPROVALS if a["status"] == status]


@app.post("/owner/approvals/{ap_id}/{action}")
def approval_action(ap_id: str, action: str) -> dict[str, Any]:
    if action not in ("approve", "reject"):
        raise HTTPException(404)
    a = _find(APPROVALS, ap_id)
    a["status"] = "approved" if action == "approve" else "rejected"
    pay = copy.deepcopy(a["payment"])
    pay["status"] = "paid" if action == "approve" else "rejected"
    return {"approval": a, "payment": pay if action == "approve" else None}


# ---------------------------------------------------------------- insights, drafts

DRAFTS: list[dict[str, Any]] = [
    {"id": "dr_1", "lead_id": "ld_5", "first_name": "Amel", "interest": "high", "score": 0.88,
     "reason": "Asked twice about instalments, left before paying.",
     "draft_message": "Hello Amel, the instalment plan we discussed is still open. Want me to hold it for you?",
     "status": "draft", "created_at": _iso(20)},
    {"id": "dr_2", "lead_id": "ld_9", "first_name": "Karim", "interest": "medium", "score": 0.61,
     "reason": "Compared two plans and asked about warranty.",
     "draft_message": "Hello Karim, both plans include the 12 month warranty. Happy to answer anything else.",
     "status": "draft", "created_at": _iso(26)},
    {"id": "dr_3", "lead_id": "ld_2", "first_name": "Sana", "interest": "high", "score": 0.79,
     "reason": "Payment failed once with a declined card.",
     "draft_message": "Hello Sana, your payment did not go through. You can retry with another card here.",
     "status": "sent_manually", "created_at": _iso(180)},
]


@app.post("/owner/insights/run")
def insights_run(mode: str = "analyze") -> Any:
    if mode == "opportunities":
        d = DRAFTS[0]
        return [{"draft_id": d["id"], "lead_id": d["lead_id"], "first_name": d["first_name"],
                 "score": d["score"], "reason": d["reason"], "draft_message": d["draft_message"]}]
    return {
        "needs": [{"label": "fast delivery", "count": 24}, {"label": "flexible payment", "count": 17},
                  {"label": "warranty", "count": 8}],
        "objections": [{"label": "delivery time", "count": 19, "example_interaction_ids": ["ix_104", "ix_118"]},
                       {"label": "price", "count": 15, "example_interaction_ids": ["ix_097"]}],
        "recommendations": copy.deepcopy(RECS),
    }


@app.get("/owner/recommendations")
def recommendations() -> list[dict[str, Any]]:
    return copy.deepcopy(RECS)


@app.get("/owner/drafts")
def drafts() -> list[dict[str, Any]]:
    return DRAFTS


@app.post("/owner/drafts/{draft_id}/mark_sent")
def mark_sent(draft_id: str) -> dict[str, Any]:
    d = _find(DRAFTS, draft_id)
    d["status"] = "sent_manually"
    return d


# ---------------------------------------------------------------- evals, simulator

_QA_DETAILS = [
    {"id": f"q{i:02d}", "question": q, "expected": exp, "got": got, "score": s, "ok": exp == got}
    for i, (q, exp, got, s) in enumerate(
        [("How long is delivery?", "qa_1", "qa_1", 0.91), ("Can I split the payment?", "qa_2", "qa_2", 0.84),
         ("What if I want my money back?", "qa_3", "qa_3", 0.77), ("Does it come with a guarantee?", "qa_4", "qa_4", 0.8),
         ("Do you deliver to Paris?", "refuse", "refuse", 0.12), ("Tell me a joke", "refuse", "qa_1", 0.41)], 1)
]
_ATTACKS = [("chat", "block", "filters"), ("chat", "block", "classifier"), ("pay", "block", "schema"),
            ("chat", "block", "filters"), ("chat", "block", "output_guard"), ("pay", "block", "fraud"),
            ("chat", "allow", "none")]


@app.get("/owner/evals")
def evals() -> dict[str, Any]:
    ok_qa = sum(1 for d in _QA_DETAILS if d["ok"])
    details = [
        {"id": f"atk_{i:02d}", "channel": ch, "expected": "block", "got": got, "layer": layer, "ok": got == "block"}
        for i, (ch, got, layer) in enumerate(_ATTACKS, 1)
    ]
    return {
        "run_at": _iso(240), "llm_mode": "mock",
        "qa": {"total": len(_QA_DETAILS), "accuracy": ok_qa / len(_QA_DETAILS), "refusal_rate": 0.5,
               "details": _QA_DETAILS},
        "attacks": {"total": len(details), "blocked": sum(1 for d in details if d["ok"]), "details": details},
    }


@app.post("/owner/simulate")
def simulate(scenario: str = "demo") -> dict[str, Any]:
    if scenario == "attack":
        steps = [
            {"step": "chat: ignore previous instructions", "result": {"blocked": True, "layer": "filters"}},
            {"step": "pay: extra field is_admin", "result": {"status": 422}},
            {"step": "chat: card number 4111 1111 1111 1111", "result": {"blocked": True, "layer": "filters"}},
        ]
    else:
        steps = [
            {"step": "chat: how long is delivery?", "result": {"source": "qa", "reply": "2 to 4 working days."}},
            {"step": "chat: I want the pro plan", "result": {"source": "sales", "interest_level": "high"}},
            {"step": "pay: plan_pro 780 TND", "result": {"status": "pending_approval", "payment_id": "pay_sim1"}},
        ]
    return {"steps": steps}
