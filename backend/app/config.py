from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://uitest:changeme@db:5432/uitestplatform"
    sync_database_url: str = "postgresql+psycopg2://uitest:changeme@db:5432/uitestplatform"

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # File storage
    upload_dir: str = "/app/uploads"
    results_dir: str = "/app/results"

    # Limits
    max_upload_size_bytes: int = 10 * 1024 * 1024   # 10 MB
    max_test_cases: int = 500
    playwright_timeout_ms: int = 25_000
    run_timeout_seconds: int = 600                   # 10 min hard limit per run

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
