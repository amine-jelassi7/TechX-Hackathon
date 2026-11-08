"""SQLModel tables (shared contract for all phases)."""

import uuid
from datetime import UTC, datetime

from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id() -> str:
    """Random id. Underscores every 8 hex chars keep digit runs under 13, so an id can never look like
    a card number to the gateway's Luhn filter (plain uuid hex did, ~0.2% of the time)."""
    h = uuid.uuid4().hex
    return "_".join(h[i : i + 8] for i in range(0, 32, 8))


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Prospect(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    email: str
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Interaction(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(default="prod_ai_bootcamp", index=True)
    session_id: str = Field(index=True)
    prospect_id: str | None = Field(default=None, index=True)
    role: str  # prospect | agent
    text: str
    source: str = "qa"  # qa | llm_fallback | handoff | blocked | system
    interest_level: str = "none"  # none | low | medium | high
    objection: str | None = None  # price | schedule | prerequisites | trust | certificate
    rating: int | None = None  # 1..5
    trace_id: str | None = Field(default=None, index=True)
    ts: NaiveDatetime = Field(default_factory=utcnow)


class QAEntry(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(default="prod_ai_bootcamp", index=True)
    question: str
    variants: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    answer: str
    status: str = "draft"  # draft | approved | rejected
    hits: int = 0


class Payment(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(default="prod_ai_bootcamp", index=True)
    prospect_id: str = Field(index=True)
    plan_id: str
    amount: float
    currency: str = "TND"
    # created | succeeded | declined | pending | held_for_approval | approved | rejected
    status: str = "created"
    test_token: str | None = None
    risk_score: float = 0.0
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    trace_id: str | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ApprovalRequest(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(default="prod_ai_bootcamp", index=True)
    kind: str  # payment | escalation | draft
    ref_id: str | None = None  # payment id etc.
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "pending"  # pending | approved | rejected
    trace_id: str | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    decided_at: NaiveDatetime | None = None


class Lead(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    first_name: str
    email: str
    interest: str
    source: str
    consent: bool = False


class OutreachDraft(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    lead_id: str = Field(index=True)
    score: float = 0.0
    reason: str = ""
    draft_message: str = ""
    status: str = "draft"  # draft | sent_manually
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Recommendation(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    title: str
    action: str
    rationale: str
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    impact: str = "medium"  # low | medium | high
    effort: str = "medium"
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    ts: NaiveDatetime = Field(default_factory=utcnow)
    trace_id: str | None = Field(default=None, index=True)
    actor: str
    event_type: str
    decision: str | None = None
    reasons_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    seq: int = Field(default=0, index=True, unique=True)  # deterministic chain order (app/audit.py)
    payload: dict = Field(
        default_factory=dict, sa_column=Column(JSON)
    )  # redacted; part of the hash
    payload_hash: str = ""
    prev_hash: str = ""
    hash: str = ""


class SecurityAlert(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    ts: NaiveDatetime = Field(default_factory=utcnow)
    trace_id: str | None = None
    alert_type: str  # injection | card_data | permission_denied | fraud | ...
    severity: str = "medium"  # low | medium | high
    detail: str = ""


class SaaSProduct(SQLModel, table=True):
    """Owner-managed product; the JSON payload preserves the existing Product contract."""

    id: str = Field(default_factory=new_id, primary_key=True)
    slug: str = Field(index=True, unique=True)
    owner: str = "owner"
    status: str = "draft"  # draft | published | archived
    product_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    published_json: dict = Field(default_factory=dict, sa_column=Column(JSON))
    source: str = "manual"  # manual | pdf | mixed
    confidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    needs_review: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)
    published_at: NaiveDatetime | None = None


class FeedbackResponse(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("product_id", "session_id", name="uq_feedback_product_session"),
    )
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(index=True)
    session_id: str = Field(index=True)
    ip_hash: str = Field(default="", index=True)
    answers: dict = Field(default_factory=dict, sa_column=Column(JSON))
    score: float
    dimensions: dict = Field(default_factory=dict, sa_column=Column(JSON))
    nps_category: str = "passive"
    review_redacted: str = ""
    themes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    verified_interaction: bool = False
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ProductNotification(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(index=True)
    kind: str
    title: str
    body: str = ""
    report_path: str = ""
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class ProductSuggestion(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(index=True)
    kind: str
    title: str
    rationale: str
    target_section: str
    before: str = ""
    after: str = ""
    priority: str = "medium"
    impact: str = "medium"
    effort: str = "medium"
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "open"  # open | accepted | dismissed | done
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class PendingPageChange(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    product_id: str = Field(index=True)
    suggestion_id: str = Field(index=True)
    target_section: str
    before: str = ""
    after: str = ""
    status: str = "pending"  # pending | approved | rejected
    created_at: NaiveDatetime = Field(default_factory=utcnow)
