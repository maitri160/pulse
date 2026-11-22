from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables (or a .env file).

    Each field maps to an env var of the same name, e.g. DATABASE_URL.
    Defaults point at the Postgres/Redis started by `docker compose up`.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "+psycopg" tells SQLAlchemy to use the psycopg (v3) driver.
    database_url: str = "postgresql+psycopg://pulse:pulse@localhost:5432/pulse"
    redis_url: str = "redis://localhost:6379/0"

    # Scheduling (all in seconds).
    dispatch_interval_seconds: int = 60  # how often Beat looks for due feeds
    fetch_interval_seconds: int = 300  # normal gap between fetches of a healthy feed
    max_backoff_seconds: int = 6 * 60 * 60  # longest wait for a failing feed
    # When a feed is dispatched, its next_fetch_at is pushed this far ahead so the next
    # Beat tick doesn't queue it again. Must be longer than a fetch can take (see task_time_limit).
    fetch_lease_seconds: int = 15 * 60
    task_time_limit_seconds: int = 10 * 60
    embed_interval_seconds: int = 60  # how often Beat asks for new articles to be embedded


settings = Settings()
