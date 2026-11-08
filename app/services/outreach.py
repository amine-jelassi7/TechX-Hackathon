"""Pure helpers for lead scoring and outreach drafts. Nothing here sends anything."""
from __future__ import annotations

import re

from app.gateway import filters
from app.schemas import Product

OPT_OUT = "Reply STOP to opt out."
SOURCE_QUALITY = {"referral": 1.0, "event": 0.8, "newsletter": 0.6, "linkedin": 0.5, "cold_list": 0.2}
_NAME_OK = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)?$", re.UNICODE)
_WORD = re.compile(r"[a-z]{3,}")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def product_keywords(product: Product) -> set[str]:
    parts = [product.name, product.tagline, product.description, *product.features, *product.audience]
    return set().union(*(_tokens(p) for p in parts))


def safe_name(raw: str) -> str:
    """Lead names are untrusted: keep a single clean word, else fall back to a neutral greeting."""
    text = filters.strip_markup(filters.normalize(raw or ""))
    if len(text) > 24 or not _NAME_OK.match(text) or filters.injection_score(text)[0] > 0.0:
        return "there"
    return text


def safe_interest(raw: str) -> str:
    text = filters.strip_markup(filters.normalize(raw or ""))
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE).strip()
    if not text or len(text) > 40 or filters.injection_score(text)[0] > 0.0:
        return "AI"
    return text


def score_lead(interest: str, source: str, keywords: set[str]) -> float:
    toks = _tokens(interest)
    overlap = len(toks & keywords) / len(toks) if toks else 0.0
    return round(0.7 * overlap + 0.3 * SOURCE_QUALITY.get(source, 0.1), 3)


def explain(interest: str, source: str, score: float) -> str:
    return f"Interest in {interest} matches the programme; lead source {source}; score {score:.2f}."


def ensure_opt_out(message: str) -> str:
    body = message.replace(OPT_OUT, "").rstrip()
    return f"{body}\n{OPT_OUT}"


def draft_template(first_name: str, interest: str, product: Product) -> str:
    return ensure_opt_out(
        f"Hi {first_name}, since you are interested in {interest}: {product.name} - {product.tagline}. "
        f"It costs {product.price:g} {product.currency}."
    )
