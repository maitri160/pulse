"""Alembic entry point: runs migrations against the database in our app settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False so running migrations (e.g. in tests) doesn't
    # silence loggers the app already set up.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Autogenerate compares this (our models) with the live DB to write migrations.
target_metadata = Base.metadata


def run_migrations() -> None:
    # Same DATABASE_URL as the app, so migrations never target a different DB by accident.
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
