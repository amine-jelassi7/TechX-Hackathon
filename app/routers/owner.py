"""Owner API (docs/API_CONTRACT.md section 2). Prototype-grade auth: shared `X-Owner-Token` header."""

import hmac
import json
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from app import audit
from app.agents import insights as insights_agent
from app.agents import payment as payment_agent
from app.config import owner_api_enabled, settings
from app.db import get_session
from app.gateway import SecurityGateway
from app.llm import LLMError
from app.models import (
    ApprovalRequest,
    AuditEvent,
    FeedbackResponse,
    Payment,
    PendingPageChange,
    ProductNotification,
    ProductSuggestion,
    QAEntry,
    SaaSProduct,
    SecurityAlert,
    utcnow,
)
from app.permissions import is_allowed
from app.schemas import Ctx, Plan, Policies, Product
from app.services import analytics, improvements, insights, qa, simulator
from app.services import feedback as feedback_service
from app.services.pdf_parser import PDFParseError, parse_pdf

EVAL_PATH = Path(__file__).resolve().parents[2] / "data" / "eval_results.json"


def require_owner(x_owner_token: str | None = Header(default=None)) -> None:
    if not owner_api_enabled(settings):  # default/empty token outside dev: closed for everyone
        raise HTTPException(status_code=401, detail="owner API disabled: set OWNER_TOKEN")
    if not x_owner_token or not hmac.compare_digest(
        x_owner_token.encode(), settings.owner_token.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid owner token")


Db = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/owner", dependencies=[Depends(require_owner)])
PDF_UPLOAD = File(...)


def _product(row: SaaSProduct) -> dict:
    return {
        "id": row.id,
        "slug": row.slug,
        "owner": row.owner,
        "status": row.status,
        "product": row.product_json,
        "source": row.source,
        "confidence": row.confidence,
        "needs_review": row.needs_review,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class ProductBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: "ProductDraft"


class IntakeConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: "ProductDraft"
    source: Literal["manual", "pdf", "mixed"] = "manual"
    confidence: dict[str, float] = {}
    needs_review: list[str] = []


class ProductDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    name: str = ""
    tagline: str = ""
    description: str = ""
    audience: list[str] = []
    price: float = 0
    currency: str = "TND"
    plans: list[Plan] = []
    features: list[str] = []
    faq_seed: list[str] = []
    policies: Policies = Policies()
    contact: str = ""
    tone: str = "professional"
    website: str = ""
    logo_url: str = ""
    use_cases: list[str] = []
    differentiators: list[str] = []
    integrations: list[str] = []
    tech_notes: str = ""
    trial_policy: str = ""
    support_channels: list[str] = []
    sla: str = ""


def _draft_product(data: dict, ident: str | None = None) -> dict:
    """Normalize incomplete intake into the existing Product shape; publish performs required checks."""
    from app.models import new_id

    allowed = {
        "id",
        "name",
        "tagline",
        "description",
        "audience",
        "price",
        "currency",
        "plans",
        "features",
        "faq_seed",
        "policies",
        "contact",
        "tone",
        "website",
        "logo_url",
        "use_cases",
        "differentiators",
        "integrations",
        "tech_notes",
        "trial_policy",
        "support_channels",
        "sla",
    }
    unknown = set(data) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail={"unknown_fields": sorted(unknown)})
    normalized = {
        "id": ident or new_id(),
        "name": "",
        "tagline": "",
        "description": "",
        "audience": [],
        "price": 0.0,
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
        **data,
    }
    normalized["id"] = ident or normalized["id"]
    return normalized


def _screen_intake(session: Session, data: dict, trace_id: str) -> dict:
    def scan(value):
        if isinstance(value, dict):
            return {key: scan(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scan(item) for item in value]
        if not isinstance(value, str) or not value:
            return value
        decision = SecurityGateway(session).check_input(
            value, Ctx(trace_id=trace_id, actor="intake")
        )
        if decision.action in ("block", "escalate"):
            raise HTTPException(
                status_code=422, detail="intake text was blocked by security checks"
            )
        return decision.sanitized if decision.action == "sanitize" and decision.sanitized else value

    return scan(data)


@router.get("/products")
def products(session: Db) -> list[dict]:
    out = []
    for row in session.exec(select(SaaSProduct).order_by(SaaSProduct.created_at)).all():
        item = _product(row)
        p = row.product_json
        checks = [
            bool(p.get("name")),
            bool(p.get("tagline")),
            bool(p.get("description")),
            bool(p.get("audience")),
            bool(p.get("features")),
            bool(p.get("plans") or p.get("contact")),
            bool(p.get("contact")),
        ]
        item["completeness"] = round(100 * sum(checks) / len(checks))
        feedback_rows = session.exec(
            select(FeedbackResponse).where(FeedbackResponse.product_id == row.id)
        ).all()
        item["average_score"] = (
            round(sum(x.score for x in feedback_rows) / len(feedback_rows), 1)
            if feedback_rows
            else None
        )
        item["has_unpublished_changes"] = row.status == "published" and row.product_json != (
            row.published_json or row.product_json
        )
        out.append(item)
    return out


@router.post("/products")
def create_product(body: ProductBody, session: Db) -> dict:
    import re

    from app.models import new_id

    ident = new_id()
    data = _draft_product(body.product.model_dump(exclude_unset=True), ident)
    data = _screen_intake(session, data, uuid.uuid4().hex)
    slug = re.sub(r"[^a-z0-9]+", "-", data["name"].lower()).strip("-")[:60] or ident[:12]
    if session.exec(select(SaaSProduct).where(SaaSProduct.slug == slug)).first():
        slug = f"{slug}-{ident[:8]}"
    row = SaaSProduct(id=ident, slug=slug, product_json=data)
    session.add(row)
    session.commit()
    session.refresh(row)
    try:
        drafts = qa.generate_qa(Product.model_validate(data))
        qa.save_drafts(session, drafts)
    except LLMError:
        # Intake remains usable in mock mode and when live Q&A generation is unavailable.
        drafts = []
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="product_draft_created",
        decision="allow",
        payload={"product_id": row.id, "slug": row.slug, "faq_drafts": len(drafts)},
    )
    return _product(row)


@router.get("/products/{product_id}")
def product_get(product_id: str, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return _product(row)


@router.put("/products/{product_id}")
def product_update(product_id: str, body: ProductBody, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    data = _draft_product(body.product.model_dump(exclude_unset=True), row.id)
    data = _screen_intake(session, data, uuid.uuid4().hex)
    row.product_json = data
    row.updated_at = utcnow()
    row.needs_review = []
    row.source = "mixed" if row.source == "pdf" else row.source
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="product_draft_saved",
        decision="allow",
        payload={"product_id": row.id},
    )
    return _product(row)


@router.post("/products/{product_id}/confirm")
def product_confirm(product_id: str, body: IntakeConfirm, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    trace = uuid.uuid4().hex
    data = _draft_product(body.product.model_dump(exclude_unset=True), row.id)
    data = _screen_intake(session, data, trace)
    row.product_json = data
    row.source = body.source
    row.confidence = body.confidence
    row.needs_review = body.needs_review
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    audit.log_event(
        session,
        trace_id=trace,
        actor="owner",
        event_type="intake_confirmed",
        decision="allow",
        payload={"product_id": row.id, "source": row.source, "confirmed_fields": list(data)},
    )
    return _product(row)


@router.post("/products/{product_id}/pdf")
async def product_pdf(product_id: str, session: Db, file: UploadFile = PDF_UPLOAD) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    data = await file.read(5 * 1024 * 1024 + 1)
    trace = uuid.uuid4().hex
    import hashlib

    audit.log_event(
        session,
        trace_id=trace,
        actor="intake",
        event_type="pdf_uploaded",
        decision="received",
        payload={
            "product_id": row.id,
            "filename": (file.filename or "document.pdf")[:120],
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        },
    )
    if not is_allowed("intake", "pdf.parse"):
        raise HTTPException(status_code=403, detail="PDF parser not permitted")
    try:
        parsed = parse_pdf(data)
        row.source = "pdf" if row.source == "manual" else "mixed"
        row.confidence = parsed["confidence"]
        row.needs_review = list(parsed["needs_review"])
        session.add(row)
        session.commit()
        safe_fields = {}
        for key, value in parsed["fields"].items():
            decision = SecurityGateway(session).check_input(
                str(value), Ctx(trace_id=trace, actor="intake")
            )
            if decision.action in ("block", "escalate"):
                audit.log_event(
                    session,
                    trace_id=trace,
                    actor="intake",
                    event_type="pdf_rejected",
                    decision="block",
                    payload={"product_id": row.id, "sha256": parsed["sha256"], "field": key},
                )
                raise HTTPException(
                    status_code=422, detail="PDF content blocked by security checks"
                )
            safe_fields[key] = decision.sanitized if decision.action == "sanitize" else value
        parsed["fields"] = safe_fields
        audit.log_event(
            session,
            trace_id=trace,
            actor="intake",
            event_type="pdf_parsed",
            decision="draft",
            payload={
                "product_id": row.id,
                "filename": (file.filename or "document.pdf")[:120],
                "sha256": parsed["sha256"],
                "field_names": list(parsed["fields"]),
            },
        )
        return parsed
    except PDFParseError as exc:
        audit.log_event(
            session,
            trace_id=trace,
            actor="intake",
            event_type="pdf_rejected",
            decision="block",
            payload={"product_id": row.id, "reason": str(exc)[:120]},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/products/{product_id}/preview", response_class=HTMLResponse)
def product_preview(product_id: str, session: Db) -> HTMLResponse:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    from starlette.requests import Request

    from app.routers.public import templates

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/owner/preview",
            "headers": [],
            "query_string": b"",
        }
    )
    faq = session.exec(
        select(QAEntry).where(QAEntry.product_id == row.id, QAEntry.status == "approved")
    ).all()
    return templates.TemplateResponse(
        request,
        "product_page.html",
        {"product": row.product_json, "product_slug": row.slug, "product_id": row.id, "faq": faq},
    )


@router.post("/products/{product_id}/publish")
def product_publish(product_id: str, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    p = row.product_json
    missing = [
        label
        for label, ok in (
            ("name", bool(p.get("name"))),
            ("tagline", bool(p.get("tagline"))),
            ("problem", bool(p.get("description"))),
            ("audience", bool(p.get("audience"))),
            ("feature", bool(p.get("features"))),
            ("pricing", bool(p.get("plans") or p.get("contact"))),
            ("contact", bool(p.get("contact"))),
        )
        if not ok
    ]
    if missing:
        raise HTTPException(status_code=422, detail={"missing": missing})
    if not is_allowed("owner", "product.publish"):
        raise HTTPException(status_code=403, detail="not permitted")
    row.status = "published"
    row.published_at = utcnow()
    row.updated_at = utcnow()
    row.published_json = dict(row.product_json)
    session.add(row)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="page_published",
        decision="allow",
        payload={"product_id": row.id, "slug": row.slug},
    )
    return {"product": _product(row), "url": f"/p/{row.slug}"}


@router.post("/products/{product_id}/unpublish")
def product_unpublish(product_id: str, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    row.status = "draft"
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="page_unpublished",
        decision="allow",
        payload={"product_id": row.id},
    )
    return _product(row)


@router.post("/products/{product_id}/archive")
def product_archive(product_id: str, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    row.status = "archived"
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="product_archived",
        decision="allow",
        payload={"product_id": row.id},
    )
    return _product(row)


@router.get("/products/{product_id}/feedback")
def product_feedback(product_id: str, session: Db) -> dict:
    if not session.get(SaaSProduct, product_id):
        raise HTTPException(status_code=404, detail="not found")
    responses = session.exec(
        select(FeedbackResponse)
        .where(FeedbackResponse.product_id == product_id)
        .order_by(FeedbackResponse.created_at)
    ).all()
    return feedback_service.aggregate(responses)


@router.post("/products/{product_id}/insights/run")
def product_insights_run(product_id: str, session: Db) -> dict:
    row = session.get(SaaSProduct, product_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    result = improvements.run_product_insights(session, row)
    return {
        "ready": result["ready"],
        "response_count": result.get(
            "response_count", result.get("stats", {}).get("response_count", 0)
        ),
        "suggestions": [_suggestion(s) for s in result.get("suggestions", [])],
        "report": result.get("report"),
    }


def _suggestion(item: ProductSuggestion) -> dict:
    return {
        "id": item.id,
        "product_id": item.product_id,
        "type": item.kind,
        "title": item.title,
        "rationale": item.rationale,
        "target_section": item.target_section,
        "before": item.before,
        "after": item.after,
        "priority": item.priority,
        "impact": item.impact,
        "effort": item.effort,
        "evidence": item.evidence,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
    }


@router.get("/products/{product_id}/suggestions")
def product_suggestions(product_id: str, session: Db) -> list[dict]:
    if not session.get(SaaSProduct, product_id):
        raise HTTPException(status_code=404, detail="not found")
    rows = session.exec(
        select(ProductSuggestion)
        .where(ProductSuggestion.product_id == product_id)
        .order_by(ProductSuggestion.created_at.desc())
    ).all()
    return [_suggestion(x) for x in rows]


@router.get("/products/{product_id}/notifications")
def product_notifications(product_id: str, session: Db) -> list[dict]:
    if not session.get(SaaSProduct, product_id):
        raise HTTPException(status_code=404, detail="not found")
    rows = session.exec(
        select(ProductNotification)
        .where(ProductNotification.product_id == product_id)
        .order_by(ProductNotification.created_at.desc())
    ).all()
    return [
        {
            "id": x.id,
            "kind": x.kind,
            "title": x.title,
            "body": x.body,
            "report_path": x.report_path,
            "created_at": x.created_at.isoformat(),
        }
        for x in rows
    ]


@router.get("/notifications/{notification_id}/report")
def notification_report(notification_id: str, session: Db):
    note = session.get(ProductNotification, notification_id)
    if not note:
        raise HTTPException(status_code=404, detail="not found")
    report = (improvements.OUTBOX / f"feedback-{note.product_id}.md").resolve()
    if report.parent != improvements.OUTBOX.resolve() or not report.is_file():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(report, media_type="text/markdown", filename=report.name)


@router.get("/page-changes")
def page_changes(session: Db, product_id: str | None = None) -> list[dict]:
    stmt = select(PendingPageChange).order_by(PendingPageChange.created_at.desc())
    if product_id:
        stmt = stmt.where(PendingPageChange.product_id == product_id)
    return [
        {
            "id": x.id,
            "product_id": x.product_id,
            "suggestion_id": x.suggestion_id,
            "target_section": x.target_section,
            "before": x.before,
            "after": x.after,
            "status": x.status,
            "created_at": x.created_at.isoformat(),
        }
        for x in session.exec(stmt).all()
    ]


@router.post("/suggestions/{suggestion_id}/accept")
def suggestion_accept(suggestion_id: str, session: Db) -> dict:
    item = session.get(ProductSuggestion, suggestion_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    if item.status != "open":
        raise HTTPException(status_code=409, detail="suggestion already handled")
    change = improvements.accept_suggestion(session, item)
    return {"suggestion": _suggestion(item), "pending_change_id": change.id if change else None}


@router.post("/suggestions/{suggestion_id}/dismiss")
def suggestion_dismiss(suggestion_id: str, session: Db) -> dict:
    item = session.get(ProductSuggestion, suggestion_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    item.status = "dismissed"
    session.add(item)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="suggestion_dismissed",
        decision="allow",
        payload={"suggestion_id": item.id},
    )
    return _suggestion(item)


@router.post("/suggestions/{suggestion_id}/done")
def suggestion_done(suggestion_id: str, session: Db) -> dict:
    item = session.get(ProductSuggestion, suggestion_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    item.status = "done"
    session.add(item)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="suggestion_done",
        decision="allow",
        payload={"suggestion_id": item.id},
    )
    return _suggestion(item)


@router.post("/page-changes/{change_id}/approve")
def page_change_approve(change_id: str, session: Db) -> dict:
    item = session.get(PendingPageChange, change_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="page change already decided")
    improvements.approve_page_change(session, item)
    return {
        "id": item.id,
        "status": item.status,
        "product_id": item.product_id,
        "message": "Draft updated. Publish the draft to apply it to the public page.",
    }


# ---- KPIs ------------------------------------------------------------------------------------
@router.get("/kpis")
def kpis(session: Db, product_id: str | None = None) -> dict:
    return analytics.get_kpis(session, product_id=product_id).model_dump(mode="json")


@router.get("/kpis/narrative")
def narrative(session: Db, product_id: str | None = None) -> dict:
    return {"text": analytics.narrative_summary(analytics.get_kpis(session, product_id=product_id))}


@router.get("/kpis/forecast")
def forecast(session: Db, days: int = Query(7, ge=1, le=90), product_id: str | None = None) -> dict:
    fn = getattr(analytics, "forecast_revenue", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="implemented in a later step")
    if product_id:
        # Forecast function accepts a scoped history in this product flow.
        return fn(session, days_ahead=days, product_id=product_id)
    return fn(session, days_ahead=days)


# ---- audit and alerts --------------------------------------------------------------------------
def _audit_row(e: AuditEvent) -> dict:
    return {
        "id": e.id,
        "ts": e.ts.isoformat(),
        "trace_id": e.trace_id,
        "actor": e.actor,
        "event_type": e.event_type,
        "decision": e.decision,
        "reasons": list(e.reasons_json or []),
        "payload": e.payload or {},
    }


@router.get("/audit")
def audit_list(session: Db, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    rows = session.exec(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(limit))
    return [_audit_row(e) for e in rows]


@router.get("/audit/verify")
def audit_verify(session: Db) -> dict:
    return audit.verify_chain(session)


@router.get("/alerts")
def alerts(session: Db, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    rows = session.exec(
        select(SecurityAlert).order_by(SecurityAlert.ts.desc(), SecurityAlert.id).limit(limit)
    )
    return [
        {
            "id": a.id,
            "ts": a.ts.isoformat(),
            "trace_id": a.trace_id,
            "alert_type": a.alert_type,
            "severity": a.severity,
            "detail": a.detail,
        }
        for a in rows
    ]


# ---- Q&A ---------------------------------------------------------------------------------------
class QAUpdate(BaseModel):
    question: str
    answer: str
    variants: list[str] = []


def _qa(e: QAEntry) -> dict:
    return {
        "id": e.id,
        "question": e.question,
        "variants": list(e.variants or []),
        "answer": e.answer,
        "status": e.status,
        "hits": e.hits,
    }


def _or_404(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(status_code=404, detail="not found") from None


@router.get("/qa")
def qa_list(session: Db, product_id: str = "prod_ai_bootcamp") -> list[dict]:
    return [
        _qa(e)
        for e in session.exec(
            select(QAEntry).where(QAEntry.product_id == product_id).order_by(QAEntry.id)
        )
    ]


@router.post("/qa/generate")
def qa_generate(session: Db, product_id: str = "prod_ai_bootcamp") -> list[dict]:
    try:
        row = session.get(SaaSProduct, product_id)
        if row is None and product_id == "prod_ai_bootcamp":
            from app.llm import load_product

            product = load_product()
        elif row is None:
            raise HTTPException(status_code=404, detail="not found")
        else:
            product = Product.model_validate(row.product_json)
        drafts = qa.generate_qa(product)
    except LLMError as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM unavailable, try again later ({exc})"
        ) from None
    return [_qa(e) for e in qa.save_drafts(session, drafts)]


@router.post("/qa/{entry_id}/approve")
def qa_approve(entry_id: str, session: Db) -> dict:
    entry = _or_404(qa.approve, session, entry_id)
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="qa_approved",
        decision="approved",
        payload={"qa_id": entry.id, "question": entry.question},
    )
    return _qa(entry)


@router.post("/qa/{entry_id}/reject")
def qa_reject(entry_id: str, session: Db) -> dict:
    entry = _or_404(qa.reject, session, entry_id)
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="qa_rejected",
        decision="rejected",
        payload={"qa_id": entry.id, "question": entry.question},
    )
    return _qa(entry)


@router.put("/qa/{entry_id}")
def qa_update(entry_id: str, body: QAUpdate, session: Db) -> dict:
    entry = _or_404(qa.update, session, entry_id, body.question, body.answer, body.variants)
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="qa_updated",
        decision="draft",
        payload={"qa_id": entry.id, "question": entry.question},
    )
    return _qa(entry)


# ---- approvals ---------------------------------------------------------------------------------
def _payment(p: Payment | None) -> dict | None:
    if p is None:
        return None
    return {
        "id": p.id,
        "prospect_id": p.prospect_id,
        "plan_id": p.plan_id,
        "amount": p.amount,
        "currency": p.currency,
        "risk_score": p.risk_score,
        "status": p.status,
    }


def _approval(a: ApprovalRequest, session: Session) -> dict:
    pay = session.get(Payment, a.ref_id) if a.kind == "payment" and a.ref_id else None
    return {
        "id": a.id,
        "kind": a.kind,
        "ref_id": a.ref_id,
        "reasons": list(a.reasons or []),
        "status": a.status,
        "created_at": a.created_at.isoformat(),
        "payment": _payment(pay),
    }


@router.get("/approvals")
def approvals(session: Db, status: str = "pending", product_id: str | None = None) -> list[dict]:
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id)
    if status != "all":
        stmt = stmt.where(ApprovalRequest.status == status)
    if product_id:
        stmt = stmt.where(ApprovalRequest.product_id == product_id)
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
