import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import llm
from app.config import owner_api_enabled, owner_token_is_default, settings
from app.db import init_db
from app.routers import owner, public

log = logging.getLogger(__name__)


def check_startup() -> None:
    """Log configuration problems once at startup (never the secrets themselves)."""
    if owner_token_is_default(settings):
        if owner_api_enabled(settings):
            log.warning(
                "OWNER_TOKEN is empty or the default 'change-me' (APP_ENV=dev): fine for a local demo, "
                "set OWNER_TOKEN before showing this to anyone."
            )
        else:
            log.error(
                "OWNER_TOKEN is empty or the default 'change-me' and APP_ENV=%s: the owner API is "
                "DISABLED (401 for every call). Set OWNER_TOKEN.",
                settings.app_env,
            )
    llm.check_live_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_startup()
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="SoukSaaS", lifespan=lifespan)

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'",
        )
        return response

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(public.router)
    application.include_router(owner.router)
    return application


app = create_app()
