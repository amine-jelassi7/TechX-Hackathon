from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from app.db import init_db


def test_existing_sqlite_interaction_table_gets_product_scope():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE interaction (id VARCHAR PRIMARY KEY, session_id VARCHAR, prospect_id VARCHAR, "
                "role VARCHAR, text VARCHAR, source VARCHAR, interest_level VARCHAR, objection VARCHAR, "
                "rating INTEGER, trace_id VARCHAR, ts DATETIME)"
            )
        )
    init_db(engine)
    names = {column["name"] for column in inspect(engine).get_columns("interaction")}
    assert "product_id" in names
