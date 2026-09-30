"""Alembic environment: migrations for the live, serving and monitoring schemas.

The URL comes from DATABASE_URL (environment or .env, through trialpulse.config.Secrets) and is
never printed. A plain postgresql:// URL is switched to the psycopg 3 driver. Offline mode
(`alembic upgrade head --sql`) only needs the dialect, so it works without a database.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from trialpulse.config import Secrets

OFFLINE_URL = "postgresql+psycopg://offline/render-only"

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def database_url() -> str | None:
    secret = Secrets().database_url
    if secret is None:
        return None
    url = secret.get_secret_value()
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def run_offline() -> None:
    context.configure(
        url=database_url() or OFFLINE_URL,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    url = database_url()
    if url is None:
        raise SystemExit(
            "refused: DATABASE_URL is not set; add it to .env (see .env.example for the local "
            "Docker Compose URL)"
        )
    # A short connect timeout turns an unreachable database into a quick, clear error.
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 10})
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
