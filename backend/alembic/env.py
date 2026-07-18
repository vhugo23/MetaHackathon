import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The connection string comes from the environment (DATABASE_URL), not a
# hardcoded value in alembic.ini — the same variable the application uses
# (docker-compose.yml, README.md). This is what proves Alembic can reach
# the real PostgreSQL instance at container-startup time (architecture.md
# Section 11.2), not just that alembic.ini parses.
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Day 4B1 adds the first persisted models (persistence/sqlalchemy/models.py).
# ``target_metadata`` is wired to the private ORM models' MetaData for
# schema consistency and future ``alembic revision --autogenerate`` support
# — every migration in this project remains hand-written (architecture.md
# Section 11.2: never `Base.metadata.create_all()`), this only lets Alembic
# compare the migrated schema against the ORM models if asked to.
from meta_rne.persistence.sqlalchemy.models import metadata as target_metadata  # noqa: E402

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
