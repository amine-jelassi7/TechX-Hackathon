"""Q&A generation, owner moderation and semantic matching over approved entries."""

import re
import threading
import unicodedata

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import settings
from app.llm import chat_json, load_product
from app.models import QAEntry
from app.schemas import Product

# TF-IDF cosine (max of char_wb 2-4 grams and word level, accent-folded, stopwords stripped,
# scaled by the share of query terms the corpus knows) lives on a lower scale than embedding
# cosines (0.72). Calibrated on the seeded entries: paraphrases/typos EN+FR score 0.42-1.0
# ("remboursement possible?" 0.57); off-topic stays <= 0.31 ("tell me prices of laptops" 0.31,
# "what is the weather in Paris" 0.09). 0.37 sits in that gap.
TFIDF_THRESHOLD = 0.37
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "be",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "i",
        "you",
        "we",
        "me",
        "my",
        "your",
        "it",
        "its",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "what",
        "whats",
        "which",
        "who",
        "how",
        "there",
        "this",
        "that",
        "with",
        "about",
        "have",
        "has",
        "get",
        "want",
        "please",
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "d",
        "l",
        "est",
        "sont",
        "a",
        "ai",
        "as",
        "avez",
        "avons",
        "y",
        "il",
        "elle",
        "on",
        "je",
        "tu",
        "nous",
        "vous",
        "mon",
        "ma",
        "mes",
        "ce",
        "cet",
        "cette",
        "et",
        "ou",
        "que",
        "qui",
        "quoi",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "en",
        "au",
        "aux",
        "pour",
        "par",
        "sur",
        "dans",
        "ca",
        "c",
        "s",
        "qu",
        "est-ce",
        # product-generic words that appear in nearly every question and discriminate nothing
        "formation",
        "training",
        "course",
        "cours",
    ]
)


def _fold(w: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


_STOP_FOLDED = frozenset(_stem(_fold(w)) for w in _STOP)


def _norm(text: str) -> str:
    """Lowercase, drop punctuation and stopwords so shared function words do not inflate scores."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    words = [_stem(w) for w in re.findall(r"\w+", folded)]
    kept = [w for w in words if w not in _STOP_FOLDED]
    return " ".join(kept or words)


class QADraft(BaseModel):
    question: str
    variants: list[str] = Field(default_factory=list)
    answer: str


class QADraftList(BaseModel):
    entries: list[QADraft]


# ---------------------------------------------------------------- generation
def _mock_entries(p: Product) -> list[dict]:
    pol = p.policies
    plans = " / ".join(f"{pl.name}: {pl.price:g} {p.currency}" for pl in p.plans)
    feats = ", ".join(p.features)
    aud = " and ".join(p.audience)
    spec = [
        (
            "How much does it cost?",
            [
                "What is the price?",
                "Quel est le prix ?",
                "Combien ca coute ?",
                "Combien coute la formation ?",
                "What are the fees and tuition?",
                "Tarif de la formation",
                "Quel budget prevoir ?",
            ],
            f"{plans}.",
        ),
        (
            "What is included in the program?",
            [
                "What features do I get?",
                "Qu'est-ce qui est inclus ?",
                "What will I learn?",
                "What is the curriculum and syllabus?",
                "What topics are covered?",
                "Quel est le programme de la formation ?",
                "Qu'est-ce qu'on apprend ?",
                "Quel est le contenu du cours ?",
                "Is there hands-on practice and projects?",
                "Les sessions sont-elles enregistrees ?",
            ],
            f"{p.name} includes: {feats}.",
        ),
        (
            "When do the sessions take place?",
            [
                "What is the schedule?",
                "Quels sont les horaires ?",
                "What time are the classes?",
                "When does the course start and how long is it?",
                "Quand ont lieu les cours ?",
                "Quelle est la duree de la formation ?",
                "Calendrier et dates des sessions",
                "Combien de temps dure la formation ?",
                "Combien de semaines ?",
                "How many weeks does it last?",
            ],
            pol.schedule,
        ),
        (
            "Is there a refund policy?",
            [
                "Can I get my money back?",
                "Quelle est la politique de remboursement ?",
                "Can I cancel and be reimbursed?",
                "Puis-je etre rembourse ?",
                "Annulation et remboursement",
            ],
            pol.refund,
        ),
        (
            "Do I need prior experience?",
            [
                "What are the prerequisites?",
                "Faut-il des prerequis ?",
                "What level is required to join?",
                "Is it suitable for beginners?",
                "Quel niveau faut-il avoir ?",
                "Do I need previous experience or skills?",
                "Faut-il des connaissances de base ?",
                "Faut-il savoir programmer ou connaitre Python ?",
                "Est-ce adapte aux debutants ?",
            ],
            pol.prerequisites,
        ),
        (
            "Do I get a certificate?",
            [
                "Is there a certificate?",
                "Y a-t-il un certificat ?",
                "Do I receive a diploma on completion?",
                "Is there an attestation of completion?",
                "Recevrai-je une attestation ou un diplome ?",
                "Certification a la fin de la formation",
            ],
            pol.certificate,
        ),
        (
            "Who is this program for?",
            [
                "Who can join?",
                "A qui s'adresse la formation ?",
                "Who is the target audience?",
                "Is it for students or developers?",
                "Is it open to beginners and students?",
                "Pour qui est cette formation ?",
                "Quel est le public cible ?",
            ],
            f"It is designed for {aud}.",
        ),
        (
            "What is the difference between Standard and Pro?",
            [
                "Standard vs Pro?",
                "Quelle est la difference entre Standard et Pro ?",
                "What offers and plans are available?",
                "Is there a mentoring option?",
                "Quelles sont les offres et formules ?",
                "Quelle formule choisir ?",
            ],
            f"Available plans: {plans}.",
        ),
        (
            "How can I contact you?",
            [
                "What is your contact email?",
                "Comment vous contacter ?",
                "How do I reach someone?",
                "Where can I send a question to the team?",
                "Quelle est votre adresse email ?",
                "Comment vous joindre ?",
            ],
            f"Write to {p.contact}.",
        ),
    ]
    out = [{"question": q, "variants": v, "answer": a} for q, v, a in spec if a]
    known = {e["question"].lower() for e in out}
    for q in p.faq_seed:
        if q.lower() not in known:
            out.append(
                {
                    "question": q,
                    "variants": [q.rstrip("?").strip(), f"Question: {q}"],
                    "answer": p.description,
                }
            )
    return out


def generate_qa(product: Product | None = None) -> list[QAEntry]:
    """Return UNSAVED draft entries (use save_drafts to persist)."""
    product = product or load_product()
    system = (
        "You write a customer FAQ for one product. Use only the product JSON. "
        'Reply as JSON {"entries":[{"question","variants","answer"}]} with 2-3 EN/FR variants each.'
    )
    result = chat_json(
        system,
        product.model_dump_json(),
        QADraftList,
        task="qa_generate",
        mock=lambda: {"entries": _mock_entries(product)},
    )
    return [
        QAEntry(
            product_id=product.id,
            question=e.question,
            variants=list(e.variants),
            answer=e.answer,
            status="draft",
        )
        for e in result.entries
    ]


def save_drafts(session: Session, entries: list[QAEntry]) -> list[QAEntry]:
    for e in entries:
        session.add(e)
    session.commit()
    for e in entries:
        session.refresh(e)
    invalidate()
    return entries


# ---------------------------------------------------------------- moderation
def _get(session: Session, entry_id: str) -> QAEntry:
    entry = session.get(QAEntry, entry_id)
    if entry is None:
        raise KeyError(entry_id)
    return entry


def _set_status(session: Session, entry_id: str, status: str) -> QAEntry:
    entry = _get(session, entry_id)
    entry.status = status
    session.add(entry)
    session.commit()
    session.refresh(entry)
    invalidate()
    return entry


def approve(session: Session, entry_id: str) -> QAEntry:
    return _set_status(session, entry_id, "approved")


def reject(session: Session, entry_id: str) -> QAEntry:
    return _set_status(session, entry_id, "rejected")


def update(
    session: Session, entry_id: str, question: str, answer: str, variants: list[str]
) -> QAEntry:
    """Edit an entry; it goes back to draft and needs re-approval."""
    entry = _get(session, entry_id)
    entry.question, entry.answer, entry.variants, entry.status = (
        question,
        answer,
        list(variants),
        "draft",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    invalidate()
    return entry


# ---------------------------------------------------------------- matching
ANSWER_WEIGHT = 0.7  # the answer text also matches, but counts less than a question/variant
AMBIGUITY_MARGIN = 0.05  # top-2 distinct entries closer than this -> no match (safer to refuse)


class Matcher:
    """Scores a query against question + variants (+ answer, lower weight) of approved entries.

    TF-IDF mode takes the max of a char_wb 2-4 gram cosine (typos, morphology) and a word-level
    cosine (exact keywords), both on accent-folded, stopword-free text.
    """

    def __init__(self, entries: list[QAEntry]):
        self.ids: list[str] = []
        self.texts: list[str] = []
        self.weights: list[float] = []
        for e in entries:
            for t in [e.question, *e.variants]:
                self._add(e.id, t, 1.0)
            self._add(e.id, e.answer, ANSWER_WEIGHT)
        self.kind = "tfidf"
        self.threshold = TFIDF_THRESHOLD
        self._embed = None
        if not self.texts:
            return
        if settings.embedding_mode == "local" and self._init_embeddings():
            return
        self._init_tfidf()

    def _add(self, entry_id: str, text: str, weight: float) -> None:
        if text and text.strip():
            self.ids.append(entry_id)
            self.texts.append(text)
            self.weights.append(weight)

    def _init_embeddings(self) -> bool:
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(EMBEDDING_MODEL)
            self._matrix = model.encode(self.texts, normalize_embeddings=True)
            self._embed = lambda q: model.encode([q], normalize_embeddings=True)[0]
            self.kind, self.threshold = "embeddings", settings.qa_similarity_threshold
            return True
        except Exception:  # noqa: BLE001  not installed or model unavailable: silent fallback
            return False

    def _init_tfidf(self) -> None:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        normed = [_norm(t) for t in self.texts]
        self._vecs = [
            TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True),
            TfidfVectorizer(analyzer="word", token_pattern=r"\w+", sublinear_tf=True),
        ]
        self._mats = [v.fit_transform(normed) for v in self._vecs]
        self._w = np.array(self.weights)

    def _tfidf_sims(self, text: str):
        q = _norm(text)
        best = None
        for vec, mat in zip(self._vecs, self._mats, strict=True):
            sims = (mat @ vec.transform([q]).T).toarray().ravel()
            # Out-of-vocabulary n-grams are invisible to the cosine, so a query that is mostly
            # unknown words ("tell me prices of laptops") would score on its one shared word.
            # Scale by the fraction of the query's distinct terms that the corpus knows.
            grams = set(vec.build_analyzer()(q))
            if grams:
                sims = sims * (len(grams & set(vec.vocabulary_)) / len(grams))
            best = sims if best is None else best.clip(min=sims)
        return best * self._w

    def best(self, text: str) -> tuple[str | None, float]:
        if not self.texts or not text.strip():
            return None, 0.0
        if self.kind == "embeddings":
            sims = (self._matrix @ self._embed(text)) * self._w
        else:
            sims = self._tfidf_sims(text)
        per_entry: dict[str, float] = {}
        for eid, sc in zip(self.ids, sims, strict=True):
            per_entry[eid] = max(per_entry.get(eid, 0.0), float(sc))
        ranked = sorted(per_entry.items(), key=lambda kv: -kv[1])
        top_id, top = ranked[0]
        if len(ranked) > 1 and top - ranked[1][1] < AMBIGUITY_MARGIN and top >= self.threshold:
            return None, top  # ambiguous: let the caller fall back / hand off
        return top_id, top


_lock = threading.Lock()
_cache: dict = {"sig": None, "matcher": None}


def invalidate() -> None:
    with _lock:
        _cache["sig"] = None
        _cache["matcher"] = None


def _get_matcher(entries: list[QAEntry]) -> Matcher:
    # The signature guards against edits made outside this module (e.g. seeding).
    sig = (settings.embedding_mode, tuple((e.id, e.question, tuple(e.variants)) for e in entries))
    with _lock:
        if _cache["sig"] != sig:
            _cache["matcher"] = Matcher(entries)
            _cache["sig"] = sig
        return _cache["matcher"]


def match(
    session: Session, text: str, product_id: str | None = None
) -> tuple[QAEntry | None, float]:
    """Best approved entry if its score clears the active threshold, else (None, score)."""
    stmt = select(QAEntry).where(QAEntry.status == "approved")
    if product_id:
        stmt = stmt.where(QAEntry.product_id == product_id)
    entries = list(session.exec(stmt.order_by(QAEntry.id)))
    matcher = _get_matcher(entries)
    entry_id, score = matcher.best(text)
    if entry_id is None or score < matcher.threshold:
        return None, score
    entry = session.get(QAEntry, entry_id)
    entry.hits += 1
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry, score
