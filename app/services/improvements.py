"""Evidence-bound product improvement suggestions and local owner reports."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session, select

from app import audit
from app.gateway import SecurityGateway
from app.models import (
    FeedbackResponse,
    PendingPageChange,
    ProductNotification,
    ProductSuggestion,
    SaaSProduct,
    utcnow,
)
from app.schemas import Ctx, Product, ProductImprovementSuggestion
from app.services.feedback import aggregate

OUTBOX = Path(__file__).resolve().parents[2] / "data" / "outbox"


def run_product_insights(session: Session, product: SaaSProduct) -> dict:
    responses = session.exec(
        select(FeedbackResponse).where(FeedbackResponse.product_id == product.id)
    ).all()
    stats = aggregate(responses)
    if not stats["ready"]:
        return {"ready": False, "response_count": stats["response_count"], "suggestions": []}
    suggestions = []
    for dimension, score in stats["dimensions"].items():
        if score >= 70:
            continue
        mapping = {
            "ease": (
                "onboarding",
                "Clarify the first steps",
                "onboarding",
                "Add a short first-session checklist.",
            ),
            "feature_fit": (
                "feature",
                "Clarify feature fit",
                "features",
                "Explain which customer need each feature addresses.",
            ),
            "value": (
                "pricing",
                "Explain plan value",
                "plans",
                "Show the main outcome included in each plan.",
            ),
            "reliability": (
                "page_copy",
                "Add reliability details",
                "description",
                "Describe the available reliability and status information.",
            ),
            "support": (
                "support",
                "Make support expectations clear",
                "contact",
                "State support hours and expected response time.",
            ),
            "satisfaction": (
                "page_copy",
                "Address overall satisfaction",
                "description",
                "Clarify the expected customer outcome and service scope.",
            ),
            "recommend": (
                "page_copy",
                "Strengthen the recommendation story",
                "tagline",
                "Make the primary benefit more concrete.",
            ),
        }
        kind, title, target, after = mapping[dimension]
        existing = session.exec(
            select(ProductSuggestion).where(
                ProductSuggestion.product_id == product.id,
                ProductSuggestion.title == title,
                ProductSuggestion.status == "open",
            )
        ).first()
        if existing:
            continue
        before = str(product.product_json.get(target, ""))[:500]
        evidence = {
            "dimension_scores": {dimension: score},
            "theme_counts": stats["themes"],
            "response_ids": [r.id for r in responses],
        }
        try:
            validated = ProductImprovementSuggestion(
                id=f"suggestion_{product.id}_{dimension}",
                type=kind,
                title=title,
                rationale=f"The {dimension} score averaged {score}/100 across {len(responses)} responses.",
                target_section=target,
                before=before,
                after=after,
                priority="high" if score < 50 else "medium",
                impact="medium",
                effort="low",
                evidence=evidence,
            )
        except ValidationError:
            audit.log_event(
                session,
                trace_id=None,
                actor="insights",
                event_type="suggestion_dropped",
                decision="block",
                payload={
                    "product_id": product.id,
                    "dimension": dimension,
                    "reason": "schema_invalid",
                },
            )
            continue
        suggestion_text = (
            f"{validated.title} {validated.rationale} {validated.before} {validated.after}"
        )
        decision = SecurityGateway(
            session, Product.model_validate(product.product_json)
        ).check_output(suggestion_text, Ctx(trace_id=f"improve_{product.id}", actor="insights"))
        if decision.action != "allow" or not evidence["response_ids"]:
            audit.log_event(
                session,
                trace_id=None,
                actor="insights",
                event_type="suggestion_dropped",
                decision="block",
                payload={"product_id": product.id, "dimension": dimension},
            )
            continue
        item = ProductSuggestion(
            product_id=product.id,
            kind=validated.type,
            title=validated.title,
            rationale=validated.rationale,
            target_section=validated.target_section,
            before=validated.before,
            after=validated.after,
            priority=validated.priority,
            impact=validated.impact,
            effort=validated.effort,
            evidence=validated.evidence.model_dump(),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        suggestions.append(item)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    report = [
        f"# Customer feedback report: {product.product_json.get('name', product.slug)}",
        "",
        f"Responses: {stats['response_count']}",
        f"Average score: {stats['average']}/100",
        f"NPS: {stats['nps']}",
        "",
        "## Dimension scores",
    ]
    report += [f"- {key}: {value}/100" for key, value in stats["dimensions"].items()]
    report += ["", "## Suggested modifications"]
    report += [f"- {item.title}: {item.rationale}" for item in suggestions]
    report_path = OUTBOX / f"feedback-{product.id}.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    note = ProductNotification(
        product_id=product.id,
        kind="feedback_report",
        title="New feedback score report",
        body=f"Average {stats['average']}/100 from {stats['response_count']} responses.",
        report_path=str(report_path.relative_to(OUTBOX.parents[0])),
    )
    session.add(note)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="insights",
        event_type="product_insights_delivered",
        decision="allow",
        payload={
            "product_id": product.id,
            "response_count": stats["response_count"],
            "suggestion_ids": [s.id for s in suggestions],
            "report": note.report_path,
        },
    )
    return {"ready": True, "stats": stats, "suggestions": suggestions, "report": note.report_path}


def accept_suggestion(session: Session, suggestion: ProductSuggestion) -> PendingPageChange | None:
    suggestion.status = "accepted"
    session.add(suggestion)
    change = None
    if suggestion.kind in ("page_copy", "faq_add"):
        change = PendingPageChange(
            product_id=suggestion.product_id,
            suggestion_id=suggestion.id,
            target_section=suggestion.target_section,
            before=suggestion.before,
            after=suggestion.after,
        )
        session.add(change)
    else:
        suggestion.status = "accepted"  # tracked owner task
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="suggestion_accepted",
        decision="allow",
        payload={
            "suggestion_id": suggestion.id,
            "pending_change_id": change.id if change else None,
        },
    )
    return change


def approve_page_change(session: Session, change: PendingPageChange) -> None:
    product = session.get(SaaSProduct, change.product_id)
    if product is None:
        raise KeyError(change.product_id)
    data = dict(product.product_json)
    if change.target_section == "faq_seed":
        data["faq_seed"] = list(data.get("faq_seed", [])) + [change.after]
    else:
        data[change.target_section] = change.after
    product.product_json = data
    product.updated_at = utcnow()
    change.status = "approved"
    session.add(product)
    session.add(change)
    session.commit()
    audit.log_event(
        session,
        trace_id=None,
        actor="owner",
        event_type="page_change_approved",
        decision="allow",
        payload={"product_id": product.id, "change_id": change.id, "target": change.target_section},
    )
