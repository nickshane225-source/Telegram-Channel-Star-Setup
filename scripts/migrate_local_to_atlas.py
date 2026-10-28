#!/usr/bin/env python3
"""
Migrate local MongoDB data to MongoDB Atlas (or another target database).
Default mode is a DRY RUN. Pass --apply to perform actual writes.
Never logs or prints connection secrets.
"""
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import mask_mongodb_uri, mask_secrets_in_text

DEFAULT_COLLECTIONS = [
    "posts",
    "sales",
    "purchases",
    "templates",
    "settings",
    "feature_flags",
    "activity_log",
    "bot_state",
]

# Unique index fields per collection (beyond _id)
UNIQUE_KEY_SPECS: Dict[str, List[str]] = {
    "sales": ["transaction_id"],
    "purchases": ["user_id", "post_id"],
    "settings": ["key"],
    "feature_flags": ["key"],
    "bot_state": ["key"],
}


def extract_host_and_db(uri: str, explicit_db: Optional[str] = None) -> Tuple[str, str]:
    """Extract normalized host authority and database name from a MongoDB URI."""
    cleaned = (uri or "").strip()
    scheme_split = cleaned.split("://", 1)
    rest = scheme_split[1] if len(scheme_split) == 2 else cleaned

    # Strip userinfo if present
    if "@" in rest:
        _, rest = rest.rsplit("@", 1)

    parts = rest.split("/", 1)
    host_part = parts[0].strip().lower()
    # Normalize 127.0.0.1 to localhost for comparison
    host_part = host_part.replace("127.0.0.1", "localhost")
    if ":" not in host_part and not cleaned.startswith("mongodb+srv://"):
        host_part = f"{host_part}:27017"

    db_from_uri = ""
    if len(parts) > 1:
        db_from_uri = parts[1].split("?", 1)[0].strip()

    final_db = (explicit_db or db_from_uri or "starpost").strip()
    return host_part, final_db


def is_same_target(
    source_uri: str,
    source_db_name: str,
    dest_uri: str,
    dest_db_name: str,
    source_client: Optional[Any] = None,
    dest_client: Optional[Any] = None,
) -> bool:
    """Return True if source and destination point to the same server and database."""
    if source_client is not None and dest_client is not None and source_client is dest_client:
        return source_db_name == dest_db_name

    src_host, src_db = extract_host_and_db(source_uri, source_db_name)
    dst_host, dst_db = extract_host_and_db(dest_uri, dest_db_name)
    return src_host == dst_host and src_db == dst_db


def find_duplicates_in_collection(collection: Any, key_fields: List[str]) -> List[Dict[str, Any]]:
    """Find duplicate key groups in a collection for --report-duplicates."""
    docs = list(collection.find({}))
    seen: Dict[Tuple[Any, ...], List[Any]] = {}
    for d in docs:
        key_tuple = tuple(d.get(k) for k in key_fields)
        if any(v is not None for v in key_tuple):
            seen.setdefault(key_tuple, []).append(d.get("_id"))

    duplicates = []
    for k_tuple, ids in seen.items():
        if len(ids) > 1:
            duplicates.append({
                "key": dict(zip(key_fields, k_tuple)),
                "count": len(ids),
                "ids": ids,
            })
    return duplicates


def ensure_destination_unique_indexes(dest_db: Any, collections: List[str]) -> None:
    """Ensure unique indexes exist on the destination database before upserting."""
    index_map = {
        "sales": ([("transaction_id", 1)], {"unique": True}),
        "purchases": ([("user_id", 1), ("post_id", 1)], {"unique": True}),
        "settings": ([("key", 1)], {"unique": True}),
        "feature_flags": ([("key", 1)], {"unique": True}),
        "bot_state": ([("key", 1)], {"unique": True}),
    }
    for coll_name in collections:
        if coll_name in index_map:
            keys, kwargs = index_map[coll_name]
            try:
                dest_db[coll_name].create_index(keys, **kwargs)
            except Exception:
                pass


def has_unique_conflict(dest_coll: Any, coll_name: str, doc: Dict[str, Any]) -> bool:
    """Check if another document with a different _id already has the same unique key in dest_coll."""
    key_fields = UNIQUE_KEY_SPECS.get(coll_name)
    if not key_fields:
        return False
    query: Dict[str, Any] = {}
    for k in key_fields:
        if k not in doc or doc[k] is None:
            return False
        query[k] = doc[k]
    existing = dest_coll.find_one(query)
    if existing and existing.get("_id") != doc.get("_id"):
        return True
    return False


def run_migration(
    source_db: Any,
    dest_db: Any,
    collections: Optional[List[str]] = None,
    batch_size: int = 500,
    apply: bool = False,
    report_duplicates: bool = False,
    source_uri: str = "mongodb://localhost:27017/starpost",
    dest_uri: str = "mongodb+srv://user:***@cluster.mongodb.net/starpost",
    source_db_name: Optional[str] = None,
    dest_db_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute migration (or dry run) between source_db and dest_db.
    Returns structured summary dict for programmatic verification and CLI printing.
    """
    s_name = source_db_name or getattr(source_db, "name", "starpost")
    d_name = dest_db_name or getattr(dest_db, "name", "starpost")

    src_client = getattr(source_db, "client", None)
    dst_client = getattr(dest_db, "client", None)

    if is_same_target(source_uri, s_name, dest_uri, d_name, src_client, dst_client):
        raise ValueError(
            f"Refusing to run: source ({mask_mongodb_uri(source_uri)} / {s_name}) "
            f"and destination ({mask_mongodb_uri(dest_uri)} / {d_name}) are the same server and database."
        )

    target_collections = collections or DEFAULT_COLLECTIONS
    mode_label = "APPLY (LIVE WRITE)" if apply else "DRY RUN (READ-ONLY)"

    print("=" * 72)
    print(f"  StarPost Manager - Database Migration [{mode_label}]")
    print("=" * 72)
    print(f"  Source      : {mask_mongodb_uri(source_uri)} (db: {s_name})")
    print(f"  Destination : {mask_mongodb_uri(dest_uri)} (db: {d_name})")
    print(f"  Batch Size  : {batch_size}")
    print("=" * 72)

    duplicate_reports: Dict[str, List[Dict[str, Any]]] = {}
    if report_duplicates:
        print("\n[Duplicate Key Report]")
        for coll_name in target_collections:
            if coll_name in UNIQUE_KEY_SPECS:
                dups = find_duplicates_in_collection(source_db[coll_name], UNIQUE_KEY_SPECS[coll_name])
                duplicate_reports[coll_name] = dups
                if dups:
                    print(f"  - {coll_name}: {len(dups)} duplicate key group(s) found:")
                    for d in dups:
                        print(f"      key={d['key']} count={d['count']} ids={d['ids']}")
                else:
                    print(f"  - {coll_name}: 0 duplicates")

    if apply:
        ensure_destination_unique_indexes(dest_db, target_collections)

    summary: Dict[str, Dict[str, int]] = {}

    for coll_name in target_collections:
        src_coll = source_db[coll_name]
        dst_coll = dest_db[coll_name]

        src_count = src_coll.count_documents({})
        dst_before = dst_coll.count_documents({})

        copied = 0
        skipped_duplicates = 0
        failed = 0

        if not apply:
            # Dry run: inspect documents to see what would be copied vs skipped
            cursor = src_coll.find({})
            seen_unique_in_batch = set()
            key_fields = UNIQUE_KEY_SPECS.get(coll_name)
            for doc in cursor:
                if key_fields:
                    k_tuple = tuple(doc.get(k) for k in key_fields)
                    if k_tuple in seen_unique_in_batch or has_unique_conflict(dst_coll, coll_name, doc):
                        skipped_duplicates += 1
                        continue
                    seen_unique_in_batch.add(k_tuple)
                copied += 1
            print(f"  [DRY RUN] {coll_name:<15} : source={src_count:<5} dest={dst_before:<5} would_upsert={copied:<5} would_skip_dup={skipped_duplicates}")
        else:
            cursor = src_coll.find({})
            batch: List[Dict[str, Any]] = []
            for doc in cursor:
                batch.append(doc)
                if len(batch) >= batch_size:
                    c, s, f = _upsert_batch(dst_coll, coll_name, batch)
                    copied += c
                    skipped_duplicates += s
                    failed += f
                    batch.clear()
            if batch:
                c, s, f = _upsert_batch(dst_coll, coll_name, batch)
                copied += c
                skipped_duplicates += s
                failed += f
                batch.clear()

        dst_after = dst_coll.count_documents({})
        summary[coll_name] = {
            "source_count": src_count,
            "dest_before": dst_before,
            "copied": copied,
            "skipped_duplicates": skipped_duplicates,
            "failed": failed,
            "dest_after": dst_after,
        }

    print("\n" + "=" * 72)
    print(f"{'Collection':<16} | {'Source':>8} | {'Copied':>8} | {'Skipped':>8} | {'Failed':>6} | {'Dest Count':>10}")
    print("-" * 72)
    for coll_name, stats in summary.items():
        print(
            f"{coll_name:<16} | {stats['source_count']:>8} | {stats['copied']:>8} | "
            f"{stats['skipped_duplicates']:>8} | {stats['failed']:>6} | {stats['dest_after']:>10}"
        )
    print("=" * 72)
    if not apply:
        print("NOTE: This was a DRY RUN. No changes were written to the destination database.")
        print("      Re-run with --apply to copy documents.")
    print("NOTE: Media files in media/ stay on this PC and are not stored in MongoDB.")

    return {
        "apply": apply,
        "collections": summary,
        "duplicates": duplicate_reports,
    }


def _upsert_batch(dst_coll: Any, coll_name: str, batch: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Upsert a batch of documents by _id while skipping unique index conflicts."""
    copied = 0
    skipped = 0
    failed = 0

    for doc in batch:
        doc_id = doc.get("_id")
        try:
            if has_unique_conflict(dst_coll, coll_name, doc):
                skipped += 1
                continue
            dst_coll.replace_one({"_id": doc_id}, doc, upsert=True)
            copied += 1
        except DuplicateKeyError:
            skipped += 1
        except Exception as exc:
            failed += 1
            print(f"  [WARN] Failed to upsert document _id={doc_id} in {coll_name}: {mask_secrets_in_text(str(exc))}")

    return copied, skipped, failed


def build_mongo_client(uri: str) -> MongoClient:
    """Build a synchronous PyMongo client with certifi CA bundle if needed."""
    kwargs: Dict[str, Any] = {
        "serverSelectionTimeoutMS": 20000,
        "connectTimeoutMS": 20000,
        "appname": "StarPostMigration",
        "retryWrites": True,
    }
    if uri.startswith("mongodb+srv://") or "mongodb.net" in uri.lower():
        kwargs["tlsCAFile"] = certifi.where()
    return MongoClient(uri, **kwargs)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate StarPost Manager data from local MongoDB to Atlas.")
    parser.add_argument(
        "--source",
        default="mongodb://localhost:27017/starpost",
        help="Source MongoDB URI (default: mongodb://localhost:27017/starpost)",
    )
    parser.add_argument(
        "--source-db",
        default="starpost",
        help="Source database name (default: starpost)",
    )
    parser.add_argument(
        "--collections",
        default="all",
        help="Comma-separated list of collections or 'all'",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for upserts (default: 500)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes to destination (default is dry-run)",
    )
    parser.add_argument(
        "--report-duplicates",
        action="store_true",
        help="Scan and report duplicate unique keys in source collections",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(ROOT_DIR / ".env")
    args = parse_args(argv)

    dest_uri = os.environ.get("MONGODB_URI", "").strip()
    dest_db_name = os.environ.get("DB_NAME", "starpost").strip() or "starpost"

    if not dest_uri:
        print("[ERROR] MONGODB_URI is not set in environment or .env.")
        return 1

    if args.collections.strip().lower() == "all":
        collections = DEFAULT_COLLECTIONS
    else:
        collections = [c.strip() for c in args.collections.split(",") if c.strip()]

    if is_same_target(args.source, args.source_db, dest_uri, dest_db_name):
        print(
            f"[ERROR] Refusing to run: source ({mask_mongodb_uri(args.source)} / {args.source_db}) "
            f"and destination ({mask_mongodb_uri(dest_uri)} / {dest_db_name}) are the same server and database."
        )
        return 1

    try:
        src_client = build_mongo_client(args.source)
        dst_client = build_mongo_client(dest_uri)
        src_db = src_client[args.source_db]
        dst_db = dst_client[dest_db_name]

        run_migration(
            source_db=src_db,
            dest_db=dst_db,
            collections=collections,
            batch_size=args.batch_size,
            apply=args.apply,
            report_duplicates=args.report_duplicates,
            source_uri=args.source,
            dest_uri=dest_uri,
            source_db_name=args.source_db,
            dest_db_name=dest_db_name,
        )
        return 0
    except Exception as exc:
        print(f"[ERROR] Migration failed: {mask_secrets_in_text(str(exc), dest_uri)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
