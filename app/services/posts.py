import os
from PIL import Image
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from loguru import logger
from app.config import settings
from app.models.post import PostModel, PostPhoto, PostStatus
from app.repositories.activity import activity_repo
from app.repositories.posts import posts_repo
from app.repositories.settings import settings_repo
from app.services.media import media_service
from app.services.telegram import (
    TelegramAPIError,
    TelegramAuthError,
    TelegramPermissionError,
    normalize_channel_id,
    telegram_service,
)
from app.utils.sanitizer import sanitize_telegram_html


class PostService:
    """Business logic for creating, publishing, editing, scheduling, and deleting paid posts."""

    def __init__(self, repo=posts_repo, tg_service=telegram_service):
        self.repo = repo
        self.tg_service = tg_service

    async def get_post_or_404(self, post_id: str) -> PostModel:
        post = await self.repo.get_by_id(post_id)
        if not post:
            raise ValueError(f"Post with ID '{post_id}' not found.")
        return post

    async def create_post(
        self,
        title: str,
        caption: str,
        photos: List[PostPhoto],
        star_count: int,
        status: PostStatus = PostStatus.DRAFT,
        scheduled_at: Optional[datetime] = None,
        channel_id: Optional[str] = None,
        hidden_text: str = "",
        hidden_mode: str = "off",
        card_style: str = "blurred photo background",
    ) -> PostModel:
        """Create a new post in database."""
        app_settings = await settings_repo.get_settings()
        target_channel = normalize_channel_id(channel_id or app_settings.channel_id or settings.TELEGRAM_CHANNEL_ID)

        # Mode A validation: max 9 real photos if card mode
        real_photos = [p for p in photos if not p.is_text_card]
        if hidden_mode == "card" and len(real_photos) > 9:
            raise ValueError("Mode 'card' supports a maximum of 9 photos so the locked card can be included as the 10th item.")

        post = PostModel(
            title=title.strip(),
            caption=caption.strip(),
            photos=photos,
            star_count=star_count,
            status=status,
            scheduled_at=scheduled_at,
            channel_id=target_channel,
            hidden_text=hidden_text.strip(),
            hidden_mode=hidden_mode,
            card_style=card_style,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        saved = await self.repo.create(post)
        # Note: Never log hidden text
        await activity_repo.log(
            action="post_created",
            details=f"Created post '{title}' with status {status.value}, mode '{hidden_mode}' and price {star_count} Stars",
            post_id=saved.id,
            level="info"
        )
        return saved

    async def publish_post(self, post_id: str) -> PostModel:
        """Publish post to Telegram channel according to configured hidden_mode."""
        post = await self.get_post_or_404(post_id)

        real_photos = post.real_photos
        if not real_photos:
            raise ValueError("Post cannot be published without at least 1 photo.")

        app_settings = await settings_repo.get_settings()
        channel = normalize_channel_id(post.channel_id or app_settings.channel_id or settings.TELEGRAM_CHANNEL_ID)

        if not channel:
            raise ValueError("No channel configured. Please configure your Telegram Channel ID in Settings.")

        # Atomically claim the post before sending to Telegram
        if hasattr(self.repo, "claim_for_publish"):
            claimed = await self.repo.claim_for_publish(post.id)
            if not claimed:
                logger.info(f"Post {post.id} is already being published (or already published/deleted), skipping.")
                return await self.get_post_or_404(post.id)
            post = claimed

        # Prepare caption with default footer if configured
        final_caption = post.caption
        if app_settings.default_caption_footer:
            footer = app_settings.default_caption_footer.strip()
            if footer and footer not in final_caption:
                final_caption = f"{final_caption}\n\n{footer}".strip()

        try:
            logger.info(f"Publishing post {post.id} ({post.title}) in mode '{post.hidden_mode}' to {channel} for {post.star_count} stars...")

            # -----------------------------------------------------------
            # MODE B: Bot Delivery (Teaser in Channel + Unlock Link)
            # -----------------------------------------------------------
            if post.hidden_mode == "bot":
                bot_info = await self.tg_service.get_me()
                bot_username = bot_info.get("username", "")
                unlock_url = f"https://t.me/{bot_username}?start=p_{post.id}"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": f"⭐ Unlock for {post.star_count} Stars", "url": unlock_url}]
                    ]
                }

                if len(real_photos) == 1:
                    # Single photo: generate blurred teaser with badge and inline button
                    teaser_path = media_service.generate_teaser(real_photos[0].path, post.star_count)
                    res = await self.tg_service.send_photo(
                        chat_id=channel,
                        photo_path=teaser_path,
                        caption=final_caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                    msg_id = res.get("message_id")
                    teaser_msg_id = None
                else:
                    # Multiple photos: generate blurred teasers for album and follow-up button
                    teaser_paths = [media_service.generate_teaser(p.path, post.star_count) for p in real_photos]
                    res = await self.tg_service.send_media_group(
                        chat_id=channel,
                        photo_paths=teaser_paths,
                        caption=final_caption,
                        parse_mode="HTML"
                    )
                    msg_id = res[0].get("message_id") if isinstance(res, list) and res else None
                    btn_res = await self.tg_service.send_message(
                        chat_id=channel,
                        text=f"🔒 Tap below to unlock '{post.title}' ({len(real_photos)} photos + bonus content):",
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                    teaser_msg_id = btn_res.get("message_id") if isinstance(btn_res, dict) else None

                updated = await self.repo.update(
                    post.id,
                    {
                        "status": PostStatus.PUBLISHED.value,
                        "published_at": datetime.utcnow(),
                        "telegram_message_id": msg_id,
                        "teaser_message_id": teaser_msg_id,
                        "channel_id": channel,
                        "error": None,
                        "retry_count": 0,
                        "publish_lock_until": None,
                    }
                )

                await activity_repo.log(
                    action="post_published",
                    details=f"Published Mode B teaser for '{post.title}' to channel {channel} (Message #{msg_id})",
                    post_id=post.id,
                    level="success"
                )
                return updated

            # -----------------------------------------------------------
            # MODE A: Card Delivery (Locked Text Card inside channel album)
            # -----------------------------------------------------------
            elif post.hidden_mode == "card" and post.hidden_text.strip():
                if len(real_photos) > 9:
                    raise ValueError("Mode 'card' allows at most 9 photos so the locked text card can be included as the 10th item.")

                ref_photo = real_photos[0]
                ref_w = ref_photo.width or 1080
                ref_h = ref_photo.height or 1080
                card_path = media_service.render_text_card(
                    text=post.hidden_text,
                    reference_size=(ref_w, ref_h),
                    style=post.card_style,
                    background_photo_path=ref_photo.path
                )

                card_img = Image.open(card_path)
                cw, ch = card_img.size
                c_size = os.path.getsize(card_path)
                card_photo = PostPhoto(
                    path=card_path,
                    original_filename="hidden_text_card.jpg",
                    width=cw,
                    height=ch,
                    size_bytes=c_size,
                    is_text_card=True,
                )

                # Card is appended as the LAST item of the album
                all_photos = list(real_photos) + [card_photo]
                photo_paths = [p.path for p in all_photos]

                msg_id, file_ids = await self.tg_service.send_paid_media(
                    chat_id=channel,
                    star_count=post.star_count,
                    photo_paths=photo_paths,
                    caption=final_caption,
                    parse_mode="HTML"
                )

                # Update photos with file IDs
                updated_photos = []
                for i, p in enumerate(all_photos):
                    f_id = file_ids[i] if i < len(file_ids) else None
                    updated_photos.append(
                        PostPhoto(
                            path=p.path,
                            file_id=f_id,
                            original_filename=p.original_filename,
                            width=p.width,
                            height=p.height,
                            size_bytes=p.size_bytes,
                            is_text_card=p.is_text_card,
                        )
                    )

                updated = await self.repo.update(
                    post.id,
                    {
                        "status": PostStatus.PUBLISHED.value,
                        "published_at": datetime.utcnow(),
                        "telegram_message_id": msg_id,
                        "channel_id": channel,
                        "photos": [p.model_dump() for p in updated_photos],
                        "error": None,
                        "retry_count": 0,
                        "publish_lock_until": None,
                    }
                )

                await activity_repo.log(
                    action="post_published",
                    details=f"Published '{post.title}' with locked text card to channel {channel} (Message #{msg_id})",
                    post_id=post.id,
                    level="success"
                )
                return updated

            # -----------------------------------------------------------
            # STANDARD MODE (hidden_mode == "off")
            # -----------------------------------------------------------
            else:
                photo_paths = [p.path for p in real_photos]
                msg_id, file_ids = await self.tg_service.send_paid_media(
                    chat_id=channel,
                    star_count=post.star_count,
                    photo_paths=photo_paths,
                    caption=final_caption,
                    parse_mode="HTML"
                )

                updated_photos = []
                for i, p in enumerate(real_photos):
                    f_id = file_ids[i] if i < len(file_ids) else None
                    updated_photos.append(
                        PostPhoto(
                            path=p.path,
                            file_id=f_id,
                            original_filename=p.original_filename,
                            width=p.width,
                            height=p.height,
                            size_bytes=p.size_bytes,
                            is_text_card=False,
                        )
                    )

                updated = await self.repo.update(
                    post.id,
                    {
                        "status": PostStatus.PUBLISHED.value,
                        "published_at": datetime.utcnow(),
                        "telegram_message_id": msg_id,
                        "channel_id": channel,
                        "photos": [p.model_dump() for p in updated_photos],
                        "error": None,
                        "retry_count": 0,
                        "publish_lock_until": None,
                    }
                )

                await activity_repo.log(
                    action="post_published",
                    details=f"Published '{post.title}' to channel {channel} (Message #{msg_id})",
                    post_id=post.id,
                    level="success"
                )
                return updated

        except Exception as exc:
            new_retry_count = post.retry_count + 1
            error_msg = str(exc)
            logger.error(f"Failed to publish post {post.id}: {error_msg}")

            # Mark as failed if maximum retries reached
            new_status = PostStatus.FAILED.value if new_retry_count >= 3 else post.status.value

            await self.repo.update(
                post.id,
                {
                    "retry_count": new_retry_count,
                    "error": error_msg,
                    "status": new_status,
                    "publish_lock_until": None,
                }
            )

            await activity_repo.log(
                action="post_publish_failed",
                details=f"Failed to publish '{post.title}': {error_msg} (Attempt {new_retry_count}/3)",
                post_id=post.id,
                level="error"
            )
            raise

    async def edit_post_caption(self, post_id: str, new_caption: str) -> PostModel:
        """Edit caption of post. If published, syncs with Telegram via editMessageCaption."""
        post = await self.get_post_or_404(post_id)

        if post.status == PostStatus.PUBLISHED and post.telegram_message_id and post.channel_id:
            logger.info(f"Updating Telegram message #{post.telegram_message_id} caption on channel {post.channel_id}")
            await self.tg_service.edit_message_caption(
                chat_id=post.channel_id,
                message_id=post.telegram_message_id,
                caption=new_caption,
                parse_mode="HTML"
            )

        updated = await self.repo.update(post_id, {"caption": new_caption})
        await activity_repo.log(
            action="caption_updated",
            details=f"Updated caption for '{post.title}'",
            post_id=post_id,
            level="info"
        )
        return updated

    async def delete_and_repost(
        self,
        post_id: str,
        new_star_count: Optional[int] = None,
        new_photos: Optional[List[PostPhoto]] = None,
        new_caption: Optional[str] = None,
        new_hidden_text: Optional[str] = None,
        new_card_style: Optional[str] = None,
    ) -> PostModel:
        """
        Delete the existing published message from channel and re-publish with modified price, photos, or hidden text.
        Per Telegram Rules, photos and price cannot be edited in-place for channel paid media.
        """
        post = await self.get_post_or_404(post_id)

        # 1. Delete previous message from channel if it was published
        if post.telegram_message_id and post.channel_id:
            try:
                await self.tg_service.delete_message(
                    chat_id=post.channel_id,
                    message_id=post.telegram_message_id
                )
                logger.info(f"Deleted old message #{post.telegram_message_id} from {post.channel_id}")
            except Exception as exc:
                logger.warning(f"Could not delete old message #{post.telegram_message_id}: {exc}")

        # If teaser message was posted separately in mode B
        if post.teaser_message_id and post.channel_id:
            try:
                await self.tg_service.delete_message(
                    chat_id=post.channel_id,
                    message_id=post.teaser_message_id
                )
            except Exception as exc:
                logger.debug(f"Could not delete teaser button message: {exc}")

        # 2. Update post state
        update_data: Dict[str, Any] = {
            "telegram_message_id": None,
            "teaser_message_id": None,
            "status": PostStatus.DRAFT.value,
        }
        if new_star_count is not None:
            update_data["star_count"] = new_star_count
        if new_photos is not None:
            update_data["photos"] = [p.model_dump() for p in new_photos]
        if new_caption is not None:
            update_data["caption"] = new_caption
        if new_hidden_text is not None:
            update_data["hidden_text"] = new_hidden_text.strip()
        if new_card_style is not None:
            update_data["card_style"] = new_card_style

        await self.repo.update(post_id, update_data)

        # 3. Re-publish immediately
        republished = await self.publish_post(post_id)

        await activity_repo.log(
            action="post_reposted",
            details=f"Deleted old post and reposted '{post.title}' with price {republished.star_count} Stars",
            post_id=post_id,
            level="success"
        )
        return republished

    async def soft_delete_post(self, post_id: str, delete_from_telegram: bool = True) -> bool:
        """Delete message from channel if published, and move post to trash."""
        post = await self.get_post_or_404(post_id)

        if delete_from_telegram and post.status == PostStatus.PUBLISHED and post.telegram_message_id and post.channel_id:
            try:
                await self.tg_service.delete_message(
                    chat_id=post.channel_id,
                    message_id=post.telegram_message_id
                )
                logger.info(f"Deleted message #{post.telegram_message_id} from channel {post.channel_id}")
            except Exception as exc:
                logger.warning(f"Failed to delete telegram message #{post.telegram_message_id}: {exc}")

        if delete_from_telegram and post.teaser_message_id and post.channel_id:
            try:
                await self.tg_service.delete_message(
                    chat_id=post.channel_id,
                    message_id=post.teaser_message_id
                )
            except Exception:
                pass

        success = await self.repo.soft_delete(post_id)
        if success:
            await activity_repo.log(
                action="post_trashed",
                details=f"Moved post '{post.title}' to Trash",
                post_id=post_id,
                level="warning"
            )
        return success

    async def restore_post(self, post_id: str) -> bool:
        """Restore post from trash to draft."""
        post = await self.get_post_or_404(post_id)
        success = await self.repo.restore(post_id)
        if success:
            await activity_repo.log(
                action="post_restored",
                details=f"Restored post '{post.title}' from Trash to Drafts",
                post_id=post_id,
                level="info"
            )
        return success

    async def permanent_delete_post(self, post_id: str) -> bool:
        """Permanently delete post and its associated photo files."""
        post = await self.get_post_or_404(post_id)

        # Delete photo files
        for p in post.photos:
            media_service.delete_photo(p.path)

        success = await self.repo.permanent_delete(post_id)
        if success:
            await activity_repo.log(
                action="post_permanently_deleted",
                details=f"Permanently deleted post '{post.title}' and cleaned media storage",
                post_id=post_id,
                level="warning"
            )
        return success

    async def duplicate_post(self, post_id: str) -> PostModel:
        """Duplicate an existing post as a draft."""
        original = await self.get_post_or_404(post_id)
        copy_title = f"{original.title} (Copy)"

        new_post = PostModel(
            title=copy_title,
            caption=original.caption,
            photos=original.real_photos,
            star_count=original.star_count,
            status=PostStatus.DRAFT,
            channel_id=original.channel_id,
            hidden_text=original.hidden_text,
            hidden_mode=original.hidden_mode,
            card_style=original.card_style,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        saved = await self.repo.create(new_post)
        await activity_repo.log(
            action="post_duplicated",
            details=f"Duplicated '{original.title}' as '{copy_title}'",
            post_id=saved.id,
            level="info"
        )
        return saved

    async def bulk_create_posts(
        self,
        photos: List[PostPhoto],
        star_count: int,
        base_caption: str,
        start_time: datetime,
        interval_hours: float,
        bulk_prefix: str = "Photo Drop",
        hidden_text: str = "",
        hidden_mode: str = "off",
        card_style: str = "blurred photo background",
    ) -> List[PostModel]:
        """
        Create multiple posts at once (one per photo), automatically scheduling them
        with the specified interval starting at start_time.
        Uses bulk_prefix for post titles.
        """
        created_posts = []
        prefix = (bulk_prefix or "Photo Drop").strip()

        for idx, photo in enumerate(photos):
            scheduled_at = start_time + timedelta(hours=interval_hours * idx)
            title = f"{prefix} #{idx + 1}"
            post = await self.create_post(
                title=title,
                caption=base_caption,
                photos=[photo],
                star_count=star_count,
                status=PostStatus.SCHEDULED,
                scheduled_at=scheduled_at,
                hidden_text=hidden_text,
                hidden_mode=hidden_mode,
                card_style=card_style,
            )
            created_posts.append(post)

        await activity_repo.log(
            action="bulk_posts_created",
            details=f"Bulk-created and scheduled {len(created_posts)} posts with prefix '{prefix}' and interval {interval_hours}h",
            level="info"
        )
        return created_posts


post_service = PostService()

