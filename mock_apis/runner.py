"""
mock_apis/runner.py
---------------------
Runs the bundled mock source/destination APIs as background threads
inside the same process as the Streamlit app, so the whole project can
be deployed as a single free-tier app instead of three separate
processes.

Why this exists:
mock_apis/source_api.py and destination_api.py are written to be run
as standalone `uvicorn ... --reload` processes locally (see their own
docstrings). That's fine on a laptop, but most free hosting (Streamlit
Community Cloud included) only runs one process per deployed app --
there's nowhere to also run two separate uvicorn servers alongside it.

This module starts both FastAPI apps as uvicorn servers on background
daemon threads inside app.py's own process. From SourceAPIClient and
DestinationAPIClient's point of view, nothing changes -- they still make
real HTTP requests to http://127.0.0.1:<port> over a real socket; the
servers just happen to live in threads of the same process rather than
separate ones.

This pattern is appropriate ONLY because these are mock/demo APIs backed
by a static JSON file and in-memory state, with no real secrets or real
customer data behind them. A real source or destination system should
never be run this way -- this module exists purely to make the
portfolio demo deployable as a single app. See src/config.py's
`use_bundled_mock_apis` flag, which is what lets a real deployment (one
pointed at an actual CRM and an actual destination system) skip this
entirely.
"""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import requests
import uvicorn

logger = logging.getLogger("integration.mock_apis.runner")

_STARTED = False
_LOCK = threading.Lock()


def _run_server(app, port: int) -> None:
    """Run one FastAPI app with uvicorn on the given port, blocking this thread."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    # uvicorn.Server checks `threading.current_thread() is threading.main_thread()`
    # before installing OS signal handlers, so running it on a background
    # thread (rather than the main thread) is safe and simply skips that step --
    # this is the same pattern uvicorn's own docs use for in-process test servers.
    server.run()


def _wait_until_healthy(url: str, timeout_seconds: float = 5.0) -> bool:
    """Poll a server's /health endpoint until it responds or timeout_seconds elapses."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=0.5)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.1)
    return False


def start_mock_apis_in_background(source_api_url: str, destination_api_url: str) -> None:
    """
    Start the bundled mock source and destination APIs as background
    daemon threads, if they aren't already running in this process.

    Idempotent and thread-safe: Streamlit reruns app.py's top-level code
    on every user interaction, but this only actually starts the servers
    once -- subsequent calls are a no-op. The `_STARTED` guard works
    because it's module-level state, which persists across Streamlit
    reruns (they're the same Python process, unlike st.session_state
    which is per-session).

    Only call this for the bundled 127.0.0.1 mock APIs. If source/destination
    URLs are ever pointed at a real external system, this should not run
    at all -- see the `use_bundled_mock_apis` check in app.py.
    """
    global _STARTED

    with _LOCK:
        if _STARTED:
            return

        # Deferred import: a deployment pointed at real source/destination
        # systems has no reason to import or run these mock FastAPI apps.
        from mock_apis.source_api import app as source_app
        from mock_apis.destination_api import app as destination_app

        source_port = urlparse(source_api_url).port or 8001
        destination_port = urlparse(destination_api_url).port or 8002

        logger.info(
            "starting_bundled_mock_apis",
            extra={"source_port": source_port, "destination_port": destination_port},
        )

        threading.Thread(
            target=_run_server, args=(source_app, source_port),
            daemon=True, name="mock-source-api",
        ).start()
        threading.Thread(
            target=_run_server, args=(destination_app, destination_port),
            daemon=True, name="mock-destination-api",
        ).start()

        source_ok = _wait_until_healthy(f"{source_api_url}/health")
        destination_ok = _wait_until_healthy(f"{destination_api_url}/health")

        if not (source_ok and destination_ok):
            logger.warning(
                "bundled_mock_apis_health_check_incomplete",
                extra={"source_ok": source_ok, "destination_ok": destination_ok},
            )

        _STARTED = True