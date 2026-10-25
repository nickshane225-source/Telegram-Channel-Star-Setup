from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId
from app.db import get_db
from app.models.template import TemplateModel
from app.repositories.base import serialize_doc, to_object_id


class TemplateRepository:
    """MongoDB repository for Caption and Price Templates."""

    @property
    def collection(self):
        db = get_db()
        if db is None:
            raise RuntimeError("Database is not connected")
        return db.templates

    async def create(self, template: TemplateModel) -> TemplateModel:
        tmpl_dict = template.model_dump(by_alias=True, exclude_none=True)
        tmpl_dict.pop("id", None)
        tmpl_dict.pop("_id", None)
        tmpl_dict["created_at"] = tmpl_dict.get("created_at") or datetime.utcnow()
        tmpl_dict["updated_at"] = tmpl_dict.get("updated_at") or datetime.utcnow()
        result = await self.collection.insert_one(tmpl_dict)
        template.id = str(result.inserted_id)
        return template

    async def get_by_id(self, template_id: str) -> Optional[TemplateModel]:
        oid = to_object_id(template_id)
        query = {"_id": oid} if oid else {"_id": template_id}
        doc = await self.collection.find_one(query)
        if not doc:
            return None
        return TemplateModel(**serialize_doc(doc))

    async def list_all(self) -> List[TemplateModel]:
        cursor = self.collection.find({}).sort("title", 1)
        templates = []
        async for doc in cursor:
            templates.append(TemplateModel(**serialize_doc(doc)))
        return templates

    async def update(self, template_id: str, update_data: Dict[str, Any]) -> Optional[TemplateModel]:
        oid = to_object_id(template_id)
        query = {"_id": oid} if oid else {"_id": template_id}
        update_data["updated_at"] = datetime.utcnow()
        update_data.pop("_id", None)
        update_data.pop("id", None)
        await self.collection.update_one(query, {"$set": update_data})
        return await self.get_by_id(template_id)

    async def delete(self, template_id: str) -> bool:
        oid = to_object_id(template_id)
        query = {"_id": oid} if oid else {"_id": template_id}
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0


templates_repo = TemplateRepository()
