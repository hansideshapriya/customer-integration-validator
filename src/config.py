"""
config.py
---------
Centralized configuration for the Customer Integration Validator.
All values that differ between environments (URLs, credentials, timeouts)
live here and are loaded from environment variables rather than hard-coded.
This is what lets the same codebase run against a laptop mock API today
and a real staging/production API later, without touching source code.
Secrets (API keys/tokens) are NEVER given default values that look like
real credentials, and are never logged. See README.md -> Security
Considerations for the full policy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load variables from a local .env file if present. In real deployments,
# these would instead be injected by the hosting platform (e.g. environment
# variables set in a CI/CD pipeline, a secrets manager, or Docker secrets).
load_dotenv()


def _get_int(name: str, default: int) -> int:
    """Read an int-valued environment variable, falling back safely."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable application settings, populated once at startup."""

    # --- Source system (the SaaS CRM we are pulling customer data FROM) ---
    source_api_url: str
    source_api_key: str
    source_page_size: int

    # --- Destination system (the system we are importing customers INTO) ---
    destination_api_url: str
    destination_api_token: str

    # --- Networking behavior ---
    request_timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float

    # --- Business rules ---
    supported_regions: tuple
    max_notes_length: int

    # --- Logging ---
    log_level: str
    log_file_path: str

    # --- Deployment mode ---
    # True (default) runs the bundled mock APIs in-process, so the whole
    # app is a single deployable process -- see mock_apis/runner.py.
    # Set to false for a real deployment pointed at an actual source/
    # destination system; app.py will then make real HTTP calls to
    # source_api_url / destination_api_url instead of starting anything.
    use_bundled_mock_apis: bool


def load_settings() -> Settings:
    """
    Build a Settings object from environment variables.
    Defaults point at the local mock APIs bundled with this project
    (see mock_apis/) so the app works out of the box for demo purposes.
    Real credentials should always come from the environment, never from
    source code.
    """
    return Settings(
        source_api_url=os.getenv("SOURCE_API_URL", "http://127.0.0.1:8001"),
        source_api_key=os.getenv("SOURCE_API_KEY", ""),
        source_page_size=_get_int("SOURCE_PAGE_SIZE", 25),
        destination_api_url=os.getenv("DESTINATION_API_URL", "http://127.0.0.1:8002"),
        destination_api_token=os.getenv("DESTINATION_API_TOKEN", ""),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 5),
        max_retries=_get_int("MAX_RETRIES", 3),
        retry_backoff_seconds=float(os.getenv("RETRY_BACKOFF_SECONDS", "0.5")),
        supported_regions=("NA", "EMEA", "APAC", "LATAM"),
        max_notes_length=_get_int("MAX_NOTES_LENGTH", 500),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_file_path=os.getenv("LOG_FILE_PATH", "logs/integration.log"),
        use_bundled_mock_apis=os.getenv("USE_BUNDLED_MOCK_APIS", "true").lower() == "true",
    )


settings = load_settings()