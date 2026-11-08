"""Deterministic rules for the Sales Agent: interest, buy intent, objections, mock product answers."""
import re

from app.schemas import Product

CANNOT_ANSWER = "CANNOT_ANSWER"  # sentinel the fallback LLM (and its mock) returns when the file lacks the answer

_I = re.IGNORECASE
_GREETING = re.compile(r"^\s*(hi|hello|hey|bonjour|bonsoir|salut|salam|slm|merci|thanks|thank you|ok|"
                       r"مرحبا|السلام)\W*$", _I)

# Interest rules (cheapest first). `high` needs a clear action verb; `medium` shows evaluation intent.
_HIGH = re.compile(r"sign me up|sign up|register|enrol|inscri|je veux (payer|acheter)|ready to (pay|buy)|"
                   r"i('ll| will) take|how (do|can) i (pay|buy|join)|comment (payer|rejoindre)|"
                   r"نسجل|ندفع|بغيت نسجل", _I)
_MEDIUM = re.compile(r"price|prix|cost|combien|how much|tarif|discount|reduction|plan|standard|pro\b|"
                     r"schedule|horaire|refund|rembours|certificat|prerequi|السعر|"
                     r"كم", _I)
_LOW = re.compile(r"bootcamp|course|formation|program|learn|cours|training|session|ai\b|\bia\b", _I)

# Buy intent: explicit request to enroll/pay (not merely mentioning "pay" or "plan").
_BUY = re.compile(r"(i want to|i'd like to|i would like to|let me|je veux|je voudrais|i wanna) "
                  r"(sign up|register|enrol|buy|pay|join|m'inscrire|payer|acheter|rejoindre)|sign me up|"
                  r"how (do|can) i (pay|register|sign up|enrol|join)|comment (payer|m'inscrire|s'inscrire)|"
                  r"ready to (pay|buy|enrol)|بغيت نسجل|"
                  r"نسجل", _I)
_PRO = re.compile(r"\bpro\b|mentor", _I)

_OBJECTIONS: list[tuple[str, re.Pattern]] = [
    ("price", re.compile(r"expensive|too (much|high)|trop cher|\bcher\b|afford|budget|cheaper|"
                         r"discount|r[eé]duction|مرتفع|غالي", _I)),
    ("schedule", re.compile(r"work until|i work|busy|conflict|not flexible|flexible|ne me conviennent|"
                            r"too (late|early)|المواعيد", _I)),
    ("prerequisites", re.compile(r"no (prior )?experience|never (done|coded|used)|jamais|beginner|d[eé]butant|"
                                 r"prior experience|follow along|suivre", _I)),
    ("trust", re.compile(r"legit|scam|trust|s[eé]rieu|confiance|fiable|arnaque|fake|je ne vous connais", _I)),
    ("certificate", re.compile(r"(certificat\w*).*(recogni|reconnu|value|valid|employ|worth|valeur)|"
                               r"(recogni|reconnu|value|valid|employ|worth).*(certificat\w*)", _I)),
]


def is_greeting(text: str) -> bool:
    return bool(_GREETING.match(text))


def rule_interest(text: str) -> str | None:
    """Interest level from keywords, or None when the rules cannot tell (caller may ask the LLM)."""
    if _HIGH.search(text):
        return "high"
    if _MEDIUM.search(text):
        return "medium"
    if is_greeting(text):
        return "none"
    if _LOW.search(text):
        return "low"
    return None


def is_ambiguous(text: str) -> bool:
    """No rule fired and the message is long enough to carry intent."""
    return rule_interest(text) is None and len(text.split()) >= 4


def has_buy_intent(text: str) -> bool:
    return bool(_BUY.search(text))


def guess_plan(text: str) -> str:
    return "pro" if _PRO.search(text) else "std"


def detect_objection(text: str) -> str | None:
    for label, pat in _OBJECTIONS:
        if pat.search(text):
            return label
    return None


# ---- mock fallback answers (built only from product.json fields) ----------------------------------
def _plans(p: Product) -> str:
    return " / ".join(f"{pl.name}: {pl.price:g} {p.currency}" for pl in p.plans)


def _feature(p: Product, word: str) -> str | None:
    return next((f for f in p.features if word in f.lower()), None)


def mock_answer(p: Product, text: str) -> str:
    """Deterministic answer from product fields when a keyword matches, else CANNOT_ANSWER."""
    t = text.lower()

    def has(*words: str) -> bool:
        return any(w in t for w in words)

    if has("record", "replay", "enregistr"):
        f = _feature(p, "recording")
        if f:
            return f"{p.name} includes: {f}."
    if has("project", "projet", "hands-on", "practice", "pratique"):
        f = _feature(p, "project")
        if f:
            return f"{p.name} includes: {f}."
    if has("how many session", "sessions", "live"):
        f = _feature(p, "session")
        if f:
            return f"{p.name} includes: {f}. {p.policies.schedule}".strip()
    if has("week", "semaine", "duration", "how long", "duree", "durée"):
        return f"{p.tagline}. {p.policies.schedule}".strip()
    if has("mentor", "coaching"):
        pro = next((pl for pl in p.plans if "mentor" in pl.name.lower()), None)
        if pro:
            return f"The {pro.name} plan ({pro.price:g} {p.currency}) adds mentoring."
    if has("student", "etudiant", "junior", "audience", "who is", "for whom"):
        return f"{p.name} is designed for {' and '.join(p.audience)}."
    if has("learn", "apprend", "topic", "curriculum", "teach", "python", "agent", "prompt", "deploy"):
        return p.description
    if has("plan", "offer", "formule"):
        return f"Available plans: {_plans(p)}."
    return CANNOT_ANSWER
