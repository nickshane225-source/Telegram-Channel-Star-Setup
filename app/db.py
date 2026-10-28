import asyncio
import re
from typing import Any, Dict, Optional, Tuple
import certifi
import dns.resolver
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import (
    ConfigurationError,
    ConnectionFailure,
    InvalidURI,
    OperationFailure,
    ServerSelectionTimeoutError,
)
from loguru import logger
from app.config import settings

client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None
db_connection_error: Optional[str] = None
db_error_category: Optional[str] = None


def mask_mongodb_uri(uri: str) -> str:
    """Mask password or credentials in a MongoDB URI so secrets are never logged or displayed."""
    if not uri or not uri.strip():
        return "<empty>"
    cleaned = uri.strip()
    if "://" not in cleaned:
        return "***"
    scheme, rest = cleaned.split("://", 1)
    if "@" not in rest:
        return f"{scheme}://{rest}"
    # Split at the LAST '@' because unescaped '@' might be inside the password
    userinfo, hostpart = rest.rsplit("@", 1)
    if ":" in userinfo:
        user, _ = userinfo.split(":", 1)
        return f"{scheme}://{user}:***@{hostpart}"
    return f"{scheme}://{userinfo}@{hostpart}"


def mask_secrets_in_text(text: str, uri: Optional[str] = None) -> str:
    """Ensure any raw URI or password from URI is masked inside an exception message."""
    if not text:
        return ""
    result = re.sub(
        r"(mongodb(?:\+srv)?://[^:\s/@]+:)([^@\s]+)(@)",
        r"\1***\3",
        str(text),
    )
    target_uri = uri or settings.MONGODB_URI
    if target_uri and "://" in target_uri and "@" in target_uri:
        _, rest = target_uri.split("://", 1)
        userinfo, _ = rest.rsplit("@", 1)
        if ":" in userinfo:
            _, raw_pwd = userinfo.split(":", 1)
            if raw_pwd and len(raw_pwd) >= 3 and raw_pwd in result:
                result = result.replace(raw_pwd, "***")
    return result


def validate_mongodb_uri(uri: str) -> Tuple[bool, Optional[str]]:
    """
    Validate MongoDB URI before connecting and return (is_valid, friendly_error).
    Checks for:
    - Empty URI
    - Placeholder text like <password>, <db_password>, <username>
    - mongodb+srv without a username
    - Unescaped special characters in the password (@ : / ? # [ ] %)
    """
    if not uri or not uri.strip():
        return False, "Invalid URI: MONGODB_URI is empty. Please set your MongoDB connection string in .env."

    raw = uri.strip()
    if not (raw.startswith("mongodb://") or raw.startswith("mongodb+srv://")):
        return (
            False,
            "Invalid URI: MONGODB_URI must start with 'mongodb://' or 'mongodb+srv://'.",
        )

    # Check for placeholder text like <password> or <db_password>
    placeholder_match = re.search(r"<[^>\s]*(?:password|username|user|cluster|host)[^>\s]*>", raw, re.IGNORECASE)
    if placeholder_match or "<password>" in raw.lower() or "<db_password>" in raw.lower():
        return (
            False,
            "Invalid URI: MONGODB_URI still contains placeholder text (such as <password> or <db_password>). "
            "Replace it with your real database user password in .env (without angle brackets).",
        )

    scheme, rest = raw.split("://", 1)

    # Check for credentials and unescaped special characters in password: @ : / ? # [ ] %
    if "@" in rest:
        full_userinfo, _ = rest.rsplit("@", 1)
        if ":" in full_userinfo:
            user, full_pwd = full_userinfo.split(":", 1)
        else:
            user, full_pwd = full_userinfo, ""

        if scheme == "mongodb+srv" and not user.strip():
            return (
                False,
                "Invalid URI: 'mongodb+srv://' connection string is missing a username before '@'.",
            )
        if scheme == "mongodb+srv" and not full_pwd:
            return (
                False,
                "Invalid URI: 'mongodb+srv://' connection string is missing a password.",
            )

        bad_chars = set(re.findall(r"[@:/?#\[\]]", full_pwd))
        invalid_pct = re.search(r"%(?![0-9A-Fa-f]{2})", full_pwd)
        if invalid_pct:
            bad_chars.add("%")
        if bad_chars:
            chars_str = " ".join(sorted(bad_chars))
            return (
                False,
                f"Invalid URI: Unescaped special character(s) ({chars_str}) detected in the MongoDB password. "
                "Please URL-encode your password using urllib.parse.quote_plus(password) before putting it in MONGODB_URI.",
            )
    elif scheme == "mongodb+srv":
        return (
            False,
            "Invalid URI: 'mongodb+srv://' connection string requires a database username and password "
            "(e.g. mongodb+srv://username:password@cluster.mongodb.net/starpost).",
        )

    return True, None


def classify_db_error(exc: Exception, uri: Optional[str] = None, srv_fallback_used: bool = False) -> Tuple[str, str]:
    """
    Classify a MongoDB connection exception into a friendly category and error message.
    Categories: auth, network_access, dns_srv, tls, timeout, invalid_uri
    """
    raw_msg = mask_secrets_in_text(str(exc), uri)
    low = raw_msg.lower()

    if isinstance(exc, InvalidURI) or "invalid uri" in low or "uri" in low and ("unescaped" in low or "userinfo" in low or "quote_plus" in low):
        return (
            "invalid_uri",
            f"Invalid URI: Could not parse MONGODB_URI ({raw_msg}). Ensure username and password are URL-encoded with urllib.parse.quote_plus.",
        )

    if isinstance(exc, OperationFailure) or "authentication failed" in low or "bad auth" in low or "auth error" in low or "code 8000" in low or "code 18" in low:
        return (
            "auth",
            f"Authentication failed: MongoDB rejected the database username or password ({raw_msg}). "
            "Check Database Access in MongoDB Atlas and URL-encode special characters in the password.",
        )

    if isinstance(exc, ConfigurationError) or "srv" in low or "_mongodb._tcp" in low or "dns" in low or "nameserver" in low or "resolution lifetime expired" in low:
        std_hint = (
            " Note: A standard (non-SRV) connection string (mongodb://host1:27017,host2:27017,host3:27017/?replicaSet=...) also works if your ISP blocks SRV records."
        )
        return (
            "dns_srv",
            f"DNS SRV lookup failed: Could not resolve the Atlas cluster SRV record ({raw_msg}).{std_hint}",
        )

    if "ssl" in low or "tls" in low or "certificate" in low or "handshake" in low or "certifi" in low:
        return (
            "tls",
            f"TLS certificate failed: SSL/TLS handshake with MongoDB Atlas failed ({raw_msg}). "
            "Ensure certifi is up to date and no firewall/antivirus is intercepting TLS traffic.",
        )

    if isinstance(exc, ServerSelectionTimeoutError):
        if "whitelist" in low or "not authorized" in low or "replicaSetNoPrimary" in low or "mongodb.net" in (uri or settings.MONGODB_URI).lower() or (uri or settings.MONGODB_URI).startswith("mongodb+srv"):
            return (
                "network_access",
                f"IP not in Atlas Network Access (or cluster unreachable): Timed out connecting to MongoDB Atlas ({raw_msg}). "
                "Add your current public IP address in MongoDB Atlas -> Security -> Network Access.",
            )
        return (
            "timeout",
            f"Connection timeout: Timed out waiting for MongoDB server response ({raw_msg}).",
        )

    if "timed out" in low or "timeout" in low:
        return (
            "timeout",
            f"Connection timeout: Could not reach MongoDB within the configured timeout ({raw_msg}).",
        )

    return (
        "network_access",
        f"Database connection failed: {raw_msg}. If using MongoDB Atlas, verify your IP is in Atlas Network Access.",
    )


def apply_fallback_dns_resolver() -> list[str]:
    """Configure dnspython default_resolver with fallback nameservers from settings.MONGODB_DNS_SERVERS."""
    servers = [s.strip() for s in settings.MONGODB_DNS_SERVERS.split(",") if s.strip()]
    if not servers:
        servers = ["8.8.8.8", "1.1.1.1"]
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = servers
    dns.resolver.default_resolver = resolver
    return servers


def _build_motor_kwargs(uri: str) -> Dict[str, Any]:
    """Build AsyncIOMotorClient keyword arguments for local or Atlas URI."""
    kwargs: Dict[str, Any] = {
        "serverSelectionTimeoutMS": settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": settings.MONGODB_CONNECT_TIMEOUT_MS,
        "maxPoolSize": settings.MONGODB_MAX_POOL_SIZE,
        "appname": "StarPostManager",
        "retryWrites": True,
    }
    if uri.startswith("mongodb+srv://") or "mongodb.net" in uri.lower():
        kwargs["tlsCAFile"] = certifi.where()
    return kwargs


async def _attempt_single_connection(uri: str) -> AsyncIOMotorClient:
    """Create a temporary client and ping the server, using SRV DNS fallback if needed."""
    kwargs = _build_motor_kwargs(uri)
    temp_client: Optional[AsyncIOMotorClient] = None
    try:
        temp_client = AsyncIOMotorClient(uri, **kwargs)
        await temp_client.admin.command("ping")
        return temp_client
    except Exception as exc:
        if temp_client is not None:
            try:
                temp_client.close()
            except Exception:
                pass
            temp_client = None

        # Check if SRV lookup failed (ConfigurationError or DNS timeout) and retry once with fallback DNS
        low_err = str(exc).lower()
        is_srv_error = isinstance(exc, ConfigurationError) and (
            "srv" in low_err or "dns" in low_err or "nameserver" in low_err or "lifetime" in low_err or "_mongodb._tcp" in low_err or uri.startswith("mongodb+srv://")
        )
        if is_srv_error and uri.startswith("mongodb+srv://"):
            servers = apply_fallback_dns_resolver()
            logger.warning(
                f"SRV DNS lookup failed with system DNS; retrying once with fallback DNS servers {servers}..."
            )
            retry_client: Optional[AsyncIOMotorClient] = None
            try:
                retry_client = AsyncIOMotorClient(uri, **kwargs)
                await retry_client.admin.command("ping")
                return retry_client
            except Exception as retry_exc:
                if retry_client is not None:
                    try:
                        retry_client.close()
                    except Exception:
                        pass
                raise retry_exc from exc
        raise


async def connect_to_db(max_attempts: int = 3) -> bool:
    """
    Initialize MongoDB connection and verify server reachability.
    Sets global `db` ONLY after a successful ping.
    Retries up to `max_attempts` times with backoff (1s, 3s, 7s).
    """
    global client, db, db_connection_error, db_error_category

    uri = settings.MONGODB_URI.strip()
    masked_uri = mask_mongodb_uri(uri)

    # 1. Pre-validate URI
    is_valid, validation_err = validate_mongodb_uri(uri)
    if not is_valid:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        client = None
        db = None
        db_error_category = "invalid_uri"
        db_connection_error = validation_err
        logger.error(f"MongoDB URI validation failed ({masked_uri}): {validation_err}")
        return False

    backoff_delays = [1, 3, 7]
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"Connecting to MongoDB at {masked_uri} (attempt {attempt}/{max_attempts})...")
            verified_client = await _attempt_single_connection(uri)

            # Close previous client if any
            if client is not None and client is not verified_client:
                try:
                    client.close()
                except Exception:
                    pass

            client = verified_client
            db = client[settings.DB_NAME]
            db_connection_error = None
            db_error_category = None
            logger.info(f"Successfully connected to MongoDB database '{settings.DB_NAME}' at {masked_uri}")
            return True

        except (ServerSelectionTimeoutError, ConnectionFailure, ConfigurationError, OperationFailure, InvalidURI, Exception) as exc:
            last_exc = exc
            category, friendly_msg = classify_db_error(exc, uri)
            db_error_category = category
            db_connection_error = friendly_msg
            logger.warning(f"MongoDB connection attempt {attempt}/{max_attempts} failed: {friendly_msg}")

            # Ensure global db and client are not left pointing to an unverified client
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            client = None
            db = None

            # Do not retry if URI is invalid or credentials failed
            if category == "invalid_uri":
                break

            if attempt < max_attempts:
                delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
                logger.info(f"Retrying MongoDB connection in {delay}s...")
                await asyncio.sleep(delay)

    logger.error(f"Failed to connect to MongoDB after {max_attempts} attempt(s): {db_connection_error}")
    return False


async def close_db_connection() -> None:
    """Close MongoDB client connection."""
    global client, db
    if client:
        client.close()
        client = None
        db = None
        logger.info("MongoDB connection closed.")


def get_db() -> Optional[AsyncIOMotorDatabase]:
    """Return database instance or None if not connected."""
    return db


def get_db_error() -> Optional[str]:
    """Return connection error string if any."""
    return db_connection_error


def get_db_error_category() -> Optional[str]:
    """Return classified error category if any."""
    return db_error_category


async def init_indexes() -> None:
    """Create essential indexes for performance and constraints, wrapping each in its own try/except."""
    if db is None:
        return

    index_specs = [
        ("posts", [("status", 1), ("scheduled_at", 1)], {}),
        ("posts", [("telegram_message_id", 1)], {}),
        ("posts", [("deleted_at", 1)], {}),
        ("posts", [("created_at", -1)], {}),
        ("sales", [("date", -1)], {}),
        ("sales", [("transaction_id", 1)], {"unique": True}),
        ("sales", [("telegram_message_id", 1)], {}),
        ("purchases", [("user_id", 1), ("post_id", 1)], {"unique": True}),
        ("purchases", [("post_id", 1)], {}),
        ("purchases", [("created_at", -1)], {}),
        ("bot_state", [("key", 1)], {"unique": True}),
        ("templates", [("title", 1)], {}),
        ("feature_flags", [("key", 1)], {"unique": True}),
        ("activity_log", [("created_at", -1)], {}),
        ("settings", [("key", 1)], {"unique": True}),
    ]

    failures = 0
    for coll_name, keys, kwargs in index_specs:
        try:
            coll = getattr(db, coll_name)
            await coll.create_index(keys, **kwargs)
        except Exception as exc:
            failures += 1
            is_unique = kwargs.get("unique", False)
            hint = (
                " (Hint: duplicate data exists, run scripts/migrate_local_to_atlas.py with --report-duplicates or clean the collection)"
                if is_unique
                else ""
            )
            logger.warning(
                f"Failed to create index {keys} on collection '{coll_name}': {mask_secrets_in_text(str(exc))}{hint}"
            )

    if failures == 0:
        logger.info("MongoDB indexes verified successfully.")
    else:
        logger.warning(f"MongoDB index initialization completed with {failures} warning(s).")
