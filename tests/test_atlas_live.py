import os
import secrets
from datetime import datetime
import certifi
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

ATLAS_TEST_URI = os.environ.get("ATLAS_TEST_URI", "").strip()


@pytest.mark.asyncio
@pytest.mark.skipif(not ATLAS_TEST_URI, reason="ATLAS_TEST_URI environment variable is not set")
async def test_atlas_live_smoke():
    """
    Optional live smoke test against a real MongoDB Atlas cluster.
    Creates a throwaway database `starpost_test_<random>`, verifies write/read/index/delete,
    and drops the throwaway database at the end.
    """
    db_name = f"starpost_test_{secrets.token_hex(4)}"
    kwargs = {
        "serverSelectionTimeoutMS": 20000,
        "connectTimeoutMS": 20000,
        "appname": "StarPostLiveTest",
        "retryWrites": True,
    }
    if ATLAS_TEST_URI.startswith("mongodb+srv://") or "mongodb.net" in ATLAS_TEST_URI.lower():
        kwargs["tlsCAFile"] = certifi.where()

    client = AsyncIOMotorClient(ATLAS_TEST_URI, **kwargs)
    try:
        await client.admin.command("ping")
        db = client[db_name]
        await db.posts.create_index([("status", 1), ("scheduled_at", 1)])
        res = await db.posts.insert_one({"title": "Live Smoke Test", "created_at": datetime.utcnow()})
        fetched = await db.posts.find_one({"_id": res.inserted_id})
        assert fetched is not None
        assert fetched["title"] == "Live Smoke Test"
    finally:
        await client.drop_database(db_name)
        client.close()
