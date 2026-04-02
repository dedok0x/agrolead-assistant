import os

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine, Session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)


def _ensure_lead_columns() -> None:
    inspector = inspect(engine)
    if "lead" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("lead")}
    statements: list[str] = []

    if "source_channel" not in existing:
        statements.append("ALTER TABLE lead ADD COLUMN source_channel VARCHAR(64) DEFAULT 'web'")

    if "raw_dialogue" not in existing:
        statements.append("ALTER TABLE lead ADD COLUMN raw_dialogue TEXT DEFAULT ''")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_lead_columns()


def get_session():
    with Session(engine) as session:
        yield session

