from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class SaleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    transaction_id: str
    star_amount: int
    post_id: Optional[str] = None
    telegram_message_id: Optional[int] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    date: datetime
    raw_data: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
