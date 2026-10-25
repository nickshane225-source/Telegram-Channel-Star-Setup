import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

async def main():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.DB_NAME]
    res = await db.settings.update_one(
        {"key": "app_settings"},
        {"$set": {"channel_id": "-1004371128231"}}
    )
    print("Settings updated:", res.modified_count)

    # Also update any failed posts with old channel_id
    res_posts = await db.posts.update_many(
        {"channel_id": "1004371128231"},
        {"$set": {"channel_id": "-1004371128231", "error": None, "status": "draft"}}
    )
    print("Posts reset to draft with fixed channel_id:", res_posts.modified_count)

if __name__ == "__main__":
    asyncio.run(main())
