"""Engine, session helper and table creation."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = (
        {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    )
    return create_engine(url, **kwargs)


engine = make_engine(settings.db_url)


def init_db(eng: Engine | None = None) -> None:
    from app import models  # noqa: F401  (register tables)

    target = eng or engine
    # Add product scope to tables that existed before the owner SaaS flow. Run this before create_all
    # because SQLAlchemy may create indexes on the new column when it sees the old table.
    if target.dialect.name == "sqlite":
        inspector = inspect(target)
        for table in ("interaction", "payment", "qaentry", "approvalrequest"):
            if table in inspector.get_table_names() and "product_id" not in {
                column["name"] for column in inspector.get_columns(table)
            }:
                with target.begin() as conn:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table} ADD COLUMN product_id VARCHAR NOT NULL DEFAULT 'prod_ai_bootcamp'"
                        )
                    )
                inspector = inspect(target)
    SQLModel.metadata.create_all(target)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
