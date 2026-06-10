from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = "sqlite:///./cis_demo.db"


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from app import db_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    column_plan = {
        "tasks": {
            "collection_strategy_mode": "VARCHAR NOT NULL DEFAULT 'balanced'",
            "is_highlighted": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "traces": {"run_id": "VARCHAR"},
        "evidence": {"run_id": "VARCHAR"},
        "reports": {"run_id": "VARCHAR"},
        "qa_results": {"run_id": "VARCHAR"},
    }
    with engine.begin() as connection:
        for table, columns in column_plan.items():
            if table not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table)}
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
