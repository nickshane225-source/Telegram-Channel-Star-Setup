import json
import pytest
import respx
import httpx
from app.services.telegram import (
    TelegramService,
    TelegramAPIError,
    TelegramAuthError,
    TelegramPermissionError,
    TelegramRateLimitError,
)

BASE_BOT_URL = "https://api.telegram.org/bot123456:TEST_TOKEN_ABC_DEF_12345"


@pytest.mark.asyncio
@respx.mock
async def test_get_me_success():
    """Verify getMe retrieves bot details correctly."""
    respx.get(f"{BASE_BOT_URL}/getMe").respond(
        status_code=200,
        json={"ok": True, "result": {"id": 123456, "is_bot": True, "first_name": "StarPostBot", "username": "starpost_bot"}}
    )
    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    info = await svc.get_me()
    assert info["id"] == 123456
    assert info["username"] == "starpost_bot"


@pytest.mark.asyncio
@respx.mock
async def test_get_me_invalid_token():
    """Verify TelegramAuthError is raised when token is rejected (401)."""
    respx.get(f"{BASE_BOT_URL}/getMe").respond(
        status_code=200,
        json={"ok": False, "error_code": 401, "description": "Unauthorized"}
    )
    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    with pytest.raises(TelegramAuthError):
        await svc.get_me()


@pytest.mark.asyncio
@respx.mock
async def test_send_paid_media_success(sample_image_path):
    """Verify sendPaidMedia sends multipart media array and returns message_id."""
    respx.post(f"{BASE_BOT_URL}/sendPaidMedia").respond(
        status_code=200,
        json={
            "ok": True,
            "result": {
                "message_id": 999123,
                "paid_media": {
                    "star_count": 50,
                    "paid_media": [
                        {
                            "type": "photo",
                            "photo": [{"file_id": "file_low"}, {"file_id": "file_high_123"}]
                        }
                    ]
                }
            }
        }
    )

    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    msg_id, file_ids = await svc.send_paid_media(
        chat_id="-1001234567890",
        star_count=50,
        photo_paths=[sample_image_path],
        caption="<b>Exclusive Drop!</b>",
        parse_mode="HTML"
    )

    assert msg_id == 999123
    assert file_ids == ["file_high_123"]


@pytest.mark.asyncio
@respx.mock
async def test_edit_message_caption_success():
    """Verify editMessageCaption posts caption update."""
    route = respx.post(f"{BASE_BOT_URL}/editMessageCaption").respond(
        status_code=200,
        json={"ok": True, "result": {"message_id": 999123}}
    )

    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    success = await svc.edit_message_caption(
        chat_id="-1001234567890",
        message_id=999123,
        caption="Updated caption text"
    )
    assert success is True
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_delete_message_success():
    """Verify deleteMessage sends delete request."""
    route = respx.post(f"{BASE_BOT_URL}/deleteMessage").respond(
        status_code=200,
        json={"ok": True, "result": True}
    )

    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    success = await svc.delete_message(chat_id="-1001234567890", message_id=999123)
    assert success is True
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_429_auto_retry(sample_image_path, monkeypatch):
    """Verify HTTP 429 invokes retry after wait."""
    # Speed up sleep in tests
    import asyncio
    sleeps = []
    async def fast_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    route = respx.post(f"{BASE_BOT_URL}/sendPaidMedia")
    # First response: 429 with retry_after = 1
    # Second response: 200 OK
    route.side_effect = [
        httpx.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}),
        httpx.Response(200, json={"ok": True, "result": {"message_id": 888777, "paid_media": {"paid_media": []}}})
    ]

    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    msg_id, _ = await svc.send_paid_media(
        chat_id="-1001234567890",
        star_count=25,
        photo_paths=[sample_image_path]
    )

    assert msg_id == 888777
    assert 1 in sleeps
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_test_connection_diagnostics():
    """Verify test_connection checks getMe and getChatMember permissions."""
    respx.get(f"{BASE_BOT_URL}/getMe").respond(
        status_code=200,
        json={"ok": True, "result": {"id": 123456, "username": "starpost_bot", "first_name": "StarPost"}}
    )
    respx.post(f"{BASE_BOT_URL}/getChatMember").respond(
        status_code=200,
        json={
            "ok": True,
            "result": {
                "status": "administrator",
                "can_post_messages": True,
                "can_edit_messages": True,
                "can_delete_messages": True
            }
        }
    )

    svc = TelegramService(token="123456:TEST_TOKEN_ABC_DEF_12345")
    diag = await svc.test_connection(channel_id="-1001234567890")

    assert diag["token_valid"] is True
    assert diag["is_admin"] is True
    assert diag["can_post_messages"] is True
    assert diag["can_edit_messages"] is True
    assert diag["can_delete_messages"] is True
    assert diag["ready_to_publish"] is True
