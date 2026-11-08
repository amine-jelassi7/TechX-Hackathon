"""Deterministic KPIs (spec 6.4). No LLM in get_kpis; narrative_summary is display-only."""

from collections import Counter, defaultdict

from sqlmodel import Session, select

from app.llm import chat
from app.models import FeedbackResponse, Interaction, Payment, Recommendation, SecurityAlert
from app.schemas import KPIs, Objection

INTERESTED_LEVELS = ("medium", "high")
TOP_OBJECTIONS = 5
EXAMPLES_PER_OBJECTION = 3


def _prospect_interactions(session: Session, product_id: str | None = None) -> list[Interaction]:
    stmt = (
        select(Interaction)
        .where(Interaction.role == "prospect")
        .order_by(Interaction.ts, Interaction.id)
    )
    if product_id:
        stmt = stmt.where(Interaction.product_id == product_id)
    return list(session.exec(stmt))


def _who(i: Interaction) -> str:
    """Identity of a prospect: prospect_id when known, else the anonymous session."""
    return i.prospect_id or f"session:{i.session_id}"


def _interested_ids(rows: list[Interaction]) -> set[str]:
    """Interested = distinct prospects (or anonymous sessions) with >=1 prospect message
    whose interest_level is medium or high."""
    return {_who(i) for i in rows if i.interest_level in INTERESTED_LEVELS}


def _paid_ids(session: Session, product_id: str | None = None) -> set[str]:
    stmt = select(Payment).where(Payment.status == "succeeded")
    if product_id:
        stmt = stmt.where(Payment.product_id == product_id)
    return {p.prospect_id for p in session.exec(stmt)}


def get_kpis(session: Session, product_id: str | None = None) -> KPIs:
    rows = _prospect_interactions(session, product_id)
    interested = _interested_ids(rows)
    paid = _paid_ids(session, product_id)
    converted = paid & interested  # a payer who never showed interest must not inflate the rate
    conversion = len(converted) / len(interested) if interested else 0.0

    obj_ids: dict[str, list[str]] = defaultdict(list)
    for i in rows:
        if i.objection:
            obj_ids[i.objection].append(i.id)
    ranked = sorted(obj_ids.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_OBJECTIONS]
    objections = [
        Objection(label=k, count=len(v), example_interaction_ids=v[:EXAMPLES_PER_OBJECTION])
        for k, v in ranked
    ]

    payment_stmt = select(Payment)
    if product_id:
        payment_stmt = payment_stmt.where(Payment.product_id == product_id)
    payments = list(session.exec(payment_stmt))
    by_status = dict(Counter(p.status for p in payments))
    revenue = sum(p.amount for p in payments if p.status == "succeeded")

    rating_stmt = select(Interaction)
    if product_id:
        rating_stmt = rating_stmt.where(Interaction.product_id == product_id)
    feedback_stmt = select(FeedbackResponse)
    if product_id:
        feedback_stmt = feedback_stmt.where(FeedbackResponse.product_id == product_id)
    feedback_scores = [r.score / 20 for r in session.exec(feedback_stmt)]
    ratings = [i.rating for i in session.exec(rating_stmt) if i.rating is not None]
    sat = (
        sum(feedback_scores) / len(feedback_scores)
        if feedback_scores
        else sum(ratings) / len(ratings)
        if ratings
        else None
    )

    alerts = list(session.exec(select(SecurityAlert)))
    recs = session.exec(
        select(Recommendation)
        .order_by(Recommendation.created_at.desc(), Recommendation.id)
        .limit(5)
    )
    return KPIs(
        interactions_count=len(rows),
        interested_prospects=len(interested),
        paid_prospects=len(paid),
        converted_prospects=len(converted),
        conversion_rate=conversion,
        top_objections=objections,
        payments_by_status=by_status,
        simulated_revenue=float(revenue),
        satisfaction_avg=sat,
        security_alerts_count=len(alerts),
        security_alerts_by_type=dict(Counter(a.alert_type for a in alerts)),
        latest_recommendations=[
            {
                "id": r.id,
                "title": r.title,
                "action": r.action,
                "rationale": r.rationale,
                "evidence": r.evidence,
                "impact": r.impact,
                "effort": r.effort,
                "created_at": r.created_at.isoformat(),
            }
            for r in recs
        ],
    )


def revenue_over_time(session: Session, product_id: str | None = None) -> list[dict]:
    """Succeeded revenue per day, ascending: [{date: 'YYYY-MM-DD', revenue: float}]."""
    per_day: dict[str, float] = defaultdict(float)
    stmt = select(Payment).where(Payment.status == "succeeded")
    if product_id:
        stmt = stmt.where(Payment.product_id == product_id)
    for p in session.exec(stmt):
        per_day[p.created_at.date().isoformat()] += p.amount
    return [{"date": d, "revenue": float(per_day[d])} for d in sorted(per_day)]


def funnel(session: Session) -> dict[str, int]:
    rows = _prospect_interactions(session)
    return {
        "interactions": len(rows),
        "interested": len(_interested_ids(rows)),
        "payment_attempts": len({p.prospect_id for p in session.exec(select(Payment))}),
        "paid": len(_paid_ids(session)),
    }


def narrative_summary(kpis: KPIs) -> str:
    def mock() -> str:
        top = kpis.top_objections[0] if kpis.top_objections else None
        label = top.label if isinstance(top, Objection) else (top or {}).get("label")
        obj = f"The most common objection is {label}." if label else "No objections were recorded."
        return (
            f"There were {kpis.interactions_count} prospect messages, with "
            f"{kpis.interested_prospects} interested prospects and {kpis.paid_prospects} paid "
            f"(conversion {kpis.conversion_rate:.0%}). {obj} Simulated revenue is "
            f"{kpis.simulated_revenue:g} with {kpis.security_alerts_count} security alerts."
        )

    return chat(
        "Write exactly 3 sentences summarising these KPIs for a business owner. Use only the numbers given.",
        kpis.model_dump_json(),
        task="kpi_narrative",
        mock=mock,
    )


def forecast_revenue(session: Session, days_ahead: int = 7, product_id: str | None = None) -> dict:
    """Linear-trend revenue forecast from daily succeeded revenue (least squares, no LLM).

    Returns {history, forecast: [{date, revenue}], method, r2}. Forecast is clamped at >= 0.
    """
    from datetime import date, timedelta

    history = revenue_over_time(session, product_id=product_id)
    if len(history) < 3 or days_ahead < 1:
        return {"history": history, "forecast": [], "method": "insufficient_data", "r2": 0.0}
    first = date.fromisoformat(history[0]["date"])
    xs = [(date.fromisoformat(h["date"]) - first).days for h in history]
    ys = [h["revenue"] for h in history]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    last = date.fromisoformat(history[-1]["date"])
    forecast = []
    for k in range(1, days_ahead + 1):
        d = last + timedelta(days=k)
        value = intercept + slope * (d - first).days
        forecast.append({"date": d.isoformat(), "revenue": round(max(0.0, value), 2)})
    return {"history": history, "forecast": forecast, "method": "linear_trend", "r2": round(r2, 4)}
