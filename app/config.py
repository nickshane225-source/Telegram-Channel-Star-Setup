import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_DIR = BASE_DIR / "media"
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"

# Ensure media directory exists
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Application configuration loaded from environment or .env file."""
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # MongoDB Atlas connection
    MONGODB_URI: str = Field(
        default="mongodb://localhost:27017/starpost",
        description="MongoDB connection string (local or Atlas URI)"
    )
    DB_NAME: str = Field(default="starpost", description="Database name")
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = Field(
        default=20000,
        description="MongoDB server selection timeout in milliseconds"
    )
    MONGODB_CONNECT_TIMEOUT_MS: int = Field(
        default=20000,
        description="MongoDB connection timeout in milliseconds"
    )
    MONGODB_MAX_POOL_SIZE: int = Field(
        default=20,
        description="Maximum MongoDB connection pool size"
    )
    MONGODB_DNS_SERVERS: str = Field(
        default="8.8.8.8,1.1.1.1",
        description="Fallback DNS servers for SRV lookups"
    )
    DB_RECONNECT_INTERVAL_SECONDS: int = Field(
        default=30,
        description="Background reconnect interval in seconds when database is unreachable"
    )

    # Security & Sessions
    SECRET_KEY: str = Field(
        default="starpost-secret-key-change-this-in-production-random-string",
        description="Session signing secret"
    )
    ADMIN_USERNAME: str = Field(default="admin", description="Local login username")
    ADMIN_PASSWORD: str = Field(default="", description="Plain text admin password fallback")
    ADMIN_PASSWORD_HASH: str = Field(
        default="",
        description="Bcrypt hash of admin password (or plain text)"
    )

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Bot token from @BotFather")
    TELEGRAM_CHANNEL_ID: str = Field(
        default="",
        description="Channel username (@channel) or ID (-100...)"
    )

    # Server configuration
    APP_HOST: str = Field(default="127.0.0.1", description="Server listen host")
    APP_PORT: int = Field(default=8000, description="Server listen port")
    DEBUG: bool = Field(default=False, description="Debug mode")

    # User Defaults & System Preferences
    DEFAULT_TIMEZONE: str = Field(default="UTC", description="Default timezone for scheduling")
    DEFAULT_THEME: str = Field(default="midnight", description="Active theme: midnight, neon, clean-light")
    DEFAULT_STAR_PRICE: int = Field(default=50, description="Default Telegram Stars price")
    DEFAULT_CAPTION_FOOTER: str = Field(default="", description="Default footer appended to captions")
    STAR_SYNC_INTERVAL_MINUTES: int = Field(default=15, description="Interval in minutes to sync star transactions")

    @property
    def is_telegram_configured(self) -> bool:
        """Check if both Bot Token and Channel ID are provided."""
        return bool(self.TELEGRAM_BOT_TOKEN.strip() and self.TELEGRAM_CHANNEL_ID.strip())

    @property
    def masked_bot_token(self) -> str:
        """Return masked bot token for display purposes without exposing secrets."""
        token = self.TELEGRAM_BOT_TOKEN.strip()
        if not token:
            return "Not configured"
        if len(token) <= 10:
            return "******"
        return f"{token[:4]}...{token[-4:]}"


settings = Settings()
