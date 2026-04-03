import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Make app importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import all models so Alembic can see them
from app.database import Base
import app.models  # noqa: F401 — registers TestRun, TestResult, RunEvent with Base

config = context.config
fileConfig(config.config_file_name)
target_metadata = Base.metadata

# Allow DATABASE_URL override via environment variable
db_url = os.environ.get("SYNC_DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline():
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    cfg = config.get_section(config.config_ini_section)
    cfg["sqlalchemy.url"] = db_url
    connectable = engine_from_config(
        cfg,
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
