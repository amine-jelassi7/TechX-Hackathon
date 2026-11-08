"""Layer 1 of the security gateway: pure, deterministic text filters (spec 7.2).

No I/O, no LLM. Functions return scores in 0..1 plus human-readable reason codes.
"""
# ruff: noqa: ISC004  (pattern table uses implicit string concatenation on purpose)
import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

REDACTED_CARD = "[REDACTED_CARD]"
REDACTED_SECRET = "[REDACTED_SECRET]"
MAX_PAYLOAD_STR = 300
_SCAN_CAP = 20000  # never run regexes over more than this many chars

# Cyrillic/Greek lookalikes mapped to Latin so "ignоre" cannot dodge the patterns.
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
    "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ո": "n", "ν": "v", "ο": "o", "ρ": "p",
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "τ": "t",
})
_WS = re.compile(r"\s+")


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove control chars (except whitespace) and zero-width/format chars. Returns (text, removed)."""
    out, removed = [], 0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Cc", "Co", "Cs") and not ch.isspace():
            removed += 1
            continue
        out.append(ch)
    return "".join(out), removed


def normalize(text: str) -> str:
    """NFKC, strip control and zero-width chars, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text, _ = strip_invisible(text)
    return _WS.sub(" ", text).strip()


def fold(text: str) -> str:
    """Aggressive matching form: lowercase, no diacritics/tashkeel/tatweel, Latin homoglyphs, alef unified."""
    text = unicodedata.normalize("NFKD", normalize(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ـ", "").translate(_HOMOGLYPHS)
    for a in "أإآٱ":
        text = text.replace(a, "ا")
    return text.replace("ى", "ي")


# ---- injection patterns: (regex on folded text, weight, reason) --------------------------
_TARGET = r"(?:instructions?|rules?|prompts?|guidelines?|directives?|consignes?|regles?|instruction)"
_P = [
    # English
    (r"\b(?:ignore|disregard|forget|override|bypass|skip)\s+(?:all\s+|any\s+|the\s+|your\s+|my\s+|every\s+)*"
     r"(?:previous|prior|above|earlier|preceding|former|system|safety|original|initial)?\s*" + _TARGET,
     0.9, "override_instructions"),
    (r"\b(?:ignore|disregard|forget)\s+(?:everything|all)\s+(?:above|before|you were told)", 0.9,
     "override_instructions"),
    (r"\b(?:reveal|print|show|display|repeat|output|leak|tell me|give me|dump|expose)\b[^.?!]{0,40}"
     r"\b(?:system prompt|your (?:hidden |initial |secret |original )?(?:instructions|prompt|rules)|"
     r"hidden rules|initial prompt|developer message)", 0.85, "prompt_extraction"),
    (r"\bsystem prompt\b", 0.5, "mentions_system_prompt"),
    (r"\byou are now\b", 0.6, "role_reassignment"),
    (r"\b(?:pretend|imagine) (?:to be|you are|you're|that you)\b", 0.5, "roleplay"),
    (r"\b(?:act|behave) as (?:if you (?:are|were)|an? )", 0.3, "roleplay"),
    (r"\bdeveloper mode\b|\bjailbreak(?:ed|ing)?\b|\bdo anything now\b|\bgod mode\b", 0.8, "jailbreak"),
    (r"\bdan\b(?= mode|,| an ai| who| with)", 0.7, "jailbreak"),
    (r"\bno (?:rules|restrictions|limits|filters)\b|\bwithout (?:any )?(?:restrictions|rules|limits)\b", 0.4,
     "no_restrictions"),
    (r"\bstay in character\b", 0.4, "roleplay"),
    (r"\b(?:new|updated) (?:instructions|rules)\s*:", 0.5, "new_instructions"),
    (r"(?:^|\s)(?:system|assistant|developer)\s*:\s*\S", 0.6, "fake_role_marker"),
    (r"<\|?(?:im_start|im_end|system|endoftext)\|?>|\[/?inst\]|###\s*(?:system|instruction)", 0.8,
     "fake_role_marker"),
    (r"\b100\s*%\s*(?:discount|off|reduction)\b|\b(?:secret|hidden)\s+(?:admin\s+)?(?:discount\s+)?code\b", 0.5,
     "discount_manipulation"),
    (r"\bdecode (?:and|then) (?:follow|execute|run|obey)\b", 0.5, "decode_and_follow"),
    # French (folded: no accents)
    (r"\b(?:ignore[zr]?|oublie[zr]?|neglige[zr]?|contourne[zr]?|desobei[sr]?)\s+(?:toutes?\s+|tous\s+|les\s+|"
     r"tes\s+|vos\s+|ces\s+|mes\s+|ta\s+|votre\s+)*(?:precedent(?:e|es|s)?\s+|anterieur(?:e|es|s)?\s+)?" + _TARGET,
     0.9, "override_instructions"),
    (r"\bne tiens? pas compte (?:des|de tes|de vos)\b[^.]{0,30}" + _TARGET, 0.9, "override_instructions"),
    (r"\b(?:reve?le[zr]?|affiche[zr]?|montre[zr]?|donne[zr]?|imprime[zr]?|repete[zr]?)\b[^.?!]{0,40}"
     r"\b(?:prompt systeme|(?:ton|votre|tes|vos) (?:prompt|instructions|consignes|regles)|instructions cachees)",
     0.85, "prompt_extraction"),
    (r"\bprompt systeme\b|\binstructions? systeme\b", 0.5, "mentions_system_prompt"),
    (r"\btu es (?:maintenant|desormais)\b|\bvous etes (?:maintenant|desormais)\b", 0.6, "role_reassignment"),
    (r"\bmode (?:developpeur|dev|sans restrictions?|sans limites?)\b", 0.8, "jailbreak"),
    (r"\bfais comme si (?:tu|vous)\b|\bfais semblant\b|\bjoue le role\b", 0.5, "roleplay"),
    (r"\b(?:reduction|remise) de 100\s*%|\bcode (?:admin|secret)\b", 0.5, "discount_manipulation"),
    # Arabic (folded: alef unified, no tashkeel)
    (r"(?:تجاهل|تجاهلي|انس|انسي|اهمل|تخط|تخطي|الغ|الغي)\s+(?:كل\s+|جميع\s+|جمي?ع\s+)?"
     r"(?:ال)?(?:تعليمات|اوامر|قواعد|ارشادات|توجيهات)", 0.9, "override_instructions"),
    (r"(?:اكشف|اظهر|اعرض|اطبع|اخبرني|اعطني|كرر)\s*(?:لي\s+)?(?:عن\s+)?[^.؟!]{0,30}"
     r"(?:تعليمات|موجه|برومبت|prompt|قواعد)\s*(?:ال)?(?:نظام|مخفيه|سريه)?", 0.8, "prompt_extraction"),
    (r"(?:تعليمات|موجه|برومبت|رسالة)\s+النظام", 0.6, "mentions_system_prompt"),
    (r"\bانت الان\b|\bانتي الان\b", 0.6, "role_reassignment"),
    (r"وضع (?:المطور|المطورين)|وضع بلا قيود|كسر الحماية", 0.8, "jailbreak"),
    (r"تظاهر|تصرف كما لو|العب دور", 0.5, "roleplay"),
    (r"خصم\s*100\s*%|100\s*%\s*خصم|كود (?:سري|الادمن)|رمز (?:سري|الادمن)", 0.5, "discount_manipulation"),
]
_P.append((r"<\s*/?\s*untrusted\s*>", 0.9, "delimiter_breakout"))  # forged data-wrapper tag
INJECTION_PATTERNS = [(re.compile(rx), w, why) for rx, w, why in _P]
_UNTRUSTED_TAG = re.compile(r"<\s*/?\s*untrusted\s*>", re.IGNORECASE)


def strip_untrusted_tags(text: str) -> str:
    """Remove every `<untrusted>` / `</untrusted>` tag (any case/spacing), repeatedly so nesting cannot rebuild one."""
    text = strip_invisible(text)[0]
    while True:
        out = _UNTRUSTED_TAG.sub("", text)
        if out == text:
            return out
        text = out


def wrap_untrusted(text: str) -> str:
    """The only way untrusted text enters an LLM prompt: the wrapper tags are ours, never the user's."""
    return f"<untrusted>{strip_untrusted_tags(text)}</untrusted>"
_B64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
_SPACED = re.compile(r"(?:\b\w\s){5,}\w\b")


def _combine(weights: list[float]) -> float:
    """Noisy-or: independent signals reinforce each other, result stays in 0..1."""
    p = 1.0
    for w in weights:
        p *= 1.0 - w
    return round(1.0 - p, 4)


def _scan_patterns(folded: str) -> list[tuple[float, str]]:
    return [(w, why) for rx, w, why in INJECTION_PATTERNS if rx.search(folded)]


def _decode_b64_blobs(text: str) -> list[str]:
    out = []
    for m in _B64.finditer(text[:_SCAN_CAP]):
        blob = m.group(0)
        if blob.isdigit() or len(blob) % 4 == 1:
            continue
        try:
            raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
            dec = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        printable = sum(c.isprintable() or c.isspace() for c in dec)
        if dec and printable / len(dec) >= 0.9:
            out.append(dec)
    return out


def injection_score(text: str, _depth: int = 0) -> tuple[float, list[str]]:
    """Score 0..1 of how likely `text` is a prompt injection, with reason codes.

    Scans the folded text, a de-spaced variant ("i g n o r e") and any decoded base64 blob.
    """
    text = text[:_SCAN_CAP]
    folded = fold(text)
    hits = _scan_patterns(folded)
    if _SPACED.search(folded):
        joined = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), folded)
        hits += [(w, f"{why}(spaced)") for w, why in _scan_patterns(joined)]
    for dec in _decode_b64_blobs(text) if _depth < 2 else []:
        d_score, d_reasons = injection_score(dec, _depth + 1)
        hits.append((max(d_score, 0.3), "base64_blob" if d_score < 0.3 else "base64_encoded_injection"))
        hits += [(0.0, f"decoded:{r}") for r in d_reasons]
    reasons = list(dict.fromkeys(why for _, why in hits))
    return _combine([w for w, _ in hits]), reasons


# ---- HTML / markdown injection --------------------------------------------------------------
_HTML_BAD = re.compile(
    r"<\s*/?\s*(?:script|iframe|object|embed|style|svg|link|meta|form)\b|\bon(?:error|load|click|mouse\w+|focus|blur|submit|change|key\w+|touch\w+|toggle|animation\w+)\s*=|javascript\s*:|"
    r"<!--|data:text/html", re.IGNORECASE)
_MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*[^)\s]+[^)]*\)")
_TAG = re.compile(r"<[^>]*>")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_BLOCK = re.compile(r"<\s*(script|style)\b.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)


def markup_findings(text: str) -> list[str]:
    reasons = []
    if _HTML_BAD.search(text):
        reasons.append("html_script_injection")
    if _MD_LINK.search(text):
        reasons.append("markdown_link")
    return reasons


def strip_markup(text: str) -> str:
    """Remove script blocks, HTML comments, tags and markdown links (keeping link text)."""
    text = _SCRIPT_BLOCK.sub(" ", text)
    text = _COMMENT.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("<", " ").replace(">", " ")
    return _WS.sub(" ", text).strip()


# ---- card numbers (Luhn) and secrets --------------------------------------------------------
_DIGIT_RUN = re.compile(r"\d+(?:[ \-]\d+)*")
_DIGIT_TOKEN = re.compile(r"\d+")


def luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits]
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_card_spans(text: str) -> list[tuple[int, int]]:
    """Spans of 13-19 digit sequences (spaces/dashes allowed) that pass the Luhn check.

    Tries every contiguous range of digit groups so trailing data ("... 12 30") cannot hide a card.
    A 16-digit number failing Luhn (e.g. an order number) is not reported.
    """
    spans: list[tuple[int, int]] = []
    for run in _DIGIT_RUN.finditer(text):
        toks = list(_DIGIT_TOKEN.finditer(run.group(0)))
        base, found = run.start(), []
        for i in range(len(toks)):
            total = ""
            for j in range(i, len(toks)):
                total += toks[j].group(0)
                if len(total) > 19:
                    break
                if len(total) >= 13 and luhn_ok(total):
                    found.append((base + toks[i].start(), base + toks[j].end()))
        found.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        last_end = -1
        for s, e in found:
            if s >= last_end:
                spans.append((s, e))
                last_end = e
    return spans


_SECRET_PATTERNS = [
    (re.compile(r"\b(?:cvv2?|cvc2?|cid|security code|code de securite|cryptogramme)\b[\s:=\-#]*(?:is|est|are)?[\s:=\-#]*\d{3,4}\b", re.IGNORECASE),
     "cvv"),
    (re.compile(r"\b(?:password|passwd|pwd|passcode|mot de passe|mdp)\b\s*(?:is|est|are|=|:)\s*\S+", re.IGNORECASE),
     "password"),
    (re.compile(r"(?:كلمة (?:السر|المرور))\s*(?:هي|:|=)?\s*\S+"), "password"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "api_key"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "api_key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "api_key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE), "api_key"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "api_key"),
    (re.compile(r"\b(?:api[_\- ]?key|secret|access[_\- ]?token|auth[_\- ]?token)\b\s*(?:is|=|:)\s*(?!tok_)\S{8,}",
                re.IGNORECASE), "api_key"),
]


def find_secrets(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), kind) for rx, kind in _SECRET_PATTERNS for m in rx.finditer(text)]


def redact(text: str) -> str:
    """Replace card-like numbers and CVV/password/API-key patterns with placeholders."""
    text = normalize(text)
    marks = [(s, e, REDACTED_CARD) for s, e in find_card_spans(text)]
    marks += [(s, e, REDACTED_SECRET) for s, e, _ in find_secrets(text)]
    out, pos = [], 0
    for s, e, repl in sorted(marks):
        if s < pos:
            continue
        out.append(text[pos:s])
        out.append(repl)
        pos = e
    out.append(text[pos:])
    return "".join(out)


# ---- aggregate input analysis ---------------------------------------------------------------
@dataclass
class FilterResult:
    text: str  # normalized text
    score: float = 0.0  # injection score 0..1
    reasons: list[str] = field(default_factory=list)
    too_long: bool = False
    invisible_removed: int = 0
    card: bool = False
    secret: bool = False
    markup: bool = False
    redacted: str = ""
    stripped: str = ""  # normalized text with markup removed


def analyze(text: str, max_length: int | None = None) -> FilterResult:
    """Run every deterministic filter over `text` (layer 1)."""
    limit = max_length or settings.input_max_length
    if len(text) > limit:
        return FilterResult(text=text[:limit], score=0.0, reasons=["input_too_long"], too_long=True,
                            redacted=redact(text[:limit]))
    cleaned, removed = strip_invisible(unicodedata.normalize("NFKC", text))
    norm = _WS.sub(" ", cleaned).strip()
    score, reasons = injection_score(text)
    res = FilterResult(text=norm, score=score, reasons=list(reasons), invisible_removed=removed)
    if removed >= 3:
        res.reasons.append("hidden_characters")
        res.score = _combine([res.score, 0.2])
    if find_card_spans(norm):
        res.card = True
        res.reasons.append("card_number")
    if find_secrets(norm):
        res.secret = True
        res.reasons.append("secret_pattern")
    marks = markup_findings(norm)
    if marks:
        res.markup = True
        res.reasons.extend(marks)
    res.redacted = redact(norm)
    res.stripped = strip_markup(norm) if res.markup else norm
    return res


# ---- output screening -----------------------------------------------------------------------
SYSTEM_PROMPT_MARKERS = (
    "system prompt", "<untrusted>", "</untrusted>", "you are the sales agent", "you are the payment agent",
    "you are the insights", "begin system", "end system", "developer message", "my instructions are",
    "prompt systeme", "mes instructions sont", "تعليمات النظام",
)
_URL = re.compile(r"(?:https?://|ftp://|www\.)[^\s<>\"')\]]+|\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:com|net|org|io|ai|tn|"
                  r"fr|ru|xyz|top|info|co|me|app|dev|test|example)/[^\s<>\"')\]]*", re.IGNORECASE)


def find_unknown_urls(text: str, allowed: tuple[str, ...] = ()) -> list[str]:
    """URLs in `text` other than the allowed ones (the product contact). E-mail addresses are not URLs."""
    no_email = re.sub(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", " ", text)
    allowed_l = [a.lower() for a in allowed if a]
    return [u for u in _URL.findall(no_email) if not any(a in u.lower() for a in allowed_l)]


def output_findings(text: str, allowed_urls: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Return {kind: [detail]} for leaks in an agent output. Empty dict means clean."""
    norm = normalize(text)
    low = fold(text)
    found: dict[str, list[str]] = {}
    if find_card_spans(norm):
        found["card_number"] = ["card-like number in output"]
    if find_secrets(norm):
        found["secret"] = ["secret pattern in output"]
    markers = [m for m in SYSTEM_PROMPT_MARKERS if m in low]
    if markers:
        found["system_prompt_leak"] = markers
    urls = find_unknown_urls(norm, allowed_urls)
    if urls:
        found["unknown_url"] = urls[:3]
    score, reasons = injection_score(text)
    if score >= 0.7:
        found["injected_instruction"] = reasons
    return found


# ---- payload redaction for the audit log ----------------------------------------------------
_SENSITIVE_KEY = re.compile(r"card|cvv|cvc|password|passwd|secret|api_?key|authorization|owner_token", re.IGNORECASE)


def redact_payload(obj: Any, _depth: int = 0) -> Any:
    """JSON-safe, redacted, truncated copy of an arbitrary payload (strings cut to 300 chars)."""
    if _depth > 6:
        return "[TRUNCATED]"
    if isinstance(obj, str):
        out = redact(obj)
        return out if len(out) <= MAX_PAYLOAD_STR else out[:MAX_PAYLOAD_STR] + "..."
    if isinstance(obj, dict):
        return {str(k): ("[REDACTED]" if _SENSITIVE_KEY.search(str(k)) else redact_payload(v, _depth + 1))
                for k, v in list(obj.items())[:50]}
    if isinstance(obj, (list, tuple, set)):
        return [redact_payload(v, _depth + 1) for v in list(obj)[:50]]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return redact_payload(str(obj), _depth + 1)
