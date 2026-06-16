#!/usr/bin/env python3
"""Launch the FakeGPS Studio backend and open it in a desktop window.

Starts uvicorn on a background thread, then opens a native webview pointing at
it. If pywebview isn't available, falls back to printing the URL so you can open
it in a browser.

    python3 run.py                 # desktop window
    FAKEGPS_DEMO=1 python3 run.py  # no hardware needed
"""
from __future__ import annotations

import os
import threading
import time

import uvicorn

HOST = os.environ.get("FAKEGPS_HOST", "127.0.0.1")
PORT = int(os.environ.get("FAKEGPS_PORT", "8765"))


def _serve() -> None:
    uvicorn.run("backend.app:app", host=HOST, port=PORT, log_level="info")


def main() -> None:
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(1.0)  # give the server a moment to bind
    url = f"http://{HOST}:{PORT}"
    try:
        import webview  # pywebview
        webview.create_window("FakeGPS Studio", url, width=1180, height=800)
        webview.start()
    except Exception as e:  # noqa: BLE001 - any failure -> browser fallback
        print(f"[pywebview unavailable: {e}]")
        print(f"Open {url} in your browser. Ctrl+C to quit.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
