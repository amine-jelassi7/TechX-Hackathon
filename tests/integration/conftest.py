import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app import llm
from app.config import settings
from app.db import get_session
from app.main import create_app
from app.services import qa

OWNER = {"X-Owner-Token": "test-token"}


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(settings, "embedding_mode", "tfidf")
    monkeypatch.setattr(settings, "owner_token", "test-token")
    monkeypatch.setattr(settings, "payment_approval_threshold", 300.0)
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    qa.invalidate()
    with Session(eng) as s:
        for d in qa.generate_qa(llm.load_product()):
            d.status = "approved"
            s.add(d)
        s.commit()
    qa.invalidate()
    yield eng
    qa.invalidate()


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine):
    app = create_app()

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    llm.reset_counts()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner():
    return OWNER
