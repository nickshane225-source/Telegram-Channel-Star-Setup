from datetime import datetime
from typing import Any, Dict, Optional
from app.config import settings as env_settings
from app.db import get_db
from app.models.setting import SettingModel
from app.repositories.base import serialize_doc


def _normalize_channel(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val).strip()
    if s.startswith("@") or s.startswith("-"):
        return s
    if s.isdigit():
        return f"-{s}" if s.startswith("100") else f"-100{s}"
    return s


class SettingsRepository:
    """MongoDB repository for Application Settings."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.settings

    async def get_settings(self) -> SettingModel:
        """Fetch settings from DB or fall back to .env defaults."""
        try:
            doc = await self.collection.find_one({"key": "app_settings"})
            if doc:
                clean = serialize_doc(doc)
                # If bot_token or channel_id in DB is empty, fill from env
                if not clean.get("bot_token") and env_settings.TELEGRAM_BOT_TOKEN:
                    clean["bot_token"] = env_settings.TELEGRAM_BOT_TOKEN
                if not clean.get("channel_id") and env_settings.TELEGRAM_CHANNEL_ID:
                    clean["channel_id"] = env_settings.TELEGRAM_CHANNEL_ID
                if clean.get("channel_id"):
                    clean["channel_id"] = _normalize_channel(clean["channel_id"])
                return SettingModel(**clean)
        except Exception:
            pass

        # Return defaults populated with env_settings
        return SettingModel(
            key="app_settings",
            bot_token=env_settings.TELEGRAM_BOT_TOKEN,
            channel_id=_normalize_channel(env_settings.TELEGRAM_CHANNEL_ID),
            default_price=env_settings.DEFAULT_STAR_PRICE,
            default_caption_footer=env_settings.DEFAULT_CAPTION_FOOTER,
            timezone=env_settings.DEFAULT_TIMEZONE,
            theme=env_settings.DEFAULT_THEME,
            sync_interval_minutes=env_settings.STAR_SYNC_INTERVAL_MINUTES,
        )

    async def update_settings(self, update_data: Dict[str, Any]) -> SettingModel:
        update_data["updated_at"] = datetime.utcnow()
        update_data.pop("_id", None)
        update_data.pop("id", None)
        update_data["key"] = "app_settings"

        await self.collection.update_one(
            {"key": "app_settings"},
            {"$set": update_data},
            upsert=True
        )
        return await self.get_settings()


settings_repo = SettingsRepository()
