"""
Central configuration for PySearch.

All settings are read from environment variables (or a `.env` file in the
`backend/` folder). Nothing secret is hard-coded here.

Why a settings class instead of scattered constants?
- One place to look when you want to change behaviour.
- Pydantic validates types: MAX_PAGES="abc" fails loudly at startup instead
  of crashing halfway through a crawl.
- Tests can build a Settings object with different values (e.g. a temporary
  database) without touching real files.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[0]=app, [1]=backend, [2]=project root
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    APP_NAME: str = "PySearch"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = Field(default="development", pattern="^(development|test|production)$")

    # --- Database ------------------------------------------------------------
    # Relative paths are resolved against the project root, so the DB lands in
    # pysearch/data/ no matter which folder you start uvicorn from.
    DATABASE_PATH: Path = Path("data/pysearch.db")

    # --- API / security ------------------------------------------------------
    # Comma-separated list in .env, e.g. "http://localhost:5173,http://127.0.0.1:5173"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Crawler limits (used from Phase 9; declared now so .env is stable) ---
    MAX_PAGES: int = Field(default=50, ge=1, le=5000)
    MAX_DEPTH: int = Field(default=2, ge=0, le=5)
    REQUEST_DELAY: float = Field(default=1.0, ge=0.2)   # seconds between requests
    REQUEST_TIMEOUT: float = Field(default=10.0, gt=0, le=60)
    ALLOWED_DOMAINS: str = ""                           # comma-separated; empty = start URL's domain only
    MAX_RETRIES: int = Field(default=2, ge=0, le=5)     # extra attempts for timeouts / 5xx / 429
    MAX_CONTENT_BYTES: int = Field(default=5 * 1024 * 1024, ge=10_000)
    USER_AGENT: str = "PySearchBot/0.1 (+private, educational crawler)"
    RESPECT_ROBOTS_TXT: bool = True
    # SSRF protection: by default the crawler refuses private/loopback/link-local
    # addresses (192.168.x.x, 127.0.0.1, 169.254.169.254 ...). Set to true ONLY to
    # crawl a site running on your own machine or LAN.
    ALLOW_PRIVATE_NETWORKS: bool = False

    @field_validator("DATABASE_PATH")
    @classmethod
    def _resolve_db_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_domains_list(self) -> list[str]:
        return [d.strip().lower() for d in self.ALLOWED_DOMAINS.split(",") if d.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings instance (read the environment only once)."""
    return Settings()
