#!/usr/bin/env python3
"""
Launcher script for StarPost Manager.
Starts the Uvicorn web server and opens the local dashboard in your browser.
"""
import socket
import sys
import threading
import time
import webbrowser
import uvicorn
from app.config import settings

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def is_port_in_use(host: str, port: int) -> bool:
    """Check if a specific host:port is already occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def get_available_port(host: str, requested_port: int) -> int:
    """Return requested port if available, or find the next free consecutive port."""
    if not is_port_in_use(host, requested_port):
        return requested_port

    print(f"\n[!] Notice: Port {requested_port} is already in use by another process.")
    for p in range(requested_port + 1, requested_port + 20):
        if not is_port_in_use(host, p):
            print(f"[+] Automatically switching to available port: {p}\n")
            return p

    return requested_port


def open_browser(url: str):
    """Wait for server to bind and launch the default system web browser."""
    time.sleep(1.2)
    try:
        print(f"\nOpening browser at {url}...")
        webbrowser.open(url)
    except Exception as exc:
        print(f"Could not open browser automatically: {exc}")


def main():
    host = settings.APP_HOST
    requested_port = settings.APP_PORT
    actual_port = get_available_port(host, requested_port)
    url = f"http://{host}:{actual_port}"

    print("=" * 65)
    print("  * StarPost Manager - Telegram Stars Paid Media Studio *")
    print("=" * 65)
    print(f"  * Local Dashboard URL : {url}")
    print(f"  * MongoDB Database    : {settings.DB_NAME}")
    print(f"  * Active Timezone     : {settings.DEFAULT_TIMEZONE}")
    print(f"  * Default Theme       : {settings.DEFAULT_THEME}")
    print("=" * 65)
    print("Press CTRL+C to stop the server.\n")

    # Start browser opener in background thread
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    # Run Uvicorn
    uvicorn.run(
        "app.main:app",
        host=host,
        port=actual_port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
