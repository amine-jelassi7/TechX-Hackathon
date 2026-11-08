"""Deterministic product fit quiz: six weighted signals, ranked catalog matches."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.schemas import CatalogProduct, FitQuizAnswers, ProductCatalog, ProductMatch

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "products.json"

QUESTIONS: list[dict] = [
    {
        "key": "goal",
        "label": "What do you want to achieve?",
        "hint": "Pick the outcome that matters most right now.",
        "options": [
            {"value": "build-ai-products", "label": "Build AI products",
             "note": "Ship an assistant, an agent or an AI feature"},
            {"value": "analyze-data", "label": "Work with data",
             "note": "Turn raw data into dashboards and decisions"},
            {"value": "automate-work", "label": "Automate my work",
             "note": "Remove repetitive tasks from the week"},
            {"value": "train-team", "label": "Train my team",
             "note": "Give a whole team the same AI baseline"},
        ],
    },
    {
        "key": "level",
        "label": "Where are you starting from?",
        "hint": "No right answer. This only calibrates the depth.",
        "options": [
            {"value": "beginner", "label": "Starting from zero", "note": "No code yet"},
            {"value": "some-code", "label": "I know some code",
             "note": "Scripts, spreadsheets, a little Python"},
            {"value": "developer", "label": "I ship software",
             "note": "Comfortable with APIs and repositories"},
        ],
    },
    {
        "key": "budget",
        "label": "What budget feels right?",
        "hint": "All prices are synthetic and shown in TND.",
        "options": [
            {"value": "low", "label": "Low", "note": "Under 500 TND"},
            {"value": "medium", "label": "Medium", "note": "500 to 1500 TND"},
            {"value": "high", "label": "High", "note": "Above 1500 TND"},
        ],
    },
    {
        "key": "format",
        "label": "How do you like to learn?",
        "hint": "The format changes how much structure you get.",
        "options": [
            {"value": "live-cohort", "label": "Live cohort",
             "note": "Fixed sessions with a group and a calendar"},
            {"value": "self-paced", "label": "Self-paced",
             "note": "Build whenever you have time"},
            {"value": "workshop", "label": "Team workshop",
             "note": "Facilitated sessions for a group"},
        ],
    },
    {
        "key": "audience",
        "label": "Who is this for?",
        "hint": "We match the product to the person or team joining.",
        "options": [
            {"value": "individual", "label": "Just me", "note": "A personal goal"},
            {"value": "student", "label": "Student", "note": "Early in a career"},
            {"value": "startup", "label": "Startup team", "note": "A small product team"},
            {"value": "enterprise", "label": "Enterprise", "note": "A larger organisation"},
        ],
    },
    {
        "key": "pace",
        "label": "How much time can you commit?",
        "hint": "This decides how intensive the recommendation is.",
        "options": [
            {"value": "full-time", "label": "Full-time", "note": "Several days a week"},
            {"value": "part-time", "label": "Part-time", "note": "A few evenings a week"},
            {"value": "weekend", "label": "Weekend", "note": "One focused weekend"},
        ],
    },
]

WEIGHTS: dict[str, float] = {
    "goal": 0.28,
    "level": 0.16,
    "budget": 0.14,
    "format": 0.18,
    "audience": 0.12,
    "pace": 0.12,
}

REASON_PREFIX: dict[str, str] = {
    "goal": "Goal",
    "level": "Level",
    "budget": "Budget",
    "format": "Format",
    "audience": "Audience",
    "pace": "Pace",
}

BANDS: tuple[tuple[int, str], ...] = ((80, "Strong fit"), (60, "Good fit"), (0, "Possible fit"))
TOP_K = 3


def _option_map() -> dict[str, dict[str, dict]]:
    return {q["key"]: {o["value"]: o for o in q["options"]} for q in QUESTIONS}


_OPTIONS = _option_map()


@lru_cache(maxsize=1)
def load_catalog() -> ProductCatalog:
    return ProductCatalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def _band(fit: int) -> str:
    return next(label for threshold, label in BANDS if fit >= threshold)


def _reasons(answers: FitQuizAnswers, product: CatalogProduct) -> list[str]:
    matched = answers.model_dump()
    out = []
    for key in WEIGHTS:
        if matched[key] in getattr(product.match, key):
            label = _OPTIONS[key][matched[key]]["label"]
            out.append(f"{REASON_PREFIX[key]}: {label}")
    return out


def _score(answers: FitQuizAnswers, product: CatalogProduct) -> int:
    matched = answers.model_dump()
    total = sum(w for key, w in WEIGHTS.items() if matched[key] in getattr(product.match, key))
    return round(total * 100)


def recommend(answers: FitQuizAnswers, catalog: ProductCatalog | None = None,
              top_k: int = TOP_K) -> list[ProductMatch]:
    catalog = catalog or load_catalog()
    ranked = []
    for product in catalog.products:
        fit = _score(answers, product)
        ranked.append((fit, product))
    ranked.sort(key=lambda item: (-item[0], item[1].price, item[1].id))
    return [
        ProductMatch(
            id=p.id, name=p.name, tagline=p.tagline, description=p.description,
            audience=p.audience, price=p.price, currency=p.currency, plans=p.plans,
            features=p.features, contact=p.contact, fit=fit, band=_band(fit),
            reasons=_reasons(answers, p),
        )
        for fit, p in ranked[:top_k]
    ]


def question_payload() -> list[dict]:
    return json.loads(json.dumps(QUESTIONS))
