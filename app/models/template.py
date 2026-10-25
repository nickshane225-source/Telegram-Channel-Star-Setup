from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class TemplateModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    title: str = Field(..., min_length=1, max_length=120)
    caption: str = Field(default="", max_length=1024)
    star_count: int = Field(default=50, ge=1, le=10000)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
