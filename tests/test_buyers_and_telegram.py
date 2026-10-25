import io
import pytest
import respx
from httpx import Response
from starlette.testclient import TestClient

from app.main import app
from app.models.post import PostModel, PostPhoto, PostStatus
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.services.auth import auth_service
from app.services.telegram import TelegramService


@pytest.mark.asyncio
async def test_buyers_page_and_csv_export(sample_photos):
    """Test /buyers list and /buyers/export CSV generation."""
    post = await posts_repo.create(
        PostModel(
            title="VIP Post for Buyers Test",
            caption="Caption",
            photos=sample_photos,
            star_count=50,
            hidden_text="Secret content",
            hidden_mode="bot"
        )
    )

    # Insert 2 purchases
    await purchases_repo.record_purchase(user_id=111, username="alice", post_id=post.id, stars=50)
    await purchases_repo.record_purchase(user_id=222, username="bob", post_id=post.id, stars=50)

    # Use authenticated test client
    client = TestClient(app)
    token = auth_service.create_token("admin")
    client.cookies.set("starpost_session", token)

    # 1. Test /buyers HTML page
    resp = client.get("/buyers", follow_redirects=True)
    assert resp.status_code == 200
    assert "alice" in resp.text
    assert "bob" in resp.text
    assert "VIP Post for Buyers Test" in resp.text

    # 2. Test /buyers/export CSV
    resp_csv = client.get("/buyers/export")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers["content-type"]
    csv_content = resp_csv.text
    assert "Buyer User ID,Username,Post ID,Stars Paid,Purchase Date (UTC)" in csv_content
    assert "alice" in csv_content
    assert "bob" in csv_content


@pytest.mark.asyncio
@respx.mock
async def test_telegram_new_endpoints():
    """Verify deleteWebhook, getUpdates, sendMessage, and sendPhoto in TelegramService."""
    bot_base = "https://api.telegram.org/bot123456:TEST_TOKEN_ABC_DEF_12345"

    respx.post(f"{bot_base}/deleteWebhook").respond(
        status_code=200,
        json={"ok": True, "result": True}
    )
    respx.post(f"{bot_base}/getUpdates").respond(
        status_code=200,
        json={"ok": True, "result": [{"update_id": 9901, "message": {"text": "hello"}}]}
    )
    respx.post(f"{bot_base}/sendMessage").respond(
        status_code=200,
        json={"ok": True, "result": {"message_id": 7711}}
    )

    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")

    # delete_webhook
    del_res = await svc.delete_webhook()
    assert del_res is True

    # get_updates
    updates = await svc.get_updates(timeout=1)
    assert len(updates) == 1
    assert updates[0]["update_id"] == 9901

    # send_message
    msg_res = await svc.send_message(chat_id=123456, text="Hello <b>World</b>", protect_content=True)
    assert msg_res["message_id"] == 7711
