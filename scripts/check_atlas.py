#!/usr/bin/env python3
"""
Step-by-step MongoDB / Atlas diagnostic script for StarPost Manager.
Prints OK/FAIL and a fix hint for each step:
1. URI parsed (masked)
2. DNS SRV lookup (and fallback resolvers)
3. TCP connect to hosts on port 27017
4. TLS handshake with certifi
5. Authentication & Ping
6. Round-trip latency (average of 5 pings in ms)
7. List collections with document counts
8. Write/read/delete of a test document in _healthcheck
Exits with code 0 only if all steps pass. Never prints the password.
"""
import os
import socket
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
import certifi
import dns.resolver
from dotenv import load_dotenv
from pymongo import MongoClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import (
    apply_fallback_dns_resolver,
    mask_mongodb_uri,
    mask_secrets_in_text,
    validate_mongodb_uri,
)


def print_step(step_num: int, title: str, ok: bool, detail: str, hint: Optional[str] = None) -> None:
    status = "OK" if ok else "FAIL"
    print(f"[{status:^4}] Step {step_num}: {title} -> {detail}")
    if not ok and hint:
        print(f"       Fix Hint: {hint}")


def extract_srv_domain_or_hosts(uri: str) -> Tuple[bool, Optional[str], List[Tuple[str, int]]]:
    """Return (is_srv, srv_domain, [(host, port), ...]) from URI."""
    cleaned = uri.strip()
    scheme, rest = cleaned.split("://", 1)
    if "@" in rest:
        _, rest = rest.rsplit("@", 1)
    host_section = rest.split("/", 1)[0].split("?", 1)[0].strip()

    if scheme == "mongodb+srv":
        return True, host_section, []

    hosts: List[Tuple[str, int]] = []
    for item in host_section.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            h, p_str = item.rsplit(":", 1)
            try:
                p = int(p_str)
            except ValueError:
                p = 27017
            hosts.append((h, p))
        else:
            hosts.append((item, 27017))
    return False, None, hosts


def resolve_srv_hosts(srv_domain: str) -> Tuple[bool, List[Tuple[str, int]], str]:
    """Resolve _mongodb._tcp.<srv_domain> using default DNS, falling back to MONGODB_DNS_SERVERS."""
    record_name = f"_mongodb._tcp.{srv_domain}"
    try:
        answers = dns.resolver.resolve(record_name, "SRV")
        hosts = [(str(r.target).rstrip("."), int(r.port)) for r in answers]
        return True, hosts, f"Resolved {len(hosts)} host(s) via system DNS: {', '.join(f'{h}:{p}' for h, p in hosts)}"
    except Exception as first_exc:
        fallback_servers = apply_fallback_dns_resolver()
        try:
            answers = dns.resolver.resolve(record_name, "SRV")
            hosts = [(str(r.target).rstrip("."), int(r.port)) for r in answers]
            return (
                True,
                hosts,
                f"System DNS failed ({first_exc}); resolved {len(hosts)} host(s) via fallback DNS {fallback_servers}: "
                f"{', '.join(f'{h}:{p}' for h, p in hosts)}",
            )
        except Exception as second_exc:
            return (
                False,
                [],
                f"SRV lookup for {record_name} failed on system DNS ({first_exc}) and fallback DNS {fallback_servers} ({second_exc})",
            )


def check_tcp_connectivity(hosts: List[Tuple[str, int]], timeout: float = 5.0) -> Tuple[bool, str]:
    """Attempt TCP socket connection to each host:port."""
    if not hosts:
        return False, "No hosts available to test TCP connection."
    results = []
    any_ok = False
    for host, port in hosts:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ms = (time.perf_counter() - t0) * 1000
                results.append(f"{host}:{port} (OK, {ms:.1f}ms)")
                any_ok = True
        except Exception as exc:
            results.append(f"{host}:{port} (FAIL: {exc})")
    return any_ok, "; ".join(results)


def check_tls_handshake(hosts: List[Tuple[str, int]], timeout: float = 5.0) -> Tuple[bool, str]:
    """Perform TLS handshake using certifi CA bundle against the first reachable host."""
    if not hosts:
        return False, "No hosts available for TLS handshake."
    ctx = ssl.create_default_context(cafile=certifi.where())
    for host, port in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                    cipher = ssock.cipher()
                    version = ssock.version()
                    return True, f"TLS handshake succeeded with {host}:{port} ({version}, cipher={cipher[0] if cipher else 'unknown'})"
        except Exception as exc:
            last_err = f"{host}:{port} -> {exc}"
    return False, f"TLS handshake failed: {last_err}"


def run_diagnostics(uri: Optional[str] = None, db_name: Optional[str] = None) -> int:
    load_dotenv(ROOT_DIR / ".env")
    target_uri = (uri if uri is not None else os.environ.get("MONGODB_URI", "")).strip()
    target_db = (db_name if db_name is not None else os.environ.get("DB_NAME", "starpost")).strip() or "starpost"

    masked = mask_mongodb_uri(target_uri)
    print("=" * 72)
    print("  StarPost Manager - MongoDB Atlas Diagnostics")
    print("=" * 72)
    print(f"  Target URI (masked) : {masked}")
    print(f"  Target Database     : {target_db}")
    print("=" * 72)

    all_passed = True

    # Step 1: URI parsed & validated
    valid, val_err = validate_mongodb_uri(target_uri)
    if not valid:
        print_step(
            1,
            "URI Parse & Validation",
            False,
            mask_secrets_in_text(val_err or "Invalid URI", target_uri),
            "Update MONGODB_URI in .env. Replace <password> and URL-encode special characters using urllib.parse.quote_plus.",
        )
        return 1
    print_step(1, "URI Parse & Validation", True, f"Valid format ({masked})")

    # Step 2: DNS SRV Lookup
    is_srv, srv_domain, hosts = extract_srv_domain_or_hosts(target_uri)
    if is_srv and srv_domain:
        srv_ok, hosts, srv_detail = resolve_srv_hosts(srv_domain)
        print_step(
            2,
            "DNS SRV Lookup",
            srv_ok,
            srv_detail,
            "Set MONGODB_DNS_SERVERS=8.8.8.8,1.1.1.1 in .env, change Windows DNS to 8.8.8.8, or use a standard (non-SRV) mongodb:// connection string from Atlas.",
        )
        if not srv_ok:
            return 1
    else:
        print_step(2, "DNS SRV Lookup", True, f"Standard URI (non-SRV); hosts: {', '.join(f'{h}:{p}' for h, p in hosts)}")

    # Step 3: TCP Connect on port 27017
    tcp_ok, tcp_detail = check_tcp_connectivity(hosts)
    print_step(
        3,
        "TCP Connect (port 27017)",
        tcp_ok,
        tcp_detail,
        "Ensure your IP is added in MongoDB Atlas -> Network Access, and outbound TCP port 27017 is not blocked by your firewall/ISP.",
    )
    if not tcp_ok:
        all_passed = False

    # Step 4: TLS Handshake with certifi
    uses_tls = is_srv or "mongodb.net" in target_uri.lower() or "tls=true" in target_uri.lower() or "ssl=true" in target_uri.lower()
    if uses_tls:
        tls_ok, tls_detail = check_tls_handshake(hosts)
        print_step(
            4,
            "TLS Handshake (certifi)",
            tls_ok,
            tls_detail,
            "Check that certifi is installed (`pip install -U certifi`), your PC clock is accurate, and no firewall/antivirus is intercepting SSL.",
        )
        if not tls_ok:
            all_passed = False
    else:
        print_step(4, "TLS Handshake (certifi)", True, "Skipped (local non-TLS MongoDB connection)")

    # Step 5: Authentication & Ping
    client_kwargs = {
        "serverSelectionTimeoutMS": 10000,
        "connectTimeoutMS": 10000,
        "appname": "StarPostDiagnostics",
        "retryWrites": True,
    }
    if uses_tls:
        client_kwargs["tlsCAFile"] = certifi.where()

    client: Optional[MongoClient] = None
    try:
        client = MongoClient(target_uri, **client_kwargs)
        client.admin.command("ping")
        print_step(5, "Authentication & Ping", True, "Authenticated and pinged server successfully")
    except Exception as exc:
        err_clean = mask_secrets_in_text(str(exc), target_uri)
        print_step(
            5,
            "Authentication & Ping",
            False,
            err_clean,
            "Verify database username/password in Atlas -> Database Access and ensure your IP is whitelisted in Atlas -> Network Access.",
        )
        if client:
            client.close()
        return 1

    # Step 6: Round-trip latency (average of 5 pings in ms)
    try:
        latencies = []
        for _ in range(5):
            t0 = time.perf_counter()
            client.admin.command("ping")
            latencies.append((time.perf_counter() - t0) * 1000.0)
        avg_ms = sum(latencies) / len(latencies)
        min_ms = min(latencies)
        max_ms = max(latencies)
        print_step(6, "Round-Trip Latency (5 pings)", True, f"avg={avg_ms:.1f} ms (min={min_ms:.1f} ms, max={max_ms:.1f} ms)")
    except Exception as exc:
        print_step(6, "Round-Trip Latency (5 pings)", False, mask_secrets_in_text(str(exc), target_uri))
        all_passed = False

    # Step 7: List collections with document counts
    db = client[target_db]
    try:
        coll_names = sorted(db.list_collection_names())
        counts_str = ", ".join(f"{c}={db[c].count_documents({})}" for c in coll_names) if coll_names else "(no collections yet)"
        print_step(7, "List Collections & Counts", True, counts_str)
    except Exception as exc:
        print_step(
            7,
            "List Collections & Counts",
            False,
            mask_secrets_in_text(str(exc), target_uri),
            "Ensure the database user has readWrite role on the target database.",
        )
        all_passed = False

    # Step 8: Write / Read / Delete test document in _healthcheck
    try:
        hc = db["_healthcheck"]
        probe_doc = {"probe": "starpost_check_atlas", "checked_at": datetime.utcnow()}
        ins = hc.insert_one(probe_doc)
        fetched = hc.find_one({"_id": ins.inserted_id})
        if not fetched or fetched.get("probe") != "starpost_check_atlas":
            raise RuntimeError("Inserted _healthcheck document could not be read back.")
        hc.delete_one({"_id": ins.inserted_id})
        print_step(8, "Write/Read/Delete (_healthcheck)", True, "Successfully inserted, verified, and deleted test document")
    except Exception as exc:
        print_step(
            8,
            "Write/Read/Delete (_healthcheck)",
            False,
            mask_secrets_in_text(str(exc), target_uri),
            "Ensure the Atlas database user has 'readWrite' permissions on the database.",
        )
        all_passed = False
    finally:
        if client:
            client.close()

    print("=" * 72)
    if all_passed:
        print("  RESULT: ALL CHECKS PASSED (exit code 0)")
        print("=" * 72)
        return 0
    else:
        print("  RESULT: ONE OR MORE CHECKS FAILED (exit code 1)")
        print("=" * 72)
        return 1


if __name__ == "__main__":
    sys.exit(run_diagnostics())
