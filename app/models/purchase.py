from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PurchaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: int
    username: Optional[str] = None
    post_id: str
    stars: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
