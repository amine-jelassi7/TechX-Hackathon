"""Public endpoints (docs/API_CONTRACT.md section 3). No card fields anywhere; amount comes from product.json."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import audit
from app.db import get_session
from app.gateway import SecurityGateway
from app.llm import load_product
from app.models import FeedbackResponse, Interaction, Payment, QAEntry, SaaSProduct
from app.orchestrator import build_orchestrator
from app.permissions import is_allowed
from app.schemas import AgentMessage, ChatRequest, Ctx, FitQuizAnswers, PayRequest, RatingRequest
from app.services import feedback as feedback_service
from app.services import recommender
from app.services.improvements import run_product_insights
from app.services.prospects import prospect_for_session

Db = Annotated[Session, Depends(get_session)]

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _trace() -> str:
    return uuid.uuid4().hex


def _product_for_id(session: Session, product_id: str | None):
    if not product_id or product_id == load_product().id:
        row = session.get(SaaSProduct, product_id or load_product().id)
        if row:
            if row.status != "published":
                raise HTTPException(status_code=404, detail="not found")
            from app.schemas import Product

            return Product.model_validate(row.published_json or row.product_json)
        return load_product()
    row = session.get(SaaSProduct, product_id)
    if row is None or row.status != "published":
        raise HTTPException(status_code=404, detail="not found")
    from app.schemas import Product

    return Product.model_validate(row.published_json or row.product_json)


@router.get("/", response_class=HTMLResponse)
def product_page(request: Request, session: Db) -> HTMLResponse:
    product = load_product()
    row = session.get(SaaSProduct, product.id)
    if row and row.status != "published":
        raise HTTPException(status_code=404, detail="not found")
    view = (row.published_json or row.product_json) if row else product.model_dump()
    faq = session.exec(
        select(QAEntry).where(QAEntry.product_id == product.id, QAEntry.status == "approved")
    ).all()
    return templates.TemplateResponse(
        request,
        "product_page.html",
        {
            "product": view,
            "faq": faq,
            "product_id": product.id,
            "product_slug": row.slug if row else None,
        },
    )


@router.get("/discover", response_class=HTMLResponse)
def discover_page(request: Request, session: Db) -> HTMLResponse:
    products = session.exec(select(SaaSProduct).where(SaaSProduct.status == "published")).all()
    if len(products) == 1:
        return RedirectResponse(f"/p/{products[0].slug}", status_code=302)
    if not products:
        return HTMLResponse("<main><h1>No published products</h1></main>")
    import html

    links = "".join(
        f'<li><a href="/p/{p.slug}">{html.escape((p.published_json or p.product_json).get("name", p.slug))}</a></li>'
        for p in products
    )
    return HTMLResponse(
        f"<!doctype html><title>Products</title><main><h1>Products</h1><ul>{links}</ul></main>"
    )


@router.get("/p/{slug}", response_class=HTMLResponse)
def product_by_slug(slug: str, request: Request, session: Db) -> HTMLResponse:
    row = session.exec(
        select(SaaSProduct).where(SaaSProduct.slug == slug, SaaSProduct.status == "published")
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    faq = session.exec(
        select(QAEntry).where(QAEntry.product_id == row.id, QAEntry.status == "approved")
    ).all()
    return templates.TemplateResponse(
        request,
        "product_page.html",
        {
            "product": row.published_json or row.product_json,
            "product_slug": row.slug,
            "product_id": row.id,
            "faq": faq,
        },
    )


@router.get("/api/feedback/questions")
def feedback_questions() -> dict:
    return feedback_service.QUESTION_BANK


@router.post("/api/feedback/{slug}")
def submit_feedback(
    slug: str, body: feedback_service.FeedbackInput, request: Request, session: Db
) -> dict:
    row = session.exec(
        select(SaaSProduct).where(SaaSProduct.slug == slug, SaaSProduct.status == "published")
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if not is_allowed("feedback", "feedback.write"):
        raise HTTPException(status_code=403, detail="feedback handler not permitted")
    ip_hash = hashlib.sha256(
        (request.client.host if request.client else "unknown").encode()
    ).hexdigest()
    recent = session.exec(
        select(FeedbackResponse).where(
            FeedbackResponse.ip_hash == ip_hash,
            FeedbackResponse.created_at
            >= datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24),
        )
    ).all()
    if len(recent) >= 20:
        raise HTTPException(status_code=429, detail="feedback rate limit reached")
    exists = session.exec(
        select(FeedbackResponse).where(
            FeedbackResponse.product_id == row.id, FeedbackResponse.session_id == body.session_id
        )
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="feedback already submitted")
    trace_id = _trace()
    decision = (
        SecurityGateway(session).check_input(
            body.review, Ctx(trace_id=trace_id, actor="feedback", session_id=body.session_id)
        )
        if body.review
        else None
    )
    if decision and decision.action == "block":
        raise HTTPException(status_code=422, detail="review was blocked by security checks")
    try:
        score, dimensions, nps = feedback_service.score_feedback(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    prospect_id = "pr_" + hashlib.sha256(body.session_id.encode("utf-8")).hexdigest()[:12]
    chatted = session.exec(
        select(Interaction).where(
            Interaction.session_id == body.session_id, Interaction.product_id == row.id
        )
    ).first()
    paid = session.exec(
        select(Payment).where(Payment.prospect_id == prospect_id, Payment.product_id == row.id)
    ).first()
    verified = bool(chatted or paid)
    review = (decision.sanitized if decision and decision.action == "sanitize" else body.review)[
        :2000
    ]
    themes = [
        word
        for word in ("pricing", "support", "slow", "bug", "easy", "feature")
        if word in review.lower()
    ]
    item = FeedbackResponse(
        product_id=row.id,
        session_id=body.session_id,
        ip_hash=ip_hash,
        answers={
            k: v
            for k, v in body.model_dump().items()
            if k not in ("session_id", "review") and v is not None
        },
        score=score,
        dimensions=dimensions,
        nps_category=nps,
        review_redacted=review,
        themes=themes,
        verified_interaction=verified,
    )
    session.add(item)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="feedback already submitted") from None
    session.refresh(item)
    audit.log_event(
        session,
        trace_id=trace_id,
        actor="feedback",
        event_type="feedback_submitted",
        decision="allow",
        payload={
            "product_id": row.id,
            "response_id": item.id,
            "score": score,
            "verified_interaction": verified,
        },
    )
    responses = session.exec(
        select(FeedbackResponse).where(FeedbackResponse.product_id == row.id)
    ).all()
    if len(responses) >= 5:
        run_product_insights(session, row)
    return {
        "ok": True,
        "response_id": item.id,
        "score": score,
        "label": feedback_service.score_label(score),
        "dimensions": dimensions,
        "nps_category": nps,
    }


@router.post("/api/recommend")
def recommend(body: FitQuizAnswers, session: Db) -> dict:
    trace_id = _trace()
    results = recommender.recommend(body)
    audit.log_event(
        session,
        trace_id=trace_id,
        actor="discovery",
        event_type="product_recommended",
        decision="allow",
        payload={"answers": body.model_dump(), "top": [r.id for r in results]},
    )
    return {"trace_id": trace_id, "results": [r.model_dump() for r in results]}


@router.post("/api/chat")
def chat(body: ChatRequest, session: Db) -> dict:
    trace_id = _trace()
    prospect = prospect_for_session(session, body.session_id)
    product = _product_for_id(session, body.product_id)
    orch = build_orchestrator(session, product=product)
    reply = orch.dispatch(
        AgentMessage(
            trace_id=trace_id,
            sender="orchestrator",
            recipient="sales",
            type="chat_turn",
            payload={
                "session_id": body.session_id,
                "prospect_id": prospect.id,
                "message": body.message,
                "product_id": product.id,
            },
        )
    )
    p = reply.payload
    return {
        "reply": p.get("reply", "Sorry, something went wrong."),
        "source": p.get("source", "system"),
        "interest_level": p.get("interest_level", "none"),
        "blocked": bool(p.get("blocked", False)),
        "trace_id": trace_id,
    }


@router.post("/api/pay")
def pay(body: PayRequest, session: Db) -> dict:
    trace_id = _trace()
    prospect = prospect_for_session(session, body.session_id)
    product = _product_for_id(session, body.product_id)
    orch = build_orchestrator(session, product=product)
    reply = orch.dispatch(
        AgentMessage(
            trace_id=trace_id,
            sender="orchestrator",
            recipient="payment",
            type="payment_request",
            payload={
                "session_id": body.session_id,
                "prospect_id": prospect.id,
                "plan_id": body.plan_id,
                "test_token": body.test_token,
                "product_id": product.id,
            },
        )
    )
    p = reply.payload
    return {
        "status": p.get("status", "error"),
        "message": p.get("message", "Payment service unavailable."),
        "payment_id": p.get("payment_id"),
        "trace_id": trace_id,
    }


@router.post("/api/rating")
def rating(body: RatingRequest, session: Db) -> dict:
    last = session.exec(
        select(Interaction)
        .where(Interaction.session_id == body.session_id, Interaction.role == "agent")
        .order_by(Interaction.ts.desc(), Interaction.id)
    ).first()
    if last is None:
        prospect = prospect_for_session(session, body.session_id)
        last = Interaction(
            session_id=body.session_id,
            prospect_id=prospect.id,
            role="agent",
            text="(rating only)",
            source="system",
        )
    last.rating = body.score
    session.add(last)
    session.commit()
    return {"ok": True}
