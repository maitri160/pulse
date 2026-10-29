"""Shared test setup.

Tests use a separate `pulse_test` database so they never touch dev data.
"""

import os

# Must happen before any `app` import: app.config reads DATABASE_URL once, at import time.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://pulse:pulse@localhost:5432/pulse_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402 (imports below must come after setting DATABASE_URL)
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import engine  # noqa: E402


def _create_test_database_if_missing() -> None:
    # CREATE DATABASE can't run inside a transaction, so connect to the default
    # `postgres` database in AUTOCOMMIT mode to do it.
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def alembic_config() -> Config:
    return Config("alembic.ini")


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    """Once per test run: start from an empty schema and run all migrations.

    Using the real migrations (not Base.metadata.create_all) means every test run
    also proves the migrations work.
    """
    _create_test_database_if_missing()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    yield


@pytest.fixture
def db_session():
    """A session whose changes are rolled back after each test.

    The outer transaction is never committed. If the test code calls commit(),
    `create_savepoint` turns that into a savepoint inside it, so it is still undone.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()
