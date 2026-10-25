from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class SettingModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    key: str = "app_settings"
    bot_token: Optional[str] = None
    channel_id: Optional[str] = None
    default_price: int = Field(default=50, ge=1, le=10000)
    default_caption_footer: str = ""
    timezone: str = "UTC"
    theme: str = "midnight"  # "midnight", "neon", "clean-light"
    sync_interval_minutes: int = Field(default=15, ge=1, le=1440)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
