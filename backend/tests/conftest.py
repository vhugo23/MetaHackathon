import os
from collections.abc import Generator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from meta_rne.api.app import app

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# --- PostgreSQL test-database fixtures (Day 4B1) -----------------------
#
# Two separate databases on the same running instance (docker-compose's
# `db` service by default — no testcontainers):
#
# - meta_rne_test           — reserved for Day 4B2/4B3 repository
#                              conformance tests (not exercised by any
#                              Day 4B1 test).
# - meta_rne_migration_test — used exclusively by
#                              tests/integration/persistence/test_migrations.py,
#                              reset to Alembic base before every test
#                              (function-scoped), so migration tests never
#                              depend on execution order and never touch
#                              the development database `meta_rne`.
#
# All three connection strings are overridable via environment variables
# so CI can point at its own service container.

_DEFAULT_ADMIN_DATABASE_URL = "postgresql+psycopg://meta_rne:meta_rne@localhost:5432/meta_rne"
_DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://meta_rne:meta_rne@localhost:5432/meta_rne_test"
_DEFAULT_MIGRATION_TEST_DATABASE_URL = (
    "postgresql+psycopg://meta_rne:meta_rne@localhost:5432/meta_rne_migration_test"
)


def _psycopg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://")


def _database_name(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.rsplit("/", 1)[-1]


@pytest.fixture(scope="session")
def postgres_test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def postgres_migration_database_url() -> str:
    return os.environ.get("MIGRATION_TEST_DATABASE_URL", _DEFAULT_MIGRATION_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def _postgres_test_databases_created(
    postgres_test_database_url: str, postgres_migration_database_url: str
) -> None:
    """Creates meta_rne_test and meta_rne_migration_test if they don't
    already exist. CREATE DATABASE cannot run inside a transaction, so this
    uses an autocommit connection to the admin (development) database —
    meta_rne itself is only ever connected to here, never dropped or
    recreated."""
    admin_dsn = _psycopg_dsn(os.environ.get("ADMIN_DATABASE_URL", _DEFAULT_ADMIN_DATABASE_URL))
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        for url in (postgres_test_database_url, postgres_migration_database_url):
            db_name = _database_name(url)
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                exists = cur.fetchone() is not None
            if not exists:
                with conn.cursor() as cur:
                    cur.execute(f'CREATE DATABASE "{db_name}"')


@pytest.fixture()
def reset_migration_database(
    _postgres_test_databases_created: None, postgres_migration_database_url: str
) -> str:
    """Function-scoped: drops and recreates the `public` schema in
    meta_rne_migration_test, leaving that database at Alembic base before
    the test begins. Each migration test then independently performs the
    upgrade/downgrade operations it needs — no migration test may rely on
    a previous test having already migrated the database (Day 4B1 binding
    decision). Returns the migration database's connection URL."""
    dsn = _psycopg_dsn(postgres_migration_database_url)
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
    return postgres_migration_database_url


# --- Repository test fixtures (Day 4B2) ---------------------------------
#
# meta_rne_test is migrated once per test session (never per test — that
# would be wasteful and is not what "migrate once" means here), then every
# repository test gets its own connection + outer transaction + Session,
# unconditionally rolled back and closed in teardown. Repository-internal
# SAVEPOINTs (persistence/sqlalchemy/*_repository.py) nest inside this
# outer transaction without disturbing it. This never touches
# meta_rne_migration_test, which is reserved for test_migrations.py.


@pytest.fixture(scope="session")
def _meta_rne_test_migrated(
    _postgres_test_databases_created: None, postgres_test_database_url: str
) -> None:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", postgres_test_database_url)
    command.upgrade(config, "head")


@pytest.fixture()
def sqlalchemy_session(
    _meta_rne_test_migrated: None, postgres_test_database_url: str
) -> Generator[Session, None, None]:
    engine = create_engine(postgres_test_database_url)
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()
        engine.dispose()
