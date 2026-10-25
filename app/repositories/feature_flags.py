from datetime import datetime
from typing import Any, Dict, List, Optional
from app.db import get_db
from app.models.feature_flag import FeatureFlagModel
from app.repositories.base import serialize_doc


class FeatureFlagsRepository:
    """MongoDB repository for Application Feature Flags and Plugins."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.feature_flags

    async def get_all(self) -> List[FeatureFlagModel]:
        cursor = self.collection.find({}).sort("name", 1)
        flags = []
        async for doc in cursor:
            flags.append(FeatureFlagModel(**serialize_doc(doc)))
        return flags

    async def is_enabled(self, key: str, default: bool = False) -> bool:
        try:
            doc = await self.collection.find_one({"key": key})
            if doc:
                return bool(doc.get("enabled", False))
            return default
        except Exception:
            return default

    async def register_flag(
        self,
        key: str,
        name: str,
        description: str = "",
        default_enabled: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> FeatureFlagModel:
        existing = await self.collection.find_one({"key": key})
        if not existing:
            flag = FeatureFlagModel(
                key=key,
                name=name,
                description=description,
                enabled=default_enabled,
                metadata=metadata or {},
                updated_at=datetime.utcnow()
            )
            doc = flag.model_dump(by_alias=True, exclude_none=True)
            doc.pop("id", None)
            doc.pop("_id", None)
            result = await self.collection.insert_one(doc)
            flag.id = str(result.inserted_id)
            return flag
        return FeatureFlagModel(**serialize_doc(existing))

    async def set_enabled(self, key: str, enabled: bool) -> bool:
        result = await self.collection.update_one(
            {"key": key},
            {"$set": {"enabled": enabled, "updated_at": datetime.utcnow()}}
        )
        return result.modified_count > 0


feature_flags_repo = FeatureFlagsRepository()
