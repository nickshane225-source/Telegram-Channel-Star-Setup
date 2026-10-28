from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class PostStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    DELETED = "deleted"


class PostPhoto(BaseModel):
    path: str
    file_id: Optional[str] = None
    original_filename: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    is_text_card: bool = False
    source_type: Optional[str] = "photo"


class PostModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    title: str = Field(..., min_length=1, max_length=200)
    caption: str = Field(default="", max_length=1024)
    photos: List[PostPhoto] = Field(default_factory=list)
    star_count: int = Field(default=50, ge=1, le=10000)
    status: PostStatus = Field(default=PostStatus.DRAFT)
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    telegram_message_id: Optional[int] = None
    teaser_message_id: Optional[int] = None
    channel_id: Optional[str] = None
    retry_count: int = Field(default=0, ge=0)
    error: Optional[str] = None
    stars_earned: int = Field(default=0, ge=0)
    hidden_text: str = Field(default="", max_length=1000)
    hidden_mode: str = Field(default="off")  # "off" | "card" | "bot"
    card_style: str = Field(default="blurred photo background")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None
    publish_lock_until: Optional[datetime] = None

    @property
    def real_photos(self) -> List[PostPhoto]:
        """Return only genuine user photos, excluding auto-generated text cards."""
        return [p for p in self.photos if not p.is_text_card]

    @property
    def has_hidden_content(self) -> bool:
        """Check if post has active hidden content."""
        return self.hidden_mode in ("card", "bot") and bool(self.hidden_text.strip())

    @property
    def is_editable(self) -> bool:
        """Draft and scheduled posts are fully editable; published posts only support caption editing (or bot mode price/text)."""
        return self.status in [PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.FAILED]

    @property
    def is_published(self) -> bool:
        return self.status == PostStatus.PUBLISHED
