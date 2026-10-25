import asyncio
from typing import Any, Dict, Optional
import httpx
from loguru import logger
from app.db import get_db
from app.models.post import PostStatus
from app.repositories.activity import activity_repo
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.services.telegram import TelegramAPIError, telegram_service


class BotListenerService:
    """
    Background long-polling worker for Telegram Bot updates.
    Handles /start deep-links and purchased_paid_media events for Mode B delivery.
    """

    def __init__(self, tg_service=telegram_service, p_repo=posts_repo, pur_repo=purchases_repo):
        self.tg_service = tg_service
        self.posts_repo = p_repo
        self.purchases_repo = pur_repo
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._offset: Optional[int] = None

    async def get_persisted_offset(self) -> Optional[int]:
        """Fetch last processed update offset from database."""
        db = get_db()
        if db is None:
            return None
        try:
            doc = await db.bot_state.find_one({"key": "update_offset"})
            return doc.get("offset") if doc else None
        except Exception as exc:
            logger.warning(f"Could not load bot update offset from DB: {exc}")
            return None

    async def save_persisted_offset(self, offset: int) -> None:
        """Persist processed update offset to database."""
        db = get_db()
        if db is None:
            return
        try:
            await db.bot_state.update_one(
                {"key": "update_offset"},
                {"$set": {"offset": offset, "updated_at": asyncio.get_event_loop().time()}},
                upsert=True
            )
        except Exception as exc:
            logger.warning(f"Could not persist bot update offset: {exc}")

    async def start(self) -> None:
        """Start long-polling worker loop in background."""
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Bot Listener background service started.")

    async def stop(self) -> None:
        """Stop long-polling worker cleanly."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Bot Listener background service stopped.")

    async def _poll_loop(self) -> None:
        """Long-polling update loop with exponential backoff on network failures."""
        # Step 1: Remove any active webhook so getUpdates works
        try:
            await self.tg_service.delete_webhook(drop_pending_updates=False)
        except Exception as exc:
            logger.warning(f"deleteWebhook check returned: {exc}")

        # Step 2: Load last saved offset
        self._offset = await self.get_persisted_offset()
        backoff_sec = 2

        while self.is_running:
            try:
                updates = await self.tg_service.get_updates(
                    offset=self._offset,
                    timeout=50,
                    allowed_updates=["message", "purchased_paid_media"],
                )
                # Successful response: reset backoff
                backoff_sec = 2

                for u in updates:
                    update_id = u.get("update_id")
                    if update_id is not None:
                        self._offset = update_id + 1
                        await self.save_persisted_offset(self._offset)

                    await self._process_single_update(u)

            except asyncio.CancelledError:
                break
            except (httpx.RequestError, TelegramAPIError, Exception) as exc:
                if not self.is_running:
                    break
                logger.warning(
                    f"Bot listener update error: {exc}. Retrying in {backoff_sec}s..."
                )
                await asyncio.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2, 30)

    async def _process_single_update(self, update: Dict[str, Any]) -> None:
        """Route an update to the appropriate handler."""
        # 1. Message Update (/start p_<post_id>)
        if "message" in update:
            msg = update["message"]
            text = (msg.get("text") or "").strip()
            from_user = msg.get("from") or {}
            user_id = from_user.get("id") or msg.get("chat", {}).get("id")
            username = from_user.get("username") or from_user.get("first_name") or "Telegram User"

            if text.startswith("/start p_") and user_id:
                post_id = text.split("p_", 1)[1].strip()
                await self.handle_start_post(user_id=int(user_id), username=username, post_id=post_id)
            elif text == "/start" and user_id:
                welcome = (
                    "👋 <b>Welcome to StarPost Manager!</b>\n\n"
                    "Tap 'Unlock' on any paid post in our channel to purchase and access exclusive content."
                )
                try:
                    await self.tg_service.send_message(chat_id=user_id, text=welcome, parse_mode="HTML")
                except Exception as exc:
                    logger.debug(f"Could not send welcome message: {exc}")

        # 2. Purchased Paid Media Update
        if "purchased_paid_media" in update:
            ppm = update["purchased_paid_media"]
            from_user = ppm.get("from") or ppm.get("user") or {}
            user_id = from_user.get("id")
            username = from_user.get("username") or from_user.get("first_name")
            payload = ppm.get("paid_media_payload") or ppm.get("payload") or ""

            if user_id and payload:
                await self.handle_purchased_paid_media(
                    user_id=int(user_id),
                    username=username,
                    payload=payload
                )

    async def handle_start_post(self, user_id: int, username: Optional[str], post_id: str) -> None:
        """
        Handle user clicking deep-link: /start p_<post_id>.
        If already purchased, re-send full media and hidden text for free.
        Otherwise send sendPaidMedia to user's private chat.
        """
        post = await self.posts_repo.get_by_id(post_id)
        if not post or post.deleted_at:
            await self.tg_service.send_message(
                chat_id=user_id,
                text="❌ <i>This post is no longer available.</i>",
                parse_mode="HTML"
            )
            return

        # Check if user already purchased this post
        has_bought = await self.purchases_repo.has_purchased(user_id=user_id, post_id=post.id)
        real_photos = post.real_photos
        photo_paths = [p.path for p in real_photos]

        if has_bought:
            logger.info(f"User {user_id} already purchased post {post.id}. Re-delivering content for free.")
            # 1. Send genuine photos for free
            if len(photo_paths) == 1:
                await self.tg_service.send_photo(
                    chat_id=user_id,
                    photo_path=photo_paths[0],
                    caption=post.caption or post.title,
                    parse_mode="HTML"
                )
            elif len(photo_paths) > 1:
                await self.tg_service.send_media_group(
                    chat_id=user_id,
                    photo_paths=photo_paths,
                    caption=post.caption or post.title,
                    parse_mode="HTML"
                )

            # 2. Re-send hidden text
            if post.hidden_text:
                hidden_msg = (
                    "🔓 <b>Your Unlocked Content:</b>\n\n"
                    f"{post.hidden_text}"
                )
                await self.tg_service.send_message(
                    chat_id=user_id,
                    text=hidden_msg,
                    parse_mode="HTML",
                    protect_content=True
                )
            return

        # User has not bought yet: Send sendPaidMedia directly to user's private chat
        if not photo_paths:
            logger.warning(f"Post {post.id} has no photos to deliver in DM.")
            return

        payload = f"u:{post.id}"
        logger.info(f"Sending sendPaidMedia to private chat of user {user_id} for post {post.id} ({post.star_count} stars)")

        try:
            await self.tg_service.send_paid_media(
                chat_id=user_id,
                star_count=post.star_count,
                photo_paths=photo_paths,
                caption=post.caption,
                parse_mode="HTML",
                payload=payload
            )
        except Exception as exc:
            logger.error(f"Failed to send sendPaidMedia to user {user_id}: {exc}")
            await self.tg_service.send_message(
                chat_id=user_id,
                text=f"⚠️ <i>Unable to initiate Stars payment: {exc}</i>",
                parse_mode="HTML"
            )

    async def handle_purchased_paid_media(
        self,
        user_id: int,
        username: Optional[str],
        payload: str,
        stars: Optional[int] = None,
    ) -> None:
        """
        Handle Telegram purchased_paid_media update.
        Idempotently inserts into purchases, increments post stars_earned, and delivers hidden text.
        """
        # Parse payload format: "u:<post_id>"
        if payload.startswith("u:"):
            post_id = payload[2:].strip()
        else:
            post_id = payload.strip()

        post = await self.posts_repo.get_by_id(post_id)
        if not post:
            logger.warning(f"Received purchased_paid_media for unknown post_id: {post_id}")
            return

        star_amount = stars if (stars and stars > 0) else post.star_count

        # Idempotent record in purchases collection
        is_new, purchase = await self.purchases_repo.record_purchase(
            user_id=user_id,
            username=username,
            post_id=post.id,
            stars=star_amount
        )

        if is_new:
            # Increment stars earned on post
            await self.posts_repo.update_stars_earned(post.id, star_amount)
            await activity_repo.log(
                action="post_purchased",
                details=f"Buyer @{username or user_id} unlocked '{post.title}' for {star_amount} Stars",
                post_id=post.id,
                level="success"
            )
            logger.info(f"Recorded new purchase for post {post.id} by user {user_id} (@{username})")
        else:
            logger.info(f"Duplicate purchase event received for post {post.id} by user {user_id}. Skipping counter increment.")

        # Deliver hidden text with sendMessage (HTML, protect_content=True)
        if post.hidden_text:
            text_delivery = (
                "🎉 <b>Thank you for your purchase!</b>\n\n"
                "Here is your exclusive content:\n\n"
                f"{post.hidden_text}"
            )
            try:
                await self.tg_service.send_message(
                    chat_id=user_id,
                    text=text_delivery,
                    parse_mode="HTML",
                    protect_content=True
                )
                logger.info(f"Delivered hidden text to user {user_id} for post {post.id}")
            except Exception as exc:
                logger.error(f"Failed to deliver hidden text to user {user_id}: {exc}")


bot_listener = BotListenerService()
