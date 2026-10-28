import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from pymongo.errors import DuplicateKeyError
from app.db import get_db
from app.models.purchase import PurchaseModel
from app.repositories.base import serialize_doc, to_object_id


class PurchaseRepository:
    """MongoDB repository for Mode B Bot Purchases."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.purchases

    async def record_purchase(
        self,
        user_id: int,
        username: Optional[str],
        post_id: str,
        stars: int,
    ) -> Tuple[bool, PurchaseModel]:
        """
        Record a new purchase idempotently.
        Returns (is_new, purchase_model).
        """
        # Pre-check for existing purchase (ensures idempotency even in mock test environments)
        existing = await self.collection.find_one({"user_id": int(user_id), "post_id": str(post_id)})
        if existing:
            return False, PurchaseModel(**serialize_doc(existing))

        doc = {
            "user_id": int(user_id),
            "username": (username or "").strip() or None,
            "post_id": str(post_id),
            "stars": int(stars),
            "created_at": datetime.utcnow(),
        }

        try:
            result = await self.collection.insert_one(doc)
            doc["_id"] = result.inserted_id
            return True, PurchaseModel(**serialize_doc(doc))
        except DuplicateKeyError:
            # Race condition duplicate
            existing = await self.collection.find_one({"user_id": int(user_id), "post_id": str(post_id)})
            return False, PurchaseModel(**serialize_doc(existing))

    async def has_purchased(self, user_id: int, post_id: str) -> bool:
        """Check if user has already bought the post."""
        doc = await self.collection.find_one({"user_id": int(user_id), "post_id": str(post_id)})
        return doc is not None

    async def get_purchases_by_post(self, post_id: str) -> List[PurchaseModel]:
        cursor = self.collection.find({"post_id": str(post_id)}).sort("created_at", -1)
        purchases = []
        async for doc in cursor:
            purchases.append(PurchaseModel(**serialize_doc(doc)))
        return purchases

    async def count_by_post(self, post_id: str) -> int:
        return await self.collection.count_documents({"post_id": str(post_id)})

    async def list_purchases(
        self,
        post_id: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[PurchaseModel], int]:
        filter_query: Dict[str, Any] = {}
        if post_id:
            filter_query["post_id"] = str(post_id)

        if search:
            # Match username or post_id or user_id
            escaped_search = re.escape(search)
            search_conds: List[Dict[str, Any]] = [
                {"username": {"$regex": escaped_search, "$options": "i"}},
                {"post_id": {"$regex": escaped_search, "$options": "i"}},
            ]
            if search.isdigit():
                search_conds.append({"user_id": int(search)})
            filter_query["$or"] = search_conds

        total = await self.collection.count_documents(filter_query)
        skip = max(0, (page - 1) * page_size)
        cursor = self.collection.find(filter_query).sort("created_at", -1).skip(skip).limit(page_size)

        items = []
        async for doc in cursor:
            items.append(PurchaseModel(**serialize_doc(doc)))
        return items, total

    async def get_total_stars(self) -> int:
        pipeline = [{"$group": {"_id": None, "total": {"$sum": "$stars"}}}]
        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        return docs[0]["total"] if docs else 0

    async def get_stars_today(self) -> int:
        start_of_today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        pipeline = [
            {"$match": {"created_at": {"$gte": start_of_today}}},
            {"$group": {"_id": None, "total": {"$sum": "$stars"}}},
        ]
        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        return docs[0]["total"] if docs else 0

    async def get_chart_data(self, days: int = 30) -> Dict[str, Any]:
        """Aggregate purchases earnings per day for the last X days."""
        now = datetime.utcnow()
        start_date = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

        pipeline = [
            {"$match": {"created_at": {"$gte": start_date}}},
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                    "total": {"$sum": "$stars"},
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        cursor = self.collection.aggregate(pipeline)
        agg_results = {doc["_id"]: doc["total"] async for doc in cursor}

        labels: List[str] = []
        values: List[int] = []

        for i in range(days):
            day_dt = start_date + timedelta(days=i)
            day_str = day_dt.strftime("%Y-%m-%d")
            labels.append(day_dt.strftime("%b %d"))
            values.append(agg_results.get(day_str, 0))

        return {"labels": labels, "values": values, "total": sum(values)}

    async def get_all_for_export(self, post_id: Optional[str] = None) -> List[Dict[str, Any]]:
        filter_query = {"post_id": str(post_id)} if post_id else {}
        cursor = self.collection.find(filter_query).sort("created_at", -1)
        items = []
        async for doc in cursor:
            doc_clean = serialize_doc(doc)
            items.append({
                "Buyer User ID": doc_clean.get("user_id", ""),
                "Username": doc_clean.get("username", "") or "",
                "Post ID": doc_clean.get("post_id", ""),
                "Stars Paid": doc_clean.get("stars", 0),
                "Purchase Date (UTC)": doc_clean.get("created_at", "").strftime("%Y-%m-%d %H:%M:%S")
                if isinstance(doc_clean.get("created_at"), datetime)
                else str(doc_clean.get("created_at", "")),
            })
        return items


purchases_repo = PurchaseRepository()
