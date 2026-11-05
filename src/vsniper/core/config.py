from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _discover_runtime_root() -> Path:
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "docker-compose.yml").exists():
            return candidate

    fallback = Path(__file__).resolve().parents[3]
    return fallback


class Settings(BaseSettings):
    # Scans Vinted and polls Telegram from this process. Turn off for a second local instance
    # that shares the bot token with the deployed one.
    run_worker: bool = Field(default=True, alias="RUN_WORKER")
    sqlite_path: Path = Field(default=Path("storage/sqlite/vsniper.db"), alias="SQLITE_PATH")
    vinted_region: str = Field(default="de", alias="VINTED_REGION")
    vinted_cookie: str = Field(default="put-your-vinted-cookie-here", alias="VINTED_COOKIE")
    # Empty uses Playwright's own Chromium build; the image points this at Debian's chromium.
    vinted_browser_executable: str = Field(default="", alias="VINTED_BROWSER_EXECUTABLE")
    vinted_browser_proxy_url: str = Field(default="", alias="VINTED_BROWSER_PROXY_URL")
    vinted_browser_timeout_seconds: int = Field(default=30, alias="VINTED_BROWSER_TIMEOUT_SECONDS")
    vinted_browser_profile_dir: Path = Field(
        default=Path("storage/browser-profile"),
        alias="VINTED_BROWSER_PROFILE_DIR",
    )
    telegram_bot_token: str = Field(default="put-your-telegram-bot-token-here", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="put-your-telegram-chat-id-here", alias="TELEGRAM_CHAT_ID")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    openai_api_key: str = Field(default="put-your-openai-api-key-here", alias="OPENAI_API_KEY")
    # Describes reference photos, writes the taste profile and explains alerts.
    openai_vision_model: str = Field(default="gpt-6.1-terra", alias="OPENAI_VISION_MODEL")
    # Decides whether a listing is worth an alert, through the Decisions API.
    openai_decision_model: str = Field(default="gpt-6-luna", alias="OPENAI_DECISION_MODEL")
    upload_dir: Path = Field(default=Path("storage/uploads"), alias="UPLOAD_DIR")
    cache_dir: Path = Field(default=Path("storage/cache"), alias="CACHE_DIR")
    feedback_asset_dir: Path = Field(default=Path("storage/feedback-assets"), alias="FEEDBACK_ASSET_DIR")
    worker_max_concurrency: int = Field(default=4, alias="WORKER_MAX_CONCURRENCY")
    # Fallback/seed value only — the live worker reads AppSettingsState.scan_interval_seconds
    # (editable on the Settings page) once the DB row exists.
    scan_interval_seconds: int = Field(default=1800, alias="SCAN_INTERVAL_SECONDS")
    # Retention: the worker prunes rows older than these (generous) windows so the SQLite file
    # and the stats/queue scans stay bounded. Pending/processing deliveries are never pruned.
    candidate_retention_days: int = Field(default=365, alias="CANDIDATE_RETENTION_DAYS")
    delivery_retention_days: int = Field(default=365, alias="DELIVERY_RETENTION_DAYS")
    ai_usage_retention_days: int = Field(default=365, alias="AI_USAGE_RETENTION_DAYS")
    error_retention_days: int = Field(default=365, alias="ERROR_RETENTION_DAYS")
    # How often (in worker cycles) to run the prune job; ~hourly at a 60s cycle interval.
    prune_every_cycles: int = Field(default=60, alias="PRUNE_EVERY_CYCLES")
    # Comma-separated list of browser origins allowed to call the API. Defaults to the local
    # Vite dev server; set explicitly (e.g. the LAN host) in deployments. "*" disables the lock.
    cors_allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ALLOWED_ORIGINS"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    def resolve_path(self, value: Path) -> Path:
        if value.is_absolute():
            return value
        return _discover_runtime_root() / value

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.resolve_path(self.sqlite_path)}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.resolve_path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    settings.resolve_path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
    settings.resolve_path(settings.feedback_asset_dir).mkdir(parents=True, exist_ok=True)
    settings.resolve_path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    return settings
