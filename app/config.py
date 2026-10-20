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


settings = Settings()
