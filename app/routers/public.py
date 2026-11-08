"""Public endpoints (docs/API_CONTRACT.md section 3). No card fields anywhere; amount comes from product.json."""
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.llm import load_product
from app.models import Interaction
from app.orchestrator import build_orchestrator
from app.schemas import AgentMessage, ChatRequest, PayRequest, RatingRequest
from app.services.prospects import prospect_for_session

Db = Annotated[Session, Depends(get_session)]

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _trace() -> str:
    return uuid.uuid4().hex


@router.get("/", response_class=HTMLResponse)
def product_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "product_page.html", {"product": load_product().model_dump()})


@router.post("/api/chat")
def chat(body: ChatRequest, session: Db) -> dict:
    trace_id = _trace()
    prospect = prospect_for_session(session, body.session_id)
    orch = build_orchestrator(session)
    reply = orch.dispatch(AgentMessage(
        trace_id=trace_id, sender="orchestrator", recipient="sales", type="chat_turn",
        payload={"session_id": body.session_id, "prospect_id": prospect.id, "message": body.message}))
    p = reply.payload
    return {"reply": p.get("reply", "Sorry, something went wrong."), "source": p.get("source", "system"),
            "interest_level": p.get("interest_level", "none"), "blocked": bool(p.get("blocked", False)),
            "trace_id": trace_id}


@router.post("/api/pay")
def pay(body: PayRequest, session: Db) -> dict:
    trace_id = _trace()
    prospect = prospect_for_session(session, body.session_id)
    orch = build_orchestrator(session)
    reply = orch.dispatch(AgentMessage(
        trace_id=trace_id, sender="orchestrator", recipient="payment", type="payment_request",
        payload={"session_id": body.session_id, "prospect_id": prospect.id, "plan_id": body.plan_id,
                 "test_token": body.test_token}))
    p = reply.payload
    return {"status": p.get("status", "error"), "message": p.get("message", "Payment service unavailable."),
            "payment_id": p.get("payment_id"), "trace_id": trace_id}


@router.post("/api/rating")
def rating(body: RatingRequest, session: Db) -> dict:
    last = session.exec(select(Interaction).where(Interaction.session_id == body.session_id,
                                                  Interaction.role == "agent")
                        .order_by(Interaction.ts.desc(), Interaction.id)).first()
    if last is None:
        prospect = prospect_for_session(session, body.session_id)
        last = Interaction(session_id=body.session_id, prospect_id=prospect.id, role="agent",
                           text="(rating only)", source="system")
    last.rating = body.score
    session.add(last)
    session.commit()
    return {"ok": True}
