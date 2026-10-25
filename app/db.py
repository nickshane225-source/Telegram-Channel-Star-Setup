import asyncio
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from loguru import logger
from app.config import settings

client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None
db_connection_error: Optional[str] = None


async def connect_to_db() -> bool:
    """Initialize MongoDB connection and verify server reachability."""
    global client, db, db_connection_error
    try:
        logger.info(f"Connecting to MongoDB at {settings.MONGODB_URI.split('@')[-1]}...")
        # Use a reasonable timeout so startup doesn't hang indefinitely if Atlas IP is not whitelisted
        client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000
        )
        db = client[settings.DB_NAME]
        
        # Ping database to verify connection
        await client.admin.command("ping")
        db_connection_error = None
        logger.info(f"Successfully connected to MongoDB database '{settings.DB_NAME}'")
        return True
    except (ServerSelectionTimeoutError, ConnectionFailure, Exception) as exc:
        db_connection_error = str(exc)
        logger.error(f"Failed to connect to MongoDB: {exc}")
        return False


async def close_db_connection() -> None:
    """Close MongoDB client connection."""
    global client
    if client:
        client.close()
        logger.info("MongoDB connection closed.")


def get_db() -> Optional[AsyncIOMotorDatabase]:
    """Return database instance or None if not connected."""
    return db


def get_db_error() -> Optional[str]:
    """Return connection error string if any."""
    return db_connection_error


async def init_indexes() -> None:
    """Create essential indexes for performance and constraints."""
    if db is None:
        return
    try:
        # Posts collection indexes
        await db.posts.create_index([("status", 1), ("scheduled_at", 1)])
        await db.posts.create_index([("telegram_message_id", 1)])
        await db.posts.create_index([("deleted_at", 1)])
        await db.posts.create_index([("created_at", -1)])

        # Sales collection indexes
        await db.sales.create_index([("date", -1)])
        await db.sales.create_index([("transaction_id", 1)], unique=True)
        await db.sales.create_index([("telegram_message_id", 1)])

        # Purchases collection indexes (Mode B Bot purchases)
        await db.purchases.create_index([("user_id", 1), ("post_id", 1)], unique=True)
        await db.purchases.create_index([("post_id", 1)])
        await db.purchases.create_index([("created_at", -1)])

        # Bot listener state index
        await db.bot_state.create_index([("key", 1)], unique=True)

        # Templates collection indexes
        await db.templates.create_index([("title", 1)])

        # Feature flags index
        await db.feature_flags.create_index([("key", 1)], unique=True)

        # Activity log index
        await db.activity_log.create_index([("created_at", -1)])

        # Settings index
        await db.settings.create_index([("key", 1)], unique=True)

        logger.info("MongoDB indexes verified successfully.")
    except Exception as exc:
        logger.warning(f"Could not initialize all MongoDB indexes: {exc}")
