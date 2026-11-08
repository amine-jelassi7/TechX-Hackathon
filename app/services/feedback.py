"""Deterministic customer feedback scoring and aggregation."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import FeedbackResponse

WEIGHTS = {
    "ease": 15,
    "feature_fit": 20,
    "value": 15,
    "reliability": 15,
    "support": 10,
    "satisfaction": 10,
    "recommend": 15,
}
QUESTION_BANK_VERSION = "1.0"
QUESTION_BANK = {
    "version": QUESTION_BANK_VERSION,
    "fr": {
        "ease": ["Le service est-il facile à prendre en main ?", "La navigation est-elle claire ?"],
        "feature_fit": [
            "Répond-il à vos besoins ?",
            "Les fonctionnalités clés sont-elles suffisantes ?",
        ],
        "value": [
            "Le prix correspond-il à la valeur reçue ?",
            "Le résultat justifie-t-il le coût ?",
        ],
        "reliability": ["Le service est-il fiable ?", "Avez-vous rencontré des interruptions ?"],
        "support": ["Êtes-vous satisfait du support ?", "Avez-vous reçu une réponse utile ?"],
        "satisfaction": [
            "Quelle est votre satisfaction globale ?",
            "Le service a-t-il répondu à vos attentes ?",
        ],
        "recommend": "Quelle est la probabilité de recommander ce service (0–10) ?",
        "review": "Votre avis (facultatif)",
    },
    "en": {
        "ease": ["Was the service easy to get started with?", "Was navigation clear?"],
        "feature_fit": ["Did it meet your needs?", "Were the key features sufficient?"],
        "value": ["Was it worth the price?", "Did the outcome justify the cost?"],
        "reliability": ["Was the service reliable?", "Did you experience interruptions?"],
        "support": ["Were you satisfied with support?", "Did you receive a useful response?"],
        "satisfaction": [
            "How satisfied are you overall?",
            "Did the service meet your expectations?",
        ],
        "recommend": "How likely are you to recommend it (0–10)?",
        "review": "Your review (optional)",
    },
}


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ease: int | None = Field(default=None, ge=1, le=5)
    ease_2: int | None = Field(default=None, ge=1, le=5)
    feature_fit: int | None = Field(default=None, ge=1, le=5)
    feature_fit_2: int | None = Field(default=None, ge=1, le=5)
    value: int | None = Field(default=None, ge=1, le=5)
    value_2: int | None = Field(default=None, ge=1, le=5)
    reliability: int | None = Field(default=None, ge=1, le=5)
    reliability_2: int | None = Field(default=None, ge=1, le=5)
    support: int | None = Field(default=None, ge=1, le=5)
    support_2: int | None = Field(default=None, ge=1, le=5)
    satisfaction: int | None = Field(default=None, ge=1, le=5)
    satisfaction_2: int | None = Field(default=None, ge=1, le=5)
    recommend: int | None = Field(default=None, ge=0, le=10)
    review: str = Field(default="", max_length=2000)

    @field_validator("session_id")
    @classmethod
    def no_card_id(cls, value: str) -> str:
        from app.schemas import _no_card_number

        return _no_card_number(value)


def score_feedback(answers: dict[str, Any]) -> tuple[float, dict[str, float], str]:
    dimensions: dict[str, float] = {}
    numerator = denominator = 0.0
    for key, weight in WEIGHTS.items():
        values = [answers.get(key), answers.get(f"{key}_2")]
        values = [value for value in values if value is not None]
        if not values:
            continue
        if key == "recommend":
            score = float(values[0]) * 10
        else:
            if any(value not in (1, 2, 3, 4, 5) for value in values):
                raise ValueError(f"invalid {key} score")
            score = sum((float(value) - 1) * 25 for value in values) / len(values)
        dimensions[key] = score
        numerator += score * weight
        denominator += weight
    if not denominator:
        raise ValueError("at least one scored answer is required")
    nps = answers.get("recommend")
    category = (
        "unknown"
        if nps is None
        else "detractor"
        if nps <= 6
        else "passive"
        if nps <= 8
        else "promoter"
    )
    return round(numerator / denominator, 2), dimensions, category


def score_label(score: float) -> str:
    return (
        "excellent" if score >= 85 else "good" if score >= 65 else "fair" if score >= 40 else "poor"
    )


def aggregate(responses: list[FeedbackResponse], minimum: int = 5) -> dict[str, Any]:
    count = len(responses)
    if not count:
        return {
            "response_count": 0,
            "ready": False,
            "message": "not enough data yet",
            "average": None,
            "distribution": {},
            "nps": None,
            "dimensions": {},
            "themes": {},
        }
    average = round(sum(r.score for r in responses) / count, 2)
    categories = Counter(r.nps_category for r in responses)
    valid_nps = count - categories.get("unknown", 0)
    nps = (
        round(100 * (categories.get("promoter", 0) - categories.get("detractor", 0)) / valid_nps, 2)
        if valid_nps
        else None
    )
    dimensions = {
        key: round(
            sum(r.dimensions.get(key, 0) for r in responses if key in r.dimensions)
            / max(1, sum(key in r.dimensions for r in responses)),
            2,
        )
        for key in WEIGHTS
    }
    return {
        "response_count": count,
        "ready": count >= minimum,
        "message": "ready" if count >= minimum else "not enough data yet",
        "average": average,
        "label": score_label(average),
        "distribution": dict(
            Counter(
                "excellent"
                if r.score >= 85
                else "good"
                if r.score >= 65
                else "fair"
                if r.score >= 40
                else "poor"
                for r in responses
            )
        ),
        "nps": nps,
        "dimensions": dimensions,
        "trend": [
            {"date": r.created_at.date().isoformat(), "score": r.score}
            for r in sorted(responses, key=lambda x: x.created_at)
        ],
        "themes": dict(Counter(t for r in responses for t in r.themes)),
    }
