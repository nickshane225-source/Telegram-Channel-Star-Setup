from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from bson import ObjectId
from app.db import get_db
from app.models.post import PostModel, PostStatus
from app.repositories.base import serialize_doc, to_object_id


class PostRepository:
    """MongoDB repository for Post collection."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.posts

    async def create(self, post: PostModel) -> PostModel:
        post_dict = post.model_dump(by_alias=True, exclude_none=True)
        if "id" in post_dict and not post_dict["id"]:
            del post_dict["id"]
        if "_id" in post_dict and not post_dict["_id"]:
            del post_dict["_id"]
            
        post_dict["created_at"] = post_dict.get("created_at") or datetime.utcnow()
        post_dict["updated_at"] = post_dict.get("updated_at") or datetime.utcnow()
        
        result = await self.collection.insert_one(post_dict)
        post.id = str(result.inserted_id)
        return post

    async def get_by_id(self, post_id: str) -> Optional[PostModel]:
        oid = to_object_id(post_id)
        query = {"_id": oid} if oid else {"_id": post_id}
        doc = await self.collection.find_one(query)
        if not doc:
            return None
        return PostModel(**serialize_doc(doc))

    async def get_by_telegram_message_id(self, message_id: int) -> Optional[PostModel]:
        doc = await self.collection.find_one({"telegram_message_id": message_id})
        if not doc:
            return None
        return PostModel(**serialize_doc(doc))

    async def update(self, post_id: str, update_data: Dict[str, Any]) -> Optional[PostModel]:
        oid = to_object_id(post_id)
        query = {"_id": oid} if oid else {"_id": post_id}
        
        update_data["updated_at"] = datetime.utcnow()
        # Remove any _id or id from update fields
        update_data.pop("_id", None)
        update_data.pop("id", None)
        
        await self.collection.update_one(query, {"$set": update_data})
        return await self.get_by_id(post_id)

    async def list_posts(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        hidden_mode: Optional[str] = None,
        page: int = 1,
        page_size: int = 12,
        sort_by: str = "created_at",
        sort_desc: bool = True,
        include_deleted: bool = False,
    ) -> Tuple[List[PostModel], int]:
        filter_query: Dict[str, Any] = {}

        if not include_deleted:
            filter_query["deleted_at"] = None
        elif status == "deleted":
            filter_query["deleted_at"] = {"$ne": None}

        if status and status != "all" and status != "deleted":
            filter_query["status"] = status

        if hidden_mode and hidden_mode != "all":
            filter_query["hidden_mode"] = hidden_mode

        if search:
            filter_query["$or"] = [
                {"title": {"$regex": search, "$options": "i"}},
                {"caption": {"$regex": search, "$options": "i"}},
            ]

        total = await self.collection.count_documents(filter_query)
        sort_direction = -1 if sort_desc else 1
        skip = max(0, (page - 1) * page_size)

        cursor = (
            self.collection.find(filter_query)
            .sort(sort_by, sort_direction)
            .skip(skip)
            .limit(page_size)
        )
        posts: List[PostModel] = []
        async for doc in cursor:
            posts.append(PostModel(**serialize_doc(doc)))

        return posts, total

    async def soft_delete(self, post_id: str) -> bool:
        oid = to_object_id(post_id)
        query = {"_id": oid} if oid else {"_id": post_id}
        result = await self.collection.update_one(
            query,
            {"$set": {"status": PostStatus.DELETED.value, "deleted_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    async def restore(self, post_id: str) -> bool:
        oid = to_object_id(post_id)
        query = {"_id": oid} if oid else {"_id": post_id}
        result = await self.collection.update_one(
            query,
            {"$set": {"status": PostStatus.DRAFT.value, "deleted_at": None, "updated_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    async def permanent_delete(self, post_id: str) -> bool:
        oid = to_object_id(post_id)
        query = {"_id": oid} if oid else {"_id": post_id}
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0

    async def get_due_scheduled_posts(self, now: datetime) -> List[PostModel]:
        cursor = self.collection.find({
            "status": PostStatus.SCHEDULED.value,
            "scheduled_at": {"$lte": now},
            "deleted_at": None
        }).sort("scheduled_at", 1)
        
        posts = []
        async for doc in cursor:
            posts.append(PostModel(**serialize_doc(doc)))
        return posts

    async def get_all_scheduled(self) -> List[PostModel]:
        cursor = self.collection.find({
            "status": PostStatus.SCHEDULED.value,
            "deleted_at": None
        }).sort("scheduled_at", 1)
        
        posts = []
        async for doc in cursor:
            posts.append(PostModel(**serialize_doc(doc)))
        return posts

    async def get_next_scheduled(self, limit: int = 5) -> List[PostModel]:
        cursor = self.collection.find({
            "status": PostStatus.SCHEDULED.value,
            "deleted_at": None
        }).sort("scheduled_at", 1).limit(limit)
        
        posts = []
        async for doc in cursor:
            posts.append(PostModel(**serialize_doc(doc)))
        return posts

    async def get_dashboard_counts(self) -> Dict[str, int]:
        counts = {
            "total": await self.collection.count_documents({"deleted_at": None}),
            "published": await self.collection.count_documents({"status": PostStatus.PUBLISHED.value, "deleted_at": None}),
            "scheduled": await self.collection.count_documents({"status": PostStatus.SCHEDULED.value, "deleted_at": None}),
            "draft": await self.collection.count_documents({"status": PostStatus.DRAFT.value, "deleted_at": None}),
            "failed": await self.collection.count_documents({"status": PostStatus.FAILED.value, "deleted_at": None}),
            "deleted": await self.collection.count_documents({"deleted_at": {"$ne": None}}),
        }
        return counts

    async def update_stars_earned(self, post_id: str, additional_stars: int) -> None:
        oid = to_object_id(post_id)
        query = {"_id": oid} if oid else {"_id": post_id}
        await self.collection.update_one(query, {"$inc": {"stars_earned": additional_stars}})


posts_repo = PostRepository()
