"""Pydantic schemas: product, agent messages, gateway decisions, insights, KPIs."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---- Product (spec 5) ----
class Plan(BaseModel):
    id: str
    name: str
    price: float
    currency: str | None = None
    billing_period: str | None = None
    included_features: list[str] = Field(default_factory=list)


class Policies(BaseModel):
    model_config = ConfigDict(extra="allow")
    refund: str = ""
    schedule: str = ""
    prerequisites: str = ""
    certificate: str = ""


class Product(BaseModel):
    id: str
    name: str
    tagline: str
    description: str
    audience: list[str]
    price: float
    currency: str
    plans: list[Plan]
    features: list[str]
    faq_seed: list[str]
    policies: Policies
    contact: str
    tone: str
    website: str = ""
    logo_url: str = ""
    use_cases: list[str] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    tech_notes: str = ""
    trial_policy: str = ""
    support_channels: list[str] = Field(default_factory=list)
    sla: str = ""


# ---- Product discovery (public fit quiz) ----
class MatchProfile(BaseModel):
    goal: list[str] = Field(default_factory=list)
    level: list[str] = Field(default_factory=list)
    budget: list[str] = Field(default_factory=list)
    format: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    pace: list[str] = Field(default_factory=list)


class CatalogProduct(Product):
    match: MatchProfile


class ProductCatalog(BaseModel):
    products: list[CatalogProduct]


class FitQuizAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: Literal["build-ai-products", "analyze-data", "automate-work", "train-team"]
    level: Literal["beginner", "some-code", "developer"]
    budget: Literal["low", "medium", "high"]
    format: Literal["live-cohort", "self-paced", "workshop"]
    audience: Literal["individual", "student", "startup", "enterprise"]
    pace: Literal["full-time", "part-time", "weekend"]


class ProductMatch(BaseModel):
    id: str
    name: str
    tagline: str
    description: str
    audience: list[str]
    price: float
    currency: str
    plans: list[Plan]
    features: list[str]
    contact: str
    fit: int
    band: str
    reasons: list[str]


# ---- Agent envelope (6.1) ----
class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str
    sender: str  # orchestrator | sales | payment | insights | gateway
    recipient: str
    type: str  # chat_turn | interest_recorded | payment_request | payment_result | ...
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=_now)


# ---- Gateway (7.1) ----
class Ctx(BaseModel):
    trace_id: str
    actor: str
    session_id: str | None = None
    prospect_id: str | None = None


class Decision(BaseModel):
    action: Literal["allow", "sanitize", "block", "escalate"]
    reasons: list[str] = Field(default_factory=list)
    risk: float = Field(default=0.0, ge=0.0, le=1.0)
    sanitized: str | None = None


# ---- Public / owner request schemas (extra fields rejected) ----
# session_id is stored in the DB: only a plain id is accepted (a card number pasted there would be kept raw).
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
TEST_TOKEN_PATTERN = r"^tok_[a-z0-9_]{1,32}$"


def _no_card_number(value: str) -> str:
    from app.gateway.filters import find_card_spans  # lazy: the gateway imports this module

    if find_card_spans(value):  # 16 digits are a legal id by the pattern but could be a pasted card
        raise ValueError("session_id must not look like a card number")
    return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, Field(pattern=SESSION_ID_PATTERN), AfterValidator(_no_card_number)]
    message: str
    product_id: str | None = Field(default="prod_ai_bootcamp", max_length=64)


class PayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, Field(pattern=SESSION_ID_PATTERN), AfterValidator(_no_card_number)]
    plan_id: str
    test_token: str = Field(pattern=TEST_TOKEN_PATTERN)  # sandbox tokens only, never a card number
    product_id: str | None = Field(default="prod_ai_bootcamp", max_length=64)


class RatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: Annotated[str, Field(pattern=SESSION_ID_PATTERN), AfterValidator(_no_card_number)]
    score: int = Field(ge=1, le=5)


# ---- Insights (6.5) ----
class Need(BaseModel):
    label: str
    count: int


class Objection(BaseModel):
    label: str
    count: int
    example_interaction_ids: list[str] = Field(default_factory=list)


class RecommendationItem(BaseModel):
    id: str
    title: str
    action: str
    rationale: str
    evidence: dict[str, Any]  # {"kpi": str, "interaction_ids": [str]}
    impact: Literal["low", "medium", "high"]
    effort: Literal["low", "medium", "high"]


class InsightsAnalysis(BaseModel):
    needs: list[Need] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    recommendations: list[RecommendationItem] = Field(default_factory=list)


class ProductSuggestionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension_scores: dict[str, float]
    theme_counts: dict[str, int]
    response_ids: list[str] = Field(min_length=1)


class ProductImprovementSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: Literal["page_copy", "faq_add", "pricing", "onboarding", "support", "feature"]
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1000)
    target_section: str = Field(min_length=1, max_length=80)
    before: str = Field(default="", max_length=1000)
    after: str = Field(default="", max_length=1000)
    priority: Literal["low", "medium", "high"]
    impact: Literal["low", "medium", "high"]
    effort: Literal["low", "medium", "high"]
    evidence: ProductSuggestionEvidence


class LeadOpportunity(BaseModel):
    lead_id: str
    score: float
    reason: str
    draft_message: str


# ---- KPIs (6.4) ----
class KPIs(BaseModel):
    interactions_count: int = 0
    interested_prospects: int = 0
    paid_prospects: int = 0
    converted_prospects: int = 0  # paid AND interested; conversion_rate = converted / interested
    conversion_rate: float = 0.0
    top_objections: list[Objection | dict[str, Any]] = Field(default_factory=list)
    payments_by_status: dict[str, int] = Field(default_factory=dict)
    simulated_revenue: float = 0.0
    satisfaction_avg: float | None = None
    security_alerts_count: int = 0
    security_alerts_by_type: dict[str, int] = Field(default_factory=dict)
    latest_recommendations: list[dict[str, Any]] = Field(default_factory=list)
