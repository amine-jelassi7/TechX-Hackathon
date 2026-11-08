"""Settings loaded from environment (.env) into a simple Pydantic object."""
import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


def _f(name: str, default: str) -> str:
    return os.getenv(name, default)


DEFAULT_OWNER_TOKENS = ("", "change-me")


class Settings(BaseModel):
    llm_mode: str = "mock"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    embedding_mode: str = "tfidf"
    db_url: str = "sqlite:///./data/app.db"
    owner_token: str = "change-me"
    app_env: str = "dev"
    tavily_api_key: str = ""
    hunter_api_key: str = ""

    # Payments / fraud (spec 7.3)
    payment_approval_threshold: float = 300.0
    fraud_w_amount: float = 0.4
    fraud_w_burst: float = 0.4
    fraud_w_declined_then_new: float = 0.3
    fraud_w_mismatch: float = 1.0
    fraud_w_disposable_email: float = 0.2
    fraud_w_same_email_multi_prospect: float = 0.3
    fraud_burst_max_attempts: int = 3
    fraud_burst_window_minutes: int = 10
    fraud_declined_before_new: int = 2
    fraud_medium_threshold: float = 0.3
    fraud_high_threshold: float = 0.6
    disposable_domains: list[str] = [
        "mailinator.test",
        "tempmail.test",
        "throwaway.test",
        "guerrilla.test",
    ]

    # Q&A / gateway / orchestrator
    qa_similarity_threshold: float = 0.72
    input_max_length: int = 1000
    classifier_low: float = 0.3
    classifier_high: float = 0.7
    # Agent handler budget. Must stay above the worst case of one LLM call (app/llm.py: 15 s timeout x 2
    # attempts = 30 s) so live-mode handlers are not abandoned mid-call. Only idempotent handlers are
    # retried (agents declare `idempotent_types`; the real agents write rows, so they run exactly once).
    orchestrator_timeout_s: float = 45.0
    orchestrator_retries: int = 2


def owner_token_is_default(cfg: Settings) -> bool:
    return cfg.owner_token.strip() in DEFAULT_OWNER_TOKENS


def owner_api_enabled(cfg: Settings) -> bool:
    """The owner API refuses everything when the token is empty/default outside dev."""
    return cfg.app_env.strip().lower() == "dev" or not owner_token_is_default(cfg)


def load_settings() -> Settings:
    return Settings(
        llm_mode=_f("LLM_MODE", "mock").split("#")[0].strip(),
        llm_model=_f("LLM_MODEL", "gpt-4o-mini").strip(),
        llm_api_key=_f("LLM_API_KEY", "") or _f("OPENAI_API_KEY", ""),
        embedding_mode=_f("EMBEDDING_MODE", "tfidf").strip(),
        db_url=_f("DB_URL", "sqlite:///./data/app.db").strip(),
        owner_token=_f("OWNER_TOKEN", "change-me"),
        app_env=_f("APP_ENV", "dev"),
        tavily_api_key=_f("TAVILY_API_KEY", "").strip(),
        hunter_api_key=_f("HUNTER_API_KEY", "").strip(),
        payment_approval_threshold=float(_f("PAYMENT_APPROVAL_THRESHOLD", "300")),
    )


settings = load_settings()
