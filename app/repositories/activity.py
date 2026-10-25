from datetime import datetime
from typing import Any, Dict, List, Optional
from app.db import get_db
from app.models.activity import ActivityModel
from app.repositories.base import serialize_doc


class ActivityRepository:
    """MongoDB repository for Application Activity Logs."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.activity_log

    async def log(
        self,
        action: str,
        details: str,
        post_id: Optional[str] = None,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None
    ) -> ActivityModel:
        activity = ActivityModel(
            action=action,
            details=details,
            post_id=post_id,
            level=level,
            metadata=metadata or {},
            created_at=datetime.utcnow()
        )
        doc = activity.model_dump(by_alias=True, exclude_none=True)
        doc.pop("id", None)
        doc.pop("_id", None)
        result = await self.collection.insert_one(doc)
        activity.id = str(result.inserted_id)
        return activity

    async def get_recent(self, limit: int = 15) -> List[ActivityModel]:
        cursor = self.collection.find({}).sort("created_at", -1).limit(limit)
        items = []
        async for doc in cursor:
            items.append(ActivityModel(**serialize_doc(doc)))
        return items


activity_repo = ActivityRepository()
