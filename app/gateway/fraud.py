"""Deterministic, explainable payment fraud rules (spec 7.3). Additive score, capped at 1.0."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import settings
from app.models import Payment, Prospect, utcnow
from app.schemas import Product


@dataclass
class FraudResult:
    score: float = 0.0
    level: str = "low"  # low | medium | high
    reasons: list[str] = field(default_factory=list)
    mismatch: bool = False  # plan/amount does not match product.json -> caller must block


def level_for(score: float) -> str:
    if score >= settings.fraud_high_threshold:
        return "high"
    if score >= settings.fraud_medium_threshold:
        return "medium"
    return "low"


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def score_payment(
    session: Session,
    *,
    prospect_id: str,
    plan_id: str,
    amount: float,
    product: Product,
    currency: str | None = None,
    exclude_payment_id: str | None = None,
    now: datetime | None = None,
) -> FraudResult:
    """Score one payment request against the rules and the DB history.

    `exclude_payment_id`: pass the id of the current Payment row if it already exists, so it does not
    count as one of its own previous attempts. Without it the request is counted as the newest attempt.
    """
    now = now or utcnow()
    score, reasons = 0.0, []

    plan = next((p for p in product.plans if p.id == plan_id), None)
    mismatch = plan is None or abs(plan.price - amount) > 0.005 or (
        currency is not None and currency != product.currency)
    if mismatch:
        score += settings.fraud_w_mismatch
        reasons.append("plan_amount_mismatch")

    if amount >= settings.payment_approval_threshold:
        score += settings.fraud_w_amount
        reasons.append("amount_over_threshold")

    history = list(session.exec(select(Payment).where(Payment.prospect_id == prospect_id)).all())
    history = sorted((p for p in history if p.id != exclude_payment_id), key=lambda p: p.created_at)

    window_start = now - timedelta(minutes=settings.fraud_burst_window_minutes)
    recent = [p for p in history if p.created_at >= window_start]
    if len(recent) + 1 > settings.fraud_burst_max_attempts:
        score += settings.fraud_w_burst
        reasons.append("burst_attempts")

    streak = 0
    for p in reversed(history):
        if p.status != "declined":
            break
        streak += 1
    if streak >= settings.fraud_declined_before_new:
        score += settings.fraud_w_declined_then_new
        reasons.append("declined_then_new_attempt")

    prospect = session.get(Prospect, prospect_id)
    if prospect is not None:
        if _domain(prospect.email) in {d.lower() for d in settings.disposable_domains}:
            score += settings.fraud_w_disposable_email
            reasons.append("disposable_email_domain")
        twins = session.exec(select(Prospect).where(Prospect.id != prospect_id)).all()
        if any(t.email.strip().lower() == prospect.email.strip().lower() for t in twins):
            score += settings.fraud_w_same_email_multi_prospect
            reasons.append("same_email_multiple_prospects")

    score = round(min(score, 1.0), 4)
    return FraudResult(score=score, level=level_for(score), reasons=reasons, mismatch=mismatch)
