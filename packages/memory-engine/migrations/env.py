import os
from logging.config import fileConfig
from dotenv import load_dotenv

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

load_dotenv()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None


def get_url() -> str:
    is_test = os.getenv("LUMEN_ENV") == "test"
    if is_test:
        env_url = os.getenv("LUMEN_TEST_DATABASE_URL")
        if env_url:
            return env_url
        db_name = "lumen_memory_test"
    else:
        env_url = os.getenv("LUMEN_DATABASE_URL") or os.getenv("DATABASE_URL")
        if env_url:
            if env_url.startswith("postgres://"):
                env_url = env_url.replace("postgres://", "postgresql://", 1)
            elif env_url.startswith("postgresql+asyncpg://"):
                env_url = env_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            return env_url
        db_name = os.getenv("POSTGRES_DB", "lumen_memory")

    pg_user = os.getenv("POSTGRES_USER", "lumen")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "")
    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{db_name}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
