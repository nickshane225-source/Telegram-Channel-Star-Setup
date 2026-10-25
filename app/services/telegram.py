import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import httpx
from loguru import logger
from app.config import settings
from app.utils.sanitizer import sanitize_telegram_html


class TelegramAPIError(Exception):
    """Base exception for Telegram Bot API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.data = data or {}


class TelegramRateLimitError(TelegramAPIError):
    """Raised when Telegram returns HTTP 429 Too Many Requests."""
    def __init__(self, retry_after: int, message: str = "Rate limited by Telegram"):
        super().__init__(f"{message}. Retry after {retry_after}s", status_code=429)
        self.retry_after = retry_after


class TelegramAuthError(TelegramAPIError):
    """Raised when Bot Token is invalid or unauthorized."""
    pass


class TelegramPermissionError(TelegramAPIError):
    """Raised when the bot lacks required channel permissions."""
    pass


def normalize_channel_id(channel_id: Optional[str]) -> str:
    """Normalize Telegram channel username or numeric ID.
    If the user enters digits without a '-' minus sign, automatically prepends '-'."""
    if not channel_id:
        return ""
    val = str(channel_id).strip()
    if val.startswith("@") or val.startswith("-"):
        return val
    # If purely digits, Telegram channel IDs are always negative with -100 prefix
    if val.isdigit():
        return f"-{val}" if val.startswith("100") else f"-100{val}"
    return val


class TelegramService:
    """Async wrapper for Telegram Bot API with rate limiting, retries, and typed exceptions."""

    def __init__(self, token: Optional[str] = None, base_url: str = "https://api.telegram.org"):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self.base_url = base_url.rstrip("/")

    @property
    def api_url(self) -> str:
        tok = self.token or settings.TELEGRAM_BOT_TOKEN
        if not tok:
            raise TelegramAuthError("Telegram Bot Token is not configured")
        return f"{self.base_url}/bot{tok}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
        request_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute an API request with automatic 429 retry handling."""
        url = f"{self.api_url}/{endpoint}"
        t_out = request_timeout or 60.0

        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=t_out) as client:
                    if files:
                        resp = await client.request(method, url, data=data, files=files)
                    else:
                        resp = await client.request(method, url, json=data)

                # Check HTTP status
                if resp.status_code == 429:
                    payload = resp.json() if resp.content else {}
                    retry_after = payload.get("parameters", {}).get("retry_after", 2)
                    if attempt < max_retries:
                        logger.warning(
                            f"Telegram HTTP 429 rate limit hit for {endpoint}. "
                            f"Waiting {retry_after}s before retry ({attempt + 1}/{max_retries})..."
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    raise TelegramRateLimitError(retry_after)

                payload = resp.json()
                if not payload.get("ok"):
                    error_code = payload.get("error_code")
                    description = payload.get("description", "Unknown Telegram API error")

                    if error_code == 401:
                        raise TelegramAuthError(f"Invalid Telegram Bot Token: {description}")
                    if error_code in (400, 403) and any(kw in description.lower() for kw in [
                        "not enough rights", "chat not found", "need administrator rights", "bot was kicked", "is not an administrator"
                    ]):
                        raise TelegramPermissionError(f"Channel permission error: {description}")

                    if error_code == 429:
                        retry_after = payload.get("parameters", {}).get("retry_after", 2)
                        if attempt < max_retries:
                            logger.warning(f"Telegram API 429 error. Waiting {retry_after}s...")
                            await asyncio.sleep(retry_after)
                            continue
                        raise TelegramRateLimitError(retry_after, description)

                    raise TelegramAPIError(description, status_code=error_code, data=payload)

                return payload.get("result", {})

            except (TelegramAPIError, TelegramAuthError, TelegramPermissionError):
                raise
            except httpx.RequestError as exc:
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(f"Network error calling Telegram ({exc}). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                raise TelegramAPIError(f"Network communication failure: {exc}")

        raise TelegramAPIError("Maximum retries exceeded")

    async def get_me(self) -> Dict[str, Any]:
        """Fetch bot's profile information."""
        return await self._request("GET", "getMe")

    async def get_chat_member(self, chat_id: str, user_id: int) -> Dict[str, Any]:
        """Get member status and permissions for a user or the bot in a channel."""
        return await self._request("POST", "getChatMember", data={"chat_id": chat_id, "user_id": user_id})

    async def test_connection(self, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Comprehensive diagnostic test for bot token and channel administrator permissions.
        Returns a structured report dictionary.
        """
        report: Dict[str, Any] = {
            "token_valid": False,
            "bot_id": None,
            "bot_username": None,
            "bot_first_name": None,
            "channel_valid": False,
            "channel_title": None,
            "is_admin": False,
            "status": None,
            "can_post_messages": False,
            "can_edit_messages": False,
            "can_delete_messages": False,
            "ready_to_publish": False,
            "messages": [],
        }

        # Step 1: Verify Bot Token via getMe
        try:
            bot_info = await self.get_me()
            report["token_valid"] = True
            report["bot_id"] = bot_info.get("id")
            report["bot_username"] = bot_info.get("username")
            report["bot_first_name"] = bot_info.get("first_name")
            report["messages"].append(f"Bot connected: @{bot_info.get('username')}")
        except TelegramAuthError as e:
            report["messages"].append(f"Token Error: {e.message}")
            return report
        except Exception as e:
            report["messages"].append(f"Connection Error: {e}")
            return report

        target_channel = normalize_channel_id(channel_id or settings.TELEGRAM_CHANNEL_ID)
        if not target_channel:
            report["messages"].append("No channel configured. Please set Channel ID or @username in settings.")
            return report

        # Step 2: Check Chat Member & Permissions
        try:
            member = await self.get_chat_member(chat_id=target_channel, user_id=report["bot_id"])
            status = member.get("status")
            report["channel_valid"] = True
            report["status"] = status

            if status == "creator":
                report["is_admin"] = True
                report["can_post_messages"] = True
                report["can_edit_messages"] = True
                report["can_delete_messages"] = True
                report["ready_to_publish"] = True
                report["messages"].append("Bot is the channel Creator with full administrative permissions.")
            elif status == "administrator":
                report["is_admin"] = True
                can_post = bool(member.get("can_post_messages", False))
                can_edit = bool(member.get("can_edit_messages", False))
                can_delete = bool(member.get("can_delete_messages", False))

                report["can_post_messages"] = can_post
                report["can_edit_messages"] = can_edit
                report["can_delete_messages"] = can_delete

                missing = []
                if not can_post:
                    missing.append("Post Messages")
                if not can_edit:
                    missing.append("Edit Messages")
                if not can_delete:
                    missing.append("Delete Messages")

                if not missing:
                    report["ready_to_publish"] = True
                    report["messages"].append("Bot is Channel Admin with all required posting/editing/deleting rights.")
                else:
                    report["messages"].append(f"Bot is Admin, but missing permissions: {', '.join(missing)}.")
            else:
                report["messages"].append(f"Bot status in channel is '{status}'. It must be an Administrator.")
        except TelegramPermissionError as e:
            report["messages"].append(f"Permission error: {e.message}. Ensure the bot has been added to the channel.")
        except Exception as e:
            report["messages"].append(f"Channel check failed: {e}")

        return report

    async def send_paid_media(
        self,
        chat_id: Union[str, int],
        star_count: int,
        photo_paths: List[str],
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
        payload: Optional[str] = None,
    ) -> Tuple[int, List[str]]:
        """
        Send 1 to 10 photos as paid media unlocking for star_count Telegram Stars.
        Uses Telegram Bot API sendPaidMedia method.
        Returns: (telegram_message_id, list_of_file_ids)
        """
        if not (1 <= len(photo_paths) <= 10):
            raise ValueError(f"sendPaidMedia supports between 1 and 10 photos (got {len(photo_paths)})")
        if not (1 <= star_count <= 10000):
            raise ValueError(f"star_count must be between 1 and 10000 (got {star_count})")

        media_array = []
        files_dict = {}
        opened_files = []

        try:
            for idx, p_str in enumerate(photo_paths):
                path = Path(p_str)
                if not path.exists():
                    raise FileNotFoundError(f"Media file not found: {p_str}")

                attach_name = f"photo_{idx}"
                media_array.append({
                    "type": "photo",
                    "media": f"attach://{attach_name}",
                })

                # Open file in binary mode
                f = open(path, "rb")
                opened_files.append(f)
                files_dict[attach_name] = (path.name, f, "image/jpeg")

            target_chat_id = str(chat_id)
            if target_chat_id.startswith("@") or target_chat_id.startswith("-") or not target_chat_id.isdigit():
                target_chat_id = normalize_channel_id(target_chat_id)

            form_data: Dict[str, Any] = {
                "chat_id": target_chat_id,
                "star_count": str(star_count),
                "media": json.dumps(media_array),
            }
            if caption:
                form_data["caption"] = sanitize_telegram_html(caption)
                form_data["parse_mode"] = parse_mode
            if payload:
                form_data["payload"] = str(payload)[:128]

            result = await self._request("POST", "sendPaidMedia", data=form_data, files=files_dict)

            message_id = result.get("message_id")
            file_ids: List[str] = []

            # Extract Telegram file IDs from paid_media result if present
            paid_media_items = result.get("paid_media", {}).get("paid_media", [])
            for item in paid_media_items:
                photos = item.get("photo", [])
                if photos:
                    # Best resolution is usually the last element
                    file_ids.append(photos[-1].get("file_id", ""))

            logger.info(f"Successfully published paid media to {target_chat_id}, message_id={message_id}")
            return message_id, file_ids

        finally:
            for f in opened_files:
                try:
                    f.close()
                except Exception:
                    pass

    async def edit_message_caption(
        self,
        chat_id: Union[str, int],
        message_id: int,
        caption: str,
        parse_mode: str = "HTML",
    ) -> bool:
        """Edit caption of an existing published message."""
        target_chat = str(chat_id)
        if target_chat.startswith("@") or target_chat.startswith("-") or not target_chat.isdigit():
            target_chat = normalize_channel_id(target_chat)

        data = {
            "chat_id": target_chat,
            "message_id": message_id,
            "caption": sanitize_telegram_html(caption),
            "parse_mode": parse_mode,
        }
        await self._request("POST", "editMessageCaption", data=data)
        return True

    async def delete_message(self, chat_id: Union[str, int], message_id: int) -> bool:
        """Delete message from the Telegram channel."""
        target_chat = str(chat_id)
        if target_chat.startswith("@") or target_chat.startswith("-") or not target_chat.isdigit():
            target_chat = normalize_channel_id(target_chat)

        data = {
            "chat_id": target_chat,
            "message_id": message_id,
        }
        await self._request("POST", "deleteMessage", data=data)
        return True

    async def send_message(
        self,
        chat_id: Union[str, int],
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        protect_content: bool = False,
    ) -> Dict[str, Any]:
        """Send a text message (used for delivering hidden text or channel teaser button)."""
        target_chat = str(chat_id)
        if target_chat.startswith("@") or target_chat.startswith("-") or not target_chat.isdigit():
            target_chat = normalize_channel_id(target_chat)

        data: Dict[str, Any] = {
            "chat_id": target_chat,
            "text": text,
            "protect_content": protect_content,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = reply_markup

        return await self._request("POST", "sendMessage", data=data)

    async def send_photo(
        self,
        chat_id: Union[str, int],
        photo_path: str,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a single photo (used for Mode B channel teaser or user DM preview)."""
        target_chat = str(chat_id)
        if target_chat.startswith("@") or target_chat.startswith("-") or not target_chat.isdigit():
            target_chat = normalize_channel_id(target_chat)

        path = Path(photo_path)
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {photo_path}")

        form_data: Dict[str, Any] = {"chat_id": target_chat}
        if caption:
            form_data["caption"] = sanitize_telegram_html(caption)
            form_data["parse_mode"] = parse_mode
        if reply_markup:
            form_data["reply_markup"] = json.dumps(reply_markup)

        with open(path, "rb") as f:
            files = {"photo": (path.name, f, "image/jpeg")}
            return await self._request("POST", "sendPhoto", data=form_data, files=files)

    async def send_media_group(
        self,
        chat_id: Union[str, int],
        photo_paths: List[str],
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
    ) -> List[Dict[str, Any]]:
        """Send multiple photos as an album (used for multiple teaser photos or resending purchased photos)."""
        target_chat = str(chat_id)
        if target_chat.startswith("@") or target_chat.startswith("-") or not target_chat.isdigit():
            target_chat = normalize_channel_id(target_chat)

        media_array = []
        files_dict = {}
        opened_files = []

        try:
            for idx, p_str in enumerate(photo_paths):
                path = Path(p_str)
                if not path.exists():
                    raise FileNotFoundError(f"Media file not found: {p_str}")

                attach_name = f"photo_{idx}"
                item_dict: Dict[str, Any] = {
                    "type": "photo",
                    "media": f"attach://{attach_name}",
                }
                # Put caption on the first photo if provided
                if idx == 0 and caption:
                    item_dict["caption"] = sanitize_telegram_html(caption)
                    item_dict["parse_mode"] = parse_mode

                media_array.append(item_dict)
                f = open(path, "rb")
                opened_files.append(f)
                files_dict[attach_name] = (path.name, f, "image/jpeg")

            form_data = {
                "chat_id": target_chat,
                "media": json.dumps(media_array),
            }
            return await self._request("POST", "sendMediaGroup", data=form_data, files=files_dict)
        finally:
            for f in opened_files:
                try:
                    f.close()
                except Exception:
                    pass

    async def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        """Delete active webhook before beginning getUpdates long-polling."""
        data = {"drop_pending_updates": drop_pending_updates}
        await self._request("POST", "deleteWebhook", data=data)
        logger.info("Successfully deleted Telegram webhook.")
        return True

    async def get_updates(
        self,
        offset: Optional[int] = None,
        limit: int = 100,
        timeout: int = 50,
        allowed_updates: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Long-poll getUpdates from Telegram Bot API."""
        data: Dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": allowed_updates or ["message", "purchased_paid_media"],
        }
        if offset is not None:
            data["offset"] = offset

        # Set HTTP client timeout higher than the long-poll timeout
        return await self._request("POST", "getUpdates", data=data, request_timeout=float(timeout + 15))

    async def get_star_transactions(self, offset: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch star transactions for the bot."""
        data = {"offset": offset, "limit": limit}
        result = await self._request("POST", "getStarTransactions", data=data)
        return result.get("transactions", [])


telegram_service = TelegramService()
