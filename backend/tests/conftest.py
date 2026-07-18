import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from meta_rne.api.app import app


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
