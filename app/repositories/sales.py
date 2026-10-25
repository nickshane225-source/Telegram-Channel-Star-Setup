from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from app.db import get_db
from app.models.sale import SaleModel
from app.repositories.base import serialize_doc, to_object_id


class SalesRepository:
    """MongoDB repository for Telegram Stars Sales transactions."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.sales

    async def upsert_transaction(self, sale: SaleModel) -> bool:
        """Upsert transaction by transaction_id to prevent duplicates."""
        sale_dict = sale.model_dump(by_alias=True, exclude_none=True)
        sale_dict.pop("id", None)
        sale_dict.pop("_id", None)
        
        result = await self.collection.update_one(
            {"transaction_id": sale.transaction_id},
            {"$setOnInsert": sale_dict},
            upsert=True
        )
        # Returns True if new document was inserted
        return result.upserted_id is not None

    async def get_total_stars(self) -> int:
        pipeline = [
            {"$group": {"_id": None, "total": {"$sum": "$star_amount"}}}
        ]
        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        return docs[0]["total"] if docs else 0

    async def get_stars_today(self) -> int:
        start_of_today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        pipeline = [
            {"$match": {"date": {"$gte": start_of_today}}},
            {"$group": {"_id": None, "total": {"$sum": "$star_amount"}}}
        ]
        cursor = self.collection.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        return docs[0]["total"] if docs else 0

    async def get_recent_sales(self, limit: int = 50) -> List[SaleModel]:
        cursor = self.collection.find({}).sort("date", -1).limit(limit)
        sales = []
        async for doc in cursor:
            sales.append(SaleModel(**serialize_doc(doc)))
        return sales

    async def get_all_sales_for_export(self) -> List[Dict[str, Any]]:
        cursor = self.collection.find({}).sort("date", -1)
        items = []
        async for doc in cursor:
            doc_clean = serialize_doc(doc)
            items.append({
                "Transaction ID": doc_clean.get("transaction_id", ""),
                "Date (UTC)": doc_clean.get("date", "").strftime("%Y-%m-%d %H:%M:%S") if isinstance(doc_clean.get("date"), datetime) else str(doc_clean.get("date", "")),
                "Stars Amount": doc_clean.get("star_amount", 0),
                "Telegram Message ID": doc_clean.get("telegram_message_id", ""),
                "User ID": doc_clean.get("user_id", ""),
                "User Name": doc_clean.get("user_name", ""),
                "Post ID": doc_clean.get("post_id", "")
            })
        return items

    async def get_chart_data(self, days: int = 30) -> Dict[str, Any]:
        """Aggregate stars earnings per day for the last X days."""
        now = datetime.utcnow()
        start_date = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        pipeline = [
            {"$match": {"date": {"$gte": start_date}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$date"}
                    },
                    "total": {"$sum": "$star_amount"},
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id": 1}}
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
            
        return {
            "labels": labels,
            "values": values,
            "total": sum(values)
        }


sales_repo = SalesRepository()
