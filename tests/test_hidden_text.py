from datetime import datetime
from pathlib import Path
import pytest
import respx
from PIL import Image

from app.models.post import PostModel, PostPhoto, PostStatus
from app.repositories.activity import activity_repo
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.services.bot_listener import bot_listener
from app.services.media import media_service
from app.services.posts import PostService
from app.services.telegram import TelegramService


class MockTelegramService:
    def __init__(self):
        self.paid_media_calls = []
        self.photos_sent = []
        self.media_groups_sent = []
        self.messages_sent = []
        self.deleted_messages = []
        self.edited_captions = []

    async def get_me(self):
        return {"id": 123456, "username": "test_star_bot", "first_name": "TestStarBot"}

    async def send_paid_media(self, chat_id, star_count, photo_paths, caption=None, parse_mode="HTML", payload=None):
        msg_id = 2000 + len(self.paid_media_calls) + 1
        call_info = {
            "msg_id": msg_id,
            "chat_id": chat_id,
            "star_count": star_count,
            "photo_paths": photo_paths,
            "caption": caption,
            "payload": payload,
        }
        self.paid_media_calls.append(call_info)
        file_ids = [f"file_id_{i}" for i in range(len(photo_paths))]
        return msg_id, file_ids

    async def send_photo(self, chat_id, photo_path, caption=None, parse_mode="HTML", reply_markup=None):
        msg_id = 3000 + len(self.photos_sent) + 1
        self.photos_sent.append({
            "msg_id": msg_id,
            "chat_id": chat_id,
            "photo_path": photo_path,
            "caption": caption,
            "reply_markup": reply_markup,
        })
        return {"message_id": msg_id}

    async def send_media_group(self, chat_id, photo_paths, caption=None, parse_mode="HTML"):
        msg_id = 4000 + len(self.media_groups_sent) + 1
        self.media_groups_sent.append({
            "msg_id": msg_id,
            "chat_id": chat_id,
            "photo_paths": photo_paths,
            "caption": caption,
        })
        return [{"message_id": msg_id}]

    async def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None, protect_content=False):
        msg_id = 5000 + len(self.messages_sent) + 1
        self.messages_sent.append({
            "msg_id": msg_id,
            "chat_id": chat_id,
            "text": text,
            "protect_content": protect_content,
            "reply_markup": reply_markup,
        })
        return {"message_id": msg_id}

    async def delete_message(self, chat_id, message_id):
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return True

    async def edit_message_caption(self, chat_id, message_id, caption, parse_mode="HTML"):
        self.edited_captions.append({"chat_id": chat_id, "message_id": message_id, "caption": caption})
        return True


@pytest.fixture
def multi_sample_photos(tmp_path):
    """Generate 10 distinct sample photos."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    photos = []
    for i in range(10):
        img = Image.new("RGB", (640, 480), color=(20 * i, 50, 100))
        p = media_dir / f"sample_{i}.jpg"
        img.save(p, "JPEG")
        photos.append(
            PostPhoto(
                path=str(p),
                original_filename=f"sample_{i}.jpg",
                width=640,
                height=480,
                size_bytes=1024,
            )
        )
    return photos


@pytest.mark.asyncio
async def test_card_rendering_and_album_order(sample_photos):
    """
    Test Mode A (Card):
    Text card is rendered using Pillow and appended as the LAST item of sendPaidMedia album.
    Marked with is_text_card=True and excluded from real_photos.
    """
    mock_tg = MockTelegramService()
    svc = PostService(repo=posts_repo, tg_service=mock_tg)

    secret_note = "VIP Secret Access Code: 998877"
    post = await svc.create_post(
        title="Mode A Test Post",
        caption="Public Channel Caption",
        photos=sample_photos,
        star_count=60,
        hidden_text=secret_note,
        hidden_mode="card",
        card_style="blurred photo background"
    )

    published = await svc.publish_post(post.id)

    # Telegram sendPaidMedia was called
    assert len(mock_tg.paid_media_calls) == 1
    call = mock_tg.paid_media_calls[0]

    # Album had 2 photos: sample photo first, text card last!
    assert len(call["photo_paths"]) == 2
    assert call["photo_paths"][0] == sample_photos[0].path
    assert "card_" in call["photo_paths"][1]

    # Post model in database has 2 photos, with the second marked is_text_card=True
    assert len(published.photos) == 2
    assert published.photos[0].is_text_card is False
    assert published.photos[1].is_text_card is True

    # Real photos property excludes the text card
    assert len(published.real_photos) == 1
    assert published.real_photos[0].path == sample_photos[0].path


@pytest.mark.asyncio
async def test_max_photo_validation_mode_card(multi_sample_photos):
    """
    Test max-photo validation:
    Mode 'card' allows at most 9 real photos (so card is 10th).
    Attempting 10 real photos in Mode A raises ValueError.
    """
    mock_tg = MockTelegramService()
    svc = PostService(repo=posts_repo, tg_service=mock_tg)

    # 10 photos in Mode 'card' must be rejected
    with pytest.raises(ValueError, match="maximum of 9 photos"):
        await svc.create_post(
            title="Too Many Photos",
            caption="Caption",
            photos=multi_sample_photos[:10],
            star_count=50,
            hidden_text="Secret content",
            hidden_mode="card"
        )

    # 9 photos in Mode 'card' must succeed
    post9 = await svc.create_post(
        title="Valid 9 Photos",
        caption="Caption",
        photos=multi_sample_photos[:9],
        star_count=50,
        hidden_text="Secret content",
        hidden_mode="card"
    )
    published = await svc.publish_post(post9.id)
    # Total sent to Telegram is 9 real + 1 card = 10 items
    assert len(mock_tg.paid_media_calls[0]["photo_paths"]) == 10
    assert published.photos[-1].is_text_card is True


@pytest.mark.asyncio
async def test_teaser_generation(sample_image_path):
    """Verify teaser generation blurs photo and creates badge image on disk."""
    teaser_path = media_service.generate_teaser(sample_image_path, star_count=75)
    assert Path(teaser_path).exists()

    img = Image.open(teaser_path)
    assert img.size == (800, 600)
    assert img.format == "JPEG"


@pytest.mark.asyncio
async def test_mode_b_publish_and_start_handling(sample_photos):
    """
    Test Mode B (Bot):
    1. Channel post sends blurred teaser with unlock button to https://t.me/<bot>?start=p_<post_id>.
    2. /start p_<id> by new user triggers sendPaidMedia to user's chat with payload u:<id>.
    3. User purchases: purchased_paid_media triggers delivery of hidden text.
    4. Subsequent /start p_<id> by same buyer re-delivers full content for free.
    """
    mock_tg = MockTelegramService()
    svc = PostService(repo=posts_repo, tg_service=mock_tg)

    post = await svc.create_post(
        title="Mode B Drop",
        caption="Check out our new drop",
        photos=sample_photos,
        star_count=120,
        hidden_text="SECRET_BOT_LINK_HTTPS://example.com/vip",
        hidden_mode="bot"
    )

    # 1. Publish to channel
    published = await svc.publish_post(post.id)
    assert published.status == PostStatus.PUBLISHED
    assert len(mock_tg.photos_sent) == 1

    teaser_call = mock_tg.photos_sent[0]
    assert "teaser_" in teaser_call["photo_path"]
    # Verify unlock link in button
    btn_url = teaser_call["reply_markup"]["inline_keyboard"][0][0]["url"]
    assert btn_url == f"https://t.me/test_star_bot?start=p_{post.id}"

    # 2. First-time buyer sends /start p_<post_id>
    bot_listener.tg_service = mock_tg
    await bot_listener.handle_start_post(user_id=888123, username="test_buyer", post_id=post.id)

    # Paid media sent to user's private chat
    assert len(mock_tg.paid_media_calls) == 1
    dm_call = mock_tg.paid_media_calls[0]
    assert dm_call["chat_id"] == 888123
    assert dm_call["star_count"] == 120
    assert dm_call["payload"] == f"u:{post.id}"
    assert dm_call["photo_paths"] == [sample_photos[0].path]

    # 3. User buys: Telegram sends purchased_paid_media update
    await bot_listener.handle_purchased_paid_media(
        user_id=888123,
        username="test_buyer",
        payload=f"u:{post.id}",
        stars=120
    )

    # Verify purchase recorded in purchases collection
    purchases = await purchases_repo.get_purchases_by_post(post.id)
    assert len(purchases) == 1
    assert purchases[0].user_id == 888123
    assert purchases[0].stars == 120

    # Verify stars_earned incremented on post
    updated_post = await posts_repo.get_by_id(post.id)
    assert updated_post.stars_earned == 120

    # Verify hidden text was delivered via sendMessage with protect_content=True
    assert len(mock_tg.messages_sent) == 1
    msg_call = mock_tg.messages_sent[0]
    assert msg_call["chat_id"] == 888123
    assert "SECRET_BOT_LINK_HTTPS" in msg_call["text"]
    assert msg_call["protect_content"] is True

    # 4. Previous buyer sends /start p_<post_id> again: should re-send for free!
    mock_tg.paid_media_calls.clear()
    mock_tg.photos_sent.clear()
    mock_tg.messages_sent.clear()

    await bot_listener.handle_start_post(user_id=888123, username="test_buyer", post_id=post.id)

    # No sendPaidMedia was sent
    assert len(mock_tg.paid_media_calls) == 0
    # Photos were re-sent for free
    assert len(mock_tg.photos_sent) == 1
    # Hidden text was re-sent
    assert len(mock_tg.messages_sent) == 1
    assert "SECRET_BOT_LINK_HTTPS" in mock_tg.messages_sent[0]["text"]


@pytest.mark.asyncio
async def test_purchase_update_idempotency(sample_photos):
    """
    Verify purchase update idempotency:
    If duplicate purchased_paid_media updates arrive for the same user and post,
    purchases collection and stars_earned are NOT duplicated.
    """
    mock_tg = MockTelegramService()
    bot_listener.tg_service = mock_tg

    post = await posts_repo.create(
        PostModel(
            title="Idempotency Test",
            caption="Caption",
            photos=sample_photos,
            star_count=50,
            hidden_text="Hidden message",
            hidden_mode="bot"
        )
    )

    # Event 1
    await bot_listener.handle_purchased_paid_media(
        user_id=555,
        username="buyer_alpha",
        payload=f"u:{post.id}",
        stars=50
    )

    # Event 2 (duplicate delivery from Telegram)
    await bot_listener.handle_purchased_paid_media(
        user_id=555,
        username="buyer_alpha",
        payload=f"u:{post.id}",
        stars=50
    )

    # Count purchases
    count = await purchases_repo.count_by_post(post.id)
    assert count == 1

    # Check stars_earned on post (must be 50, not 100)
    p = await posts_repo.get_by_id(post.id)
    assert p.stars_earned == 50


@pytest.mark.asyncio
async def test_hidden_text_never_appears_in_caption_or_logs(sample_photos):
    """
    Verify hidden text NEVER leaks into:
    1. Telegram public captions
    2. Activity log entries
    """
    mock_tg = MockTelegramService()
    svc = PostService(repo=posts_repo, tg_service=mock_tg)

    CANARY = "SECRET_CANARY_VALUE_SUPER_TOP_SECRET_12345"

    post = await svc.create_post(
        title="Canary Test",
        caption="Public preview caption text",
        photos=sample_photos,
        star_count=50,
        hidden_text=CANARY,
        hidden_mode="card"
    )

    await svc.publish_post(post.id)

    # 1. Check sendPaidMedia caption
    for call in mock_tg.paid_media_calls:
        if call.get("caption"):
            assert CANARY not in call["caption"]

    # 2. Check activity log
    recent_logs = await activity_repo.get_recent(limit=50)
    for log in recent_logs:
        assert CANARY not in log.details
        assert CANARY not in log.action
