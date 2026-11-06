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
    sqlite_path: Path = Field(default=Path("storage/sqlite/vsniper.db"), alias="SQLITE_PATH")
    telegram_bot_token: str = Field(default="put-your-telegram-bot-token-here", alias="TELEGRAM_BOT_TOKEN")
    # Comma-separated Telegram chat IDs allowed to use the bot; each gets its own profile.
    # The first one also receives error notifications.
    telegram_chat_ids: str = Field(default="", alias="TELEGRAM_CHAT_IDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    openai_api_key: str = Field(default="put-your-openai-api-key-here", alias="OPENAI_API_KEY")
    # Describes reference photos, writes the taste profile and explains alerts.
    openai_vision_model: str = Field(default="gpt-6.1-terra", alias="OPENAI_VISION_MODEL")
    # Decides whether a listing is worth an alert, through the Decisions API.
    openai_decision_model: str = Field(default="gpt-6-luna", alias="OPENAI_DECISION_MODEL")
    upload_dir: Path = Field(default=Path("storage/uploads"), alias="UPLOAD_DIR")
    cache_dir: Path = Field(default=Path("storage/cache"), alias="CACHE_DIR")
    feedback_asset_dir: Path = Field(default=Path("storage/feedback-assets"), alias="FEEDBACK_ASSET_DIR")
    # Comma-separated list of browser origins allowed to call the API. Defaults to the local
    # Vite dev server; set explicitly (e.g. the LAN host) in deployments. "*" disables the lock.
    cors_allowed_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ALLOWED_ORIGINS"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def allowed_chat_ids(self) -> list[str]:
        # Chat IDs are integers; this also drops the placeholder from .env.example.
        return [chat_id for chat_id in (part.strip() for part in self.telegram_chat_ids.split(",")) if chat_id.lstrip("-").isdigit()]

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
