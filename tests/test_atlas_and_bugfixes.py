import asyncio
from datetime import datetime
from typing import Any, Dict, List
from bson import ObjectId
import mongomock
import mongomock_motor
import pytest
from pymongo.errors import ConfigurationError, OperationFailure, ServerSelectionTimeoutError
from starlette.testclient import TestClient

import app.db as app_db
import app.main as app_main
from app.config import settings
from app.db import (
    classify_db_error,
    connect_to_db,
    get_db,
    get_db_error,
    init_indexes,
    mask_mongodb_uri,
    mask_secrets_in_text,
    validate_mongodb_uri,
)
from app.main import app, start_services
from app.models.post import PostModel, PostStatus
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.services.auth import auth_service
from app.services.bot_listener import BotListenerService
from app.services.posts import PostService
from scripts.migrate_local_to_atlas import run_migration


# ---------------------------------------------------------------------------
# 1. URI validation & credential masking
# ---------------------------------------------------------------------------
def test_uri_validation_and_masking():
    """Verify URI validator catches placeholders, unescaped special chars, missing srv username, and empty URIs."""
    # Empty URI
    ok, err = validate_mongodb_uri("")
    assert ok is False
    assert "empty" in err.lower()

    # Placeholder <password> or <db_password>
    ok, err = validate_mongodb_uri("mongodb+srv://admin:<password>@cluster0.abc.mongodb.net/starpost")
    assert ok is False
    assert "placeholder" in err.lower()

    ok, err = validate_mongodb_uri("mongodb+srv://admin:<db_password>@cluster0.abc.mongodb.net/starpost")
    assert ok is False
    assert "placeholder" in err.lower()

    # mongodb+srv without username
    ok, err = validate_mongodb_uri("mongodb+srv://cluster0.abc.mongodb.net/starpost")
    assert ok is False
    assert "username" in err.lower()

    # Unescaped special characters in password (@ : / ? # [ ] %)
    for bad_char in ["@", ":", "/", "?", "#", "[", "]", "%"]:
        bad_uri = f"mongodb+srv://myuser:p{bad_char}ssword@cluster0.abc.mongodb.net/starpost"
        ok, err = validate_mongodb_uri(bad_uri)
        assert ok is False, f"Expected failure for unescaped '{bad_char}'"
        assert "unescaped" in err.lower() or "url-encode" in err.lower()

    # Properly URL-encoded password succeeds
    ok, err = validate_mongodb_uri("mongodb+srv://myuser:p%40ss%3Aword%231@cluster0.abc.mongodb.net/starpost")
    assert ok is True
    assert err is None

    # Credential masking
    raw_uri = "mongodb+srv://star_user:SuperSecretPass123@cluster0.xyz.mongodb.net/starpost?retryWrites=true"
    masked = mask_mongodb_uri(raw_uri)
    assert "SuperSecretPass123" not in masked
    assert masked.startswith("mongodb+srv://star_user:***@cluster0.xyz.mongodb.net")

    err_with_secret = f"Connection failed to {raw_uri} with password SuperSecretPass123"
    cleaned = mask_secrets_in_text(err_with_secret, raw_uri)
    assert "SuperSecretPass123" not in cleaned
    assert "star_user:***@cluster0.xyz.mongodb.net" in cleaned


# ---------------------------------------------------------------------------
# 2. Failed connect leaves get_db() None + Degraded Mode 503 Middleware
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_connect_leaves_db_none_and_returns_503(monkeypatch):
    """
    Verify:
    - When ping fails during connect_to_db, global db remains None (critical bug fix).
    - Dashboard HTML route returns 503 error_db.html.
    - /api/* routes return 503 JSON {"detail": ...}.
    - /auth/login remains accessible (200) even when db is None.
    """
    closed_clients = []

    class FailingMotorClient:
        def __init__(self, *args, **kwargs):
            self.admin = self

        def __getitem__(self, name):
            return {"name": name}

        async def command(self, cmd):
            raise ServerSelectionTimeoutError("No replica set members available (IP not whitelisted)")

        def close(self):
            closed_clients.append(True)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(app_db, "AsyncIOMotorClient", FailingMotorClient)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(settings, "MONGODB_URI", "mongodb+srv://admin:encodedpass@cluster0.mongodb.net/starpost")

    # Simulate initial disconnected state
    app_db.db = None
    app_db.client = None

    ok = await connect_to_db(max_attempts=2)
    assert ok is False
    assert get_db() is None
    assert len(closed_clients) >= 2
    assert get_db_error() is not None
    assert "Network Access" in get_db_error()

    # Test HTTP responses in degraded mode
    client = TestClient(app)
    token = auth_service.create_token("admin")
    client.cookies.set("starpost_session", token)

    # Dashboard returns 503 with error_db.html and Retry Connection button
    resp_html = client.get("/")
    assert resp_html.status_code == 503
    assert "MongoDB Atlas Unreachable" in resp_html.text
    assert "/api/db/retry" in resp_html.text

    # Other HTML page (/posts) also returns 503 via middleware instead of 500 RuntimeError
    resp_posts = client.get("/posts")
    assert resp_posts.status_code == 503
    assert "MongoDB Atlas Unreachable" in resp_posts.text

    # API route returns 503 JSON {"detail": ...}
    resp_api = client.get("/api/stats/chart")
    assert resp_api.status_code == 503
    assert "detail" in resp_api.json()

    # /auth/login is exempt and returns 200 when unauthenticated
    anon_client = TestClient(app)
    resp_login = anon_client.get("/auth/login")
    assert resp_login.status_code == 200


# ---------------------------------------------------------------------------
# 3. SRV DNS fallback & error classification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_srv_dns_fallback_on_configuration_error(monkeypatch):
    """Verify that when SRV lookup raises ConfigurationError, fallback DNS resolver is applied and retried."""
    attempts = []
    fallback_called = []

    def fake_apply_fallback():
        fallback_called.append(True)
        return ["8.8.8.8", "1.1.1.1"]

    class SrvRetryClient:
        def __init__(self, *args, **kwargs):
            self.admin = self

        def __getitem__(self, name):
            return mongomock_motor.AsyncMongoMockClient()[name]

        async def command(self, cmd):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                raise ConfigurationError("The DNS query name does not exist: _mongodb._tcp.cluster0.mongodb.net.")
            return {"ok": 1}

        def close(self):
            pass

    monkeypatch.setattr(app_db, "apply_fallback_dns_resolver", fake_apply_fallback)
    monkeypatch.setattr(app_db, "AsyncIOMotorClient", SrvRetryClient)
    monkeypatch.setattr(settings, "MONGODB_URI", "mongodb+srv://user:pass123@cluster0.mongodb.net/starpost")

    ok = await connect_to_db(max_attempts=1)
    assert ok is True
    assert len(fallback_called) == 1
    assert get_db() is not None

    # Also verify DNS error message mentions standard (non-SRV) connection string
    cat, msg = classify_db_error(ConfigurationError("DNS SRV lookup timed out"))
    assert cat == "dns_srv"
    assert "standard (non-SRV)" in msg


# ---------------------------------------------------------------------------
# 4. Reconnect endpoint & start_services idempotency (runs once)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reconnect_starts_services_only_once(monkeypatch):
    """Verify start_services and POST /api/db/retry initialize plugins/scheduler/indexes only once."""
    init_calls = []
    plugin_calls = []
    sched_calls = []

    async def fake_init_indexes():
        init_calls.append(1)

    async def fake_load_plugins(app_inst):
        plugin_calls.append(1)

    async def fake_sched_start():
        sched_calls.append(1)

    monkeypatch.setattr(app_main, "init_indexes", fake_init_indexes)
    monkeypatch.setattr(app_main, "load_plugins", fake_load_plugins)
    monkeypatch.setattr(app_main.schedule_service, "start", fake_sched_start)
    monkeypatch.setattr(app_main, "_services_started", False)
    monkeypatch.setattr(app_main, "_plugins_loaded", False)

    # First call when DB is connected
    started_1 = await start_services(app)
    # Second concurrent/repeated call must be a no-op
    started_2 = await start_services(app)

    assert started_1 is True
    assert started_2 is True
    assert len(init_calls) == 1
    assert len(plugin_calls) == 1
    assert len(sched_calls) == 1

    # Test POST /api/db/retry endpoint with authenticated client
    client = TestClient(app)
    token = auth_service.create_token("admin")
    client.cookies.set("starpost_session", token)
    resp = client.post("/api/db/retry")
    assert resp.status_code == 200
    assert resp.json()["connected"] is True
    assert len(plugin_calls) == 1


# ---------------------------------------------------------------------------
# 5. Regex-safe search in PostRepository and PurchaseRepository
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_regex_safe_search(sample_photos):
    """Searching special regex metacharacters like '(' or '[' must not crash and must match literally."""
    p = await posts_repo.create(
        PostModel(
            title="Special [Bracket] & (Paren) Drop",
            caption="Price $50 (limited) [exclusive]",
            photos=sample_photos,
            star_count=50,
        )
    )
    await purchases_repo.record_purchase(user_id=9001, username="user_(vip)[1]", post_id=p.id, stars=50)

    for special_query in ["(", "[", " [Bracket]", "(Paren)", "user_(vip)", "[1]", ".*+"]:
        posts, total_posts = await posts_repo.list_posts(search=special_query)
        purchases, total_purchases = await purchases_repo.list_purchases(search=special_query)
        assert isinstance(total_posts, int)
        assert isinstance(total_purchases, int)

    matched_posts, count_p = await posts_repo.list_posts(search="[Bracket]")
    assert count_p == 1
    assert matched_posts[0].id == p.id

    matched_buyers, count_b = await purchases_repo.list_purchases(search="user_(vip)")
    assert count_b == 1
    assert matched_buyers[0].user_id == 9001


# ---------------------------------------------------------------------------
# 6. Bot listener offset persistence order & UTC datetime
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bot_listener_offset_not_advanced_on_error():
    """
    Verify:
    - If processing an update raises a database error, the offset is NOT persisted/advanced.
    - When processing succeeds, the offset IS persisted with updated_at as a datetime (UTC).
    """
    calls = []

    class FakeTG:
        async def delete_webhook(self, drop_pending_updates=False):
            return True

        async def get_updates(self, offset=None, timeout=50, allowed_updates=None):
            calls.append(offset)
            if len(calls) == 1:
                return [{"update_id": 500, "message": {"text": "/start p_bad", "from": {"id": 123}}}]
            # Stop the loop on second poll
            listener.is_running = False
            return []

    listener = BotListenerService(tg_service=FakeTG())

    async def failing_process(update):
        raise RuntimeError("Simulated database outage during update processing")

    listener._process_single_update = failing_process

    async def fast_sleep(_):
        listener.is_running = False

    import app.services.bot_listener as bl_mod
    orig_sleep = asyncio.sleep
    asyncio.sleep = fast_sleep
    try:
        listener.is_running = True
        await listener._poll_loop()
    finally:
        asyncio.sleep = orig_sleep

    # Offset must NOT have been persisted because _process_single_update raised
    persisted = await listener.get_persisted_offset()
    assert persisted is None

    # Now test successful save_persisted_offset stores datetime
    await listener.save_persisted_offset(501)
    doc = await get_db().bot_state.find_one({"key": "update_offset"})
    assert doc is not None
    assert doc["offset"] == 501
    assert isinstance(doc["updated_at"], datetime)


# ---------------------------------------------------------------------------
# 7. Atomic publish lock prevents double publish
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_publish_sends_single_telegram_message(sample_photos):
    """Two concurrent publish_post calls on the same post must send exactly one Telegram message."""
    sent_messages = []

    class SlowTelegramService:
        async def send_paid_media(self, chat_id, star_count, photo_paths, caption=None, parse_mode="HTML"):
            await asyncio.sleep(0.05)
            msg_id = 7000 + len(sent_messages) + 1
            sent_messages.append(msg_id)
            return msg_id, ["file_1"]

    tg = SlowTelegramService()
    svc = PostService(repo=posts_repo, tg_service=tg)

    post = await svc.create_post(
        title="Atomic Publish Test",
        caption="No double send",
        photos=sample_photos,
        star_count=50,
        status=PostStatus.SCHEDULED,
        channel_id="-1001234567890",
    )

    # Fire two concurrent publish_post calls
    res1, res2 = await asyncio.gather(
        svc.publish_post(post.id),
        svc.publish_post(post.id),
    )

    assert len(sent_messages) == 1
    final_post = await posts_repo.get_by_id(post.id)
    assert final_post.status == PostStatus.PUBLISHED
    assert final_post.telegram_message_id == 7001
    assert final_post.publish_lock_until is None


# ---------------------------------------------------------------------------
# 8. Per-index failure handling in init_indexes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_init_indexes_continues_when_one_index_fails(monkeypatch):
    """If creating a unique index on `sales` fails due to duplicate data, remaining indexes still get created."""
    created_on = []

    class FakeCollection:
        def __init__(self, name):
            self.name = name

        async def create_index(self, keys, **kwargs):
            if self.name == "sales" and kwargs.get("unique"):
                raise OperationFailure("E11000 duplicate key error collection: starpost.sales index: transaction_id_1")
            created_on.append((self.name, keys, kwargs))
            return "idx_ok"

    class FakeDB:
        def __getattr__(self, name):
            return FakeCollection(name)

    monkeypatch.setattr(app_db, "db", FakeDB())

    # Must not raise, and must still create later indexes (like settings, purchases, etc.)
    await init_indexes()
    assert any(c[0] == "settings" for c in created_on)
    assert any(c[0] == "purchases" for c in created_on)


# ---------------------------------------------------------------------------
# 9. Migration script tests (dry-run, apply, duplicates, same-DB refusal)
# ---------------------------------------------------------------------------
def test_migration_script_dry_run_apply_duplicates_and_same_db_refusal():
    """Verify migration script with mongomock for dry-run, apply, duplicate skipping, and same-database refusal."""
    src_client = mongomock.MongoClient()
    dst_client = mongomock.MongoClient()

    src_db = src_client["starpost"]
    dst_db = dst_client["starpost"]

    # 1. Same database refusal
    with pytest.raises(ValueError, match="same server and database"):
        run_migration(
            source_db=src_db,
            dest_db=src_db,
            source_uri="mongodb://localhost:27017/starpost",
            dest_uri="mongodb://127.0.0.1:27017/starpost",
            source_db_name="starpost",
            dest_db_name="starpost",
        )

    # Seed source data including a duplicate transaction_id in sales
    now = datetime.utcnow()
    oid1 = ObjectId()
    oid2 = ObjectId()
    oid3 = ObjectId()
    src_db.posts.insert_one({"_id": oid1, "title": "Migrated Post", "created_at": now})
    src_db.sales.insert_many([
        {"_id": oid2, "transaction_id": "tx_dup_1", "star_amount": 50, "date": now},
        {"_id": oid3, "transaction_id": "tx_dup_1", "star_amount": 50, "date": now},
    ])

    # 2. Dry run must not write anything to destination
    dry_res = run_migration(
        source_db=src_db,
        dest_db=dst_db,
        collections=["posts", "sales"],
        apply=False,
        report_duplicates=True,
        source_uri="mongodb://localhost:27017/starpost",
        dest_uri="mongodb+srv://user:pass@cluster0.mongodb.net/starpost",
    )
    assert dst_db.posts.count_documents({}) == 0
    assert dst_db.sales.count_documents({}) == 0
    assert len(dry_res["duplicates"]["sales"]) == 1
    assert dry_res["collections"]["posts"]["copied"] == 1
    assert dry_res["collections"]["sales"]["skipped_duplicates"] == 1

    # 3. Apply mode copies documents, preserves ObjectId and datetime, and skips duplicate transaction_id
    apply_res = run_migration(
        source_db=src_db,
        dest_db=dst_db,
        collections=["posts", "sales"],
        apply=True,
        report_duplicates=True,
        source_uri="mongodb://localhost:27017/starpost",
        dest_uri="mongodb+srv://user:pass@cluster0.mongodb.net/starpost",
    )
    assert dst_db.posts.count_documents({}) == 1
    assert dst_db.sales.count_documents({}) == 1
    assert apply_res["collections"]["sales"]["copied"] == 1
    assert apply_res["collections"]["sales"]["skipped_duplicates"] == 1

    migrated_post = dst_db.posts.find_one({"_id": oid1})
    assert migrated_post is not None
    assert isinstance(migrated_post["_id"], ObjectId)
    assert isinstance(migrated_post["created_at"], datetime)
