from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # DB
    database_url: str = Field(
        default="postgresql+asyncpg://crm:crm_local_dev@postgres:5432/crm"
    )
    sync_database_url: str = Field(
        default="postgresql+psycopg2://crm:crm_local_dev@postgres:5432/crm"
    )

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # LLM
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    mock_llm: bool = True

    # Scraping
    mock_scraper: bool = True
    scraper_min_delay_sec: float = 2.0
    scraper_max_delay_sec: float = 5.0
    scraper_daily_limit: int = 100

    # Modo "control manual": si False, el scheduler NO corre discover/enrich/generate
    # ni el catch-up al arranque - el equipo dispara todo via la UI o /api/jobs/*.
    # Si lo activas, corre el cron diario 09:00 AR + intervalos definidos.
    autostart_jobs: bool = False

    # Alphasoft (datos de contacto que usa el agente en los mensajes)
    alphasoft_email: str = "alphasoftwebs@gmail.com"
    alphasoft_instagram: str = "@alphasoft__"
    alphasoft_website: str = "https://www.alphasoft.cloud/"
    alphasoft_whatsapp: str = ""

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
