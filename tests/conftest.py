"""
tests/conftest.py
------------------
Shared pytest fixtures. Kept minimal on purpose: a fast-retry Settings
object for anything that talks to an HTTP client (so tests don't
actually wait through real backoff delays), and a small helper for
building valid raw customer dicts without repeating all 11 required
fields in every single test.
"""
from __future__ import annotations

import pytest

from src.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """
    Settings pointed at fake hosts (never actually contacted directly --
    the `responses` library intercepts calls made through these URLs)
    with a very short retry backoff, so retry-exhaustion tests run in
    milliseconds instead of real seconds.
    """
    return Settings(
        source_api_url="http://testserver/source",
        source_api_key="test-source-key",
        source_page_size=25,
        destination_api_url="http://testserver/destination",
        destination_api_token="test-destination-token",
        request_timeout_seconds=5,
        max_retries=3,
        retry_backoff_seconds=0.01,
        supported_regions=("NA", "EMEA", "APAC", "LATAM"),
        max_notes_length=500,
        log_level="INFO",
        log_file_path="logs/test_integration.log",
        use_bundled_mock_apis=False,
    )


def make_raw_record(**overrides) -> dict:
    """
    A valid raw source record with sensible defaults, so each test only
    needs to specify the field(s) it actually cares about.
    """
    record = {
        "customer_id": "CUST-1001",
        "first_name": "Colin",
        "last_name": "Keegan",
        "email_address": "colin.keegan@example.com",
        "phone": "614-555-0142",
        "company_name": "Riverside Logistics",
        "website": "https://riversidelogistics.com",
        "customer_status": "active",
        "customer_type": "standard",
        "region": "NA",
        "notes": "",
        "created_at": "2025-11-02",
    }
    record.update(overrides)
    return record