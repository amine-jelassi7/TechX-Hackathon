"""Owner API (docs/API_CONTRACT.md section 2). Prototype-grade auth: shared `X-Owner-Token` header."""
import hmac
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app import audit
from app.agents import insights as insights_agent
from app.agents import payment as payment_agent
from app.config import owner_api_enabled, settings
from app.db import get_session
from app.llm import LLMError
from app.models import ApprovalRequest, AuditEvent, Payment, QAEntry, SecurityAlert
from app.services import analytics, insights, qa, simulator

EVAL_PATH = Path(__file__).resolve().parents[2] / "data" / "eval_results.json"


def require_owner(x_owner_token: str | None = Header(default=None)) -> None:
    if not owner_api_enabled(settings):  # default/empty token outside dev: closed for everyone
        raise HTTPException(status_code=401, detail="owner API disabled: set OWNER_TOKEN")
    if not x_owner_token or not hmac.compare_digest(x_owner_token.encode(), settings.owner_token.encode()):
        raise HTTPException(status_code=401, detail="invalid owner token")


Db = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/owner", dependencies=[Depends(require_owner)])


# ---- KPIs ------------------------------------------------------------------------------------
@router.get("/kpis")
def kpis(session: Db) -> dict:
    return analytics.get_kpis(session).model_dump(mode="json")


@router.get("/kpis/narrative")
def narrative(session: Db) -> dict:
    return {"text": analytics.narrative_summary(analytics.get_kpis(session))}


@router.get("/kpis/forecast")
def forecast(session: Db, days: int = Query(7, ge=1, le=90)) -> dict:
    fn = getattr(analytics, "forecast_revenue", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="implemented in a later step")
    return fn(session, days_ahead=days)


# ---- audit and alerts --------------------------------------------------------------------------
def _audit_row(e: AuditEvent) -> dict:
    return {"id": e.id, "ts": e.ts.isoformat(), "trace_id": e.trace_id, "actor": e.actor,
            "event_type": e.event_type, "decision": e.decision, "reasons": list(e.reasons_json or []),
            "payload": e.payload or {}}


@router.get("/audit")
def audit_list(session: Db, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    rows = session.exec(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit))
    return [_audit_row(e) for e in rows]


@router.get("/audit/verify")
def audit_verify(session: Db) -> dict:
    return audit.verify_chain(session)


@router.get("/alerts")
def alerts(session: Db, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    rows = session.exec(select(SecurityAlert).order_by(SecurityAlert.ts.desc(), SecurityAlert.id).limit(limit))
    return [{"id": a.id, "ts": a.ts.isoformat(), "trace_id": a.trace_id, "alert_type": a.alert_type,
             "severity": a.severity, "detail": a.detail} for a in rows]


# ---- Q&A ---------------------------------------------------------------------------------------
class QAUpdate(BaseModel):
    question: str
    answer: str
    variants: list[str] = []


def _qa(e: QAEntry) -> dict:
    return {"id": e.id, "question": e.question, "variants": list(e.variants or []), "answer": e.answer,
            "status": e.status, "hits": e.hits}


def _or_404(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(status_code=404, detail="not found") from None


@router.get("/qa")
def qa_list(session: Db) -> list[dict]:
    return [_qa(e) for e in session.exec(select(QAEntry).order_by(QAEntry.id))]


@router.post("/qa/generate")
def qa_generate(session: Db) -> list[dict]:
    try:
        drafts = qa.generate_qa()
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=f"LLM unavailable, try again later ({exc})") from None
    return [_qa(e) for e in qa.save_drafts(session, drafts)]


@router.post("/qa/{entry_id}/approve")
def qa_approve(entry_id: str, session: Db) -> dict:
    entry = _or_404(qa.approve, session, entry_id)
    audit.log_event(session, trace_id=None, actor="owner", event_type="qa_approved", decision="approved",
                    payload={"qa_id": entry.id, "question": entry.question})
    return _qa(entry)


@router.post("/qa/{entry_id}/reject")
def qa_reject(entry_id: str, session: Db) -> dict:
    entry = _or_404(qa.reject, session, entry_id)
    audit.log_event(session, trace_id=None, actor="owner", event_type="qa_rejected", decision="rejected",
                    payload={"qa_id": entry.id, "question": entry.question})
    return _qa(entry)


@router.put("/qa/{entry_id}")
def qa_update(entry_id: str, body: QAUpdate, session: Db) -> dict:
    entry = _or_404(qa.update, session, entry_id, body.question, body.answer, body.variants)
    audit.log_event(session, trace_id=None, actor="owner", event_type="qa_updated", decision="draft",
                    payload={"qa_id": entry.id, "question": entry.question})
    return _qa(entry)


# ---- approvals ---------------------------------------------------------------------------------
def _payment(p: Payment | None) -> dict | None:
    if p is None:
        return None
    return {"id": p.id, "prospect_id": p.prospect_id, "plan_id": p.plan_id, "amount": p.amount,
            "currency": p.currency, "risk_score": p.risk_score, "status": p.status}


def _approval(a: ApprovalRequest, session: Session) -> dict:
    pay = session.get(Payment, a.ref_id) if a.kind == "payment" and a.ref_id else None
    return {"id": a.id, "kind": a.kind, "ref_id": a.ref_id, "reasons": list(a.reasons or []),
            "status": a.status, "created_at": a.created_at.isoformat(), "payment": _payment(pay)}


@router.get("/approvals")
def approvals(session: Db, status: str = "pending") -> list[dict]:
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id)
    if status != "all":
        stmt = stmt.where(ApprovalRequest.status == status)
    return [_approval(a, session) for a in session.exec(stmt)]


def _decide(fn, approval_id: str, session: Session) -> dict:
    try:
        req, pay = fn(session, approval_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="not found") from None
    except payment_agent.ApprovalStateError:
        raise HTTPException(status_code=409, detail="approval already decided") from None
    return {"approval": _approval(req, session), "payment": _payment(pay)}


@router.post("/approvals/{approval_id}/approve")
def approval_approve(approval_id: str, session: Db) -> dict:
    return _decide(payment_agent.approve, approval_id, session)


@router.post("/approvals/{approval_id}/reject")
def approval_reject(approval_id: str, session: Db) -> dict:
    return _decide(payment_agent.reject, approval_id, session)


# ---- evals -------------------------------------------------------------------------------------
@router.get("/evals")
def evals() -> dict:
    if not EVAL_PATH.exists():
        return {}
    try:
        return json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---- insights, recommendations, outreach drafts ----------------------------------------------
@router.post("/insights/run")
def insights_run(session: Db, mode: str = Query("analyze", pattern="^(analyze|opportunities)$")):
    """Dispatched through the orchestrator: shows up as agent_message events, retried on failure."""
    payload, _ok = insights_agent.run(session, mode)
    if mode == "analyze":
        return {k: payload.get(k, []) for k in ("needs", "objections", "recommendations")}
    return payload.get("items", [])


@router.get("/recommendations")
def recommendations(session: Db) -> list[dict]:
    return insights.list_recommendations(session)


@router.get("/drafts")
def drafts(session: Db) -> list[dict]:
    return insights.list_drafts(session)


@router.post("/drafts/{draft_id}/mark_sent")
def draft_mark_sent(draft_id: str, session: Db) -> dict:
    return _or_404(insights.mark_sent, session, draft_id)


# ---- simulator ---------------------------------------------------------------------------------
@router.post("/simulate")
def simulate(session: Db, scenario: str = Query("demo", pattern="^(demo|attack)$")) -> dict:
    return simulator.run_demo(session) if scenario == "demo" else simulator.run_attacks(session)
