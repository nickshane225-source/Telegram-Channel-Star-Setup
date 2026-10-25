from datetime import datetime, timedelta
from pathlib import Path
import pytest
from app.models.post import PostStatus
from app.repositories.posts import posts_repo
from app.services.posts import PostService


class FakeTelegramService:
    """Mock Telegram service for testing post workflows."""
    def __init__(self):
        self.published = []
        self.edited_captions = []
        self.deleted_messages = []

    async def send_paid_media(self, chat_id, star_count, photo_paths, caption=None, parse_mode="HTML"):
        msg_id = 1000 + len(self.published) + 1
        self.published.append({
            "msg_id": msg_id,
            "chat_id": chat_id,
            "star_count": star_count,
            "photo_paths": photo_paths,
            "caption": caption
        })
        return msg_id, ["file_id_mock_123"]

    async def edit_message_caption(self, chat_id, message_id, caption, parse_mode="HTML"):
        self.edited_captions.append({"chat_id": chat_id, "message_id": message_id, "caption": caption})
        return True

    async def delete_message(self, chat_id, message_id):
        self.deleted_messages.append({"chat_id": chat_id, "message_id": message_id})
        return True


@pytest.mark.asyncio
async def test_create_and_publish_post(sample_photos):
    """Test full cycle: create draft post, then publish to channel."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    # 1. Create post
    post = await service.create_post(
        title="Exclusive Mountain Drop",
        caption="<b>Mountains in sunset</b>",
        photos=sample_photos,
        star_count=75,
        status=PostStatus.DRAFT,
        channel_id="-1001234567890"
    )

    assert post.id is not None
    assert post.status == PostStatus.DRAFT
    assert post.star_count == 75

    # 2. Publish post
    published = await service.publish_post(post.id)
    assert published.status == PostStatus.PUBLISHED
    assert published.telegram_message_id == 1001
    assert published.published_at is not None
    assert len(fake_tg.published) == 1


@pytest.mark.asyncio
async def test_edit_caption_flow(sample_photos):
    """Test editing caption on a published post calls editMessageCaption on Telegram."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    post = await service.create_post(
        title="Test Edit Post",
        caption="Initial Caption",
        photos=sample_photos,
        star_count=50,
        channel_id="-1001234567890"
    )
    published = await service.publish_post(post.id)

    # Edit caption
    updated = await service.edit_post_caption(published.id, "Brand New Caption ✨")
    assert updated.caption == "Brand New Caption ✨"
    assert len(fake_tg.edited_captions) == 1
    assert fake_tg.edited_captions[0]["caption"] == "Brand New Caption ✨"


@pytest.mark.asyncio
async def test_delete_and_repost_flow(sample_photos):
    """Test Delete and Repost: deletes previous Telegram message and posts again with new price."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    post = await service.create_post(
        title="Price Change Post",
        caption="Original Caption",
        photos=sample_photos,
        star_count=50,
        channel_id="-1001234567890"
    )
    published = await service.publish_post(post.id)
    old_msg_id = published.telegram_message_id

    # Repost with new price = 100
    reposted = await service.delete_and_repost(published.id, new_star_count=100)

    assert reposted.star_count == 100
    assert reposted.status == PostStatus.PUBLISHED
    # Old message was deleted
    assert any(m["message_id"] == old_msg_id for m in fake_tg.deleted_messages)
    # A new message was published
    assert reposted.telegram_message_id != old_msg_id


@pytest.mark.asyncio
async def test_soft_delete_and_restore(sample_photos):
    """Test soft delete moves post to trash and restore recovers it."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    post = await service.create_post(
        title="Trash Test Post",
        caption="Will be deleted",
        photos=sample_photos,
        star_count=20
    )

    # Soft delete
    deleted = await service.soft_delete_post(post.id, delete_from_telegram=False)
    assert deleted is True

    trashed_post = await posts_repo.get_by_id(post.id)
    assert trashed_post.status == PostStatus.DELETED
    assert trashed_post.deleted_at is not None

    # Restore
    restored = await service.restore_post(post.id)
    assert restored is True

    recovered_post = await posts_repo.get_by_id(post.id)
    assert recovered_post.status == PostStatus.DRAFT
    assert recovered_post.deleted_at is None


@pytest.mark.asyncio
async def test_permanent_delete_cleans_media(sample_photos):
    """Test permanent delete removes database record and disk media files."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    post = await service.create_post(
        title="Permanent Delete Test",
        caption="Goodbye",
        photos=sample_photos,
        star_count=10
    )
    photo_file = Path(sample_photos[0].path)
    assert photo_file.exists()

    # Permanent delete
    success = await service.permanent_delete_post(post.id)
    assert success is True

    # Post is removed from DB
    post_in_db = await posts_repo.get_by_id(post.id)
    assert post_in_db is None

    # Image file deleted from disk
    assert not photo_file.exists()


@pytest.mark.asyncio
async def test_duplicate_post(sample_photos):
    """Test duplicating an existing post."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    post = await service.create_post(
        title="Original Drop",
        caption="Copy me",
        photos=sample_photos,
        star_count=35
    )

    duplicated = await service.duplicate_post(post.id)
    assert duplicated.id != post.id
    assert duplicated.title == "Original Drop (Copy)"
    assert duplicated.star_count == 35
    assert duplicated.status == PostStatus.DRAFT


@pytest.mark.asyncio
async def test_bulk_create_posts(sample_photos):
    """Test creating multiple scheduled posts at spaced intervals."""
    fake_tg = FakeTelegramService()
    service = PostService(repo=posts_repo, tg_service=fake_tg)

    start = datetime.utcnow()
    posts = await service.bulk_create_posts(
        photos=sample_photos,
        star_count=50,
        base_caption="Bulk Drop Photo",
        start_time=start,
        interval_hours=2.0
    )

    assert len(posts) == 1
    assert posts[0].status == PostStatus.SCHEDULED
    assert posts[0].star_count == 50
