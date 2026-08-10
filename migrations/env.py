from __future__ import annotations

import os
from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        if explicit.startswith("postgresql://"):
            return explicit.replace("postgresql://", "postgresql+psycopg://", 1)
        return explicit
    host = os.getenv("POSTGRES_HOST", "postgres").strip()
    port = os.getenv("POSTGRES_PORT", "5432").strip()
    database = os.getenv("POSTGRES_DB", "vision").strip()
    user = os.getenv("POSTGRES_USER", "vision").strip()
    password = os.getenv("POSTGRES_PASSWORD", "")
    password_file = os.getenv("POSTGRES_PASSWORD_FILE", "").strip()
    if not password and password_file:
        try:
            password = open(password_file, "r", encoding="utf-8").read().strip()
        except OSError as exc:
            raise RuntimeError(f"Cannot read POSTGRES_PASSWORD_FILE: {password_file}") from exc
    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD, POSTGRES_PASSWORD_FILE, or DATABASE_URL is required for Alembic"
        )
    return (
        "postgresql+psycopg://"
        f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}"
    )


config.set_main_option("sqlalchemy.url", _database_url().replace("%", "%%"))
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
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

