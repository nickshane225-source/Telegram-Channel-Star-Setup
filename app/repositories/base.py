from typing import Any, Dict, Optional
from bson import ObjectId
from app.db import get_db


def to_object_id(id_str: str) -> Optional[ObjectId]:
    """Safely convert string to ObjectId, returning None if invalid."""
    try:
        if ObjectId.is_valid(id_str):
            return ObjectId(id_str)
        return None
    except Exception:
        return None


def serialize_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert MongoDB document ObjectId to string id."""
    if not doc:
        return None
    doc_copy = dict(doc)
    if "_id" in doc_copy:
        doc_copy["id"] = str(doc_copy.pop("_id"))
    return doc_copy
