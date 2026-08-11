"""
api_client.py
-------------
HTTP client for retrieving customer records from the source system
(the SaaS CRM we are importing FROM).

This is the GET/read-only counterpart to destination_client.py. It is
kept as a separate class rather than a shared base class with
DestinationAPIClient because the two have different failure semantics:

  * Source calls are read-only and safe to retry unconditionally.
  * Source data may be paginated; destination submissions are not.
  * A source failure means "we have less data than expected" (degrade
    gracefully, report what we got); a destination failure means "this
    specific record didn't make it" (report per-record, see
    SubmissionResult in destination_client.py).

Every method here raises one of the typed exceptions below rather than
letting `requests` exceptions or JSON errors escape -- callers (the
pipeline, the Streamlit UI) should never need to know that this client
is built on `requests`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

from src.config import Settings
from src.security import redact_for_logging

logger = logging.getLogger("integration.api_client")


# --- Typed errors ------------------------------------------------------
# One exception type per failure mode the UI/pipeline actually needs to
# handle differently. A caller can catch SourceAPIError to be safe, or
# catch a specific subclass to react differently (e.g. don't retry on
# SourceAuthError).

class SourceAPIError(Exception):
    """Base class for all source-ingestion failures. Safe to show to a user."""


class SourceAuthError(SourceAPIError):
    """401/403 from the source API -- credentials are wrong or expired."""


class SourceUnavailableError(SourceAPIError):
    """Network failure, timeout, or repeated 5xx after retries exhausted."""


class SourceResponseFormatError(SourceAPIError):
    """200 OK but the body wasn't valid JSON, or wasn't shaped as expected."""


@dataclass
class FetchResult:
    """
    Outcome of a full (possibly multi-page) fetch from the source system.

    Deliberately holds *both* the records we got and any warning about
    what went wrong, rather than only succeeding or only raising --
    "we retrieved 480 of an expected 500 records because page 4 failed"
    is a normal, reportable outcome for an implementation, not a crash.
    """
    records: List[dict] = field(default_factory=list)
    pages_fetched: int = 0
    warning: Optional[str] = None


class SourceAPIClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self._settings = settings
        self._session = session or requests.Session()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.source_api_key}",
            "Accept": "application/json",
        }

    def _get_with_retries(self, url: str, params: dict, correlation_id: str) -> requests.Response:
        """
        Single GET with retry/backoff for transient failures only.

        Retrying a GET is always safe (it's not side-effecting), which is
        exactly why this is simpler than the retry logic in
        destination_client.py -- no idempotency concerns here.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._settings.max_retries + 1):
            try:
                logger.info(
                    "source_fetch_attempt",
                    extra={"correlation_id": correlation_id, "url": url,
                           "params": redact_for_logging(params), "attempt": attempt},
                )
                response = self._session.get(
                    url, params=params, headers=self._headers(),
                    timeout=self._settings.request_timeout_seconds,
                )
            except requests.Timeout as exc:
                last_exc = exc
                logger.warning("source_fetch_timeout", extra={"correlation_id": correlation_id, "attempt": attempt})
            except requests.ConnectionError as exc:
                last_exc = exc
                logger.warning("source_fetch_connection_error", extra={"correlation_id": correlation_id, "attempt": attempt})
            else:
                if response.status_code in (401, 403):
                    raise SourceAuthError(
                        "Source API rejected credentials (401/403). Check SOURCE_API_KEY."
                    )
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self._settings.retry_backoff_seconds))
                    logger.warning("source_fetch_rate_limited", extra={"correlation_id": correlation_id, "attempt": attempt})
                    time.sleep(retry_after)
                    continue
                if response.status_code >= 500:
                    last_exc = SourceUnavailableError(f"Source API server error ({response.status_code}).")
                    time.sleep(self._settings.retry_backoff_seconds * (2 ** (attempt - 1)))
                    continue
                if response.status_code != 200:
                    raise SourceAPIError(f"Source API returned unexpected status {response.status_code}.")

                return response

            if attempt < self._settings.max_retries:
                time.sleep(self._settings.retry_backoff_seconds * (2 ** (attempt - 1)))

        raise SourceUnavailableError(
            "Could not reach the source API after multiple attempts. "
            "Check SOURCE_API_URL and network connectivity."
        ) from last_exc

    def fetch_customers(self, correlation_id: str) -> FetchResult:
        """
        Retrieve all customer records from the source system, following
        pagination until the source signals there are no more pages.

        Never raises for a *partial* failure (e.g. page 3 of 5 fails) --
        returns what it has plus a warning. Raises SourceAuthError /
        SourceUnavailableError only when we couldn't get *any* data,
        since that's the one case the caller can't meaningfully proceed
        past.
        """
        all_records: List[dict] = []
        page = 1
        pages_fetched = 0
        warning: Optional[str] = None

        while True:
            url = f"{self._settings.source_api_url}/customers"
            params = {"page": page, "page_size": self._settings.source_page_size}

            try:
                response = self._get_with_retries(url, params, correlation_id)
            except SourceUnavailableError:
                if pages_fetched == 0:
                    raise  # got nothing at all -- caller can't proceed
                warning = (
                    f"Stopped fetching after page {pages_fetched}: source API "
                    "became unavailable. Results below are partial."
                )
                break

            try:
                body = response.json()
            except ValueError as exc:
                if pages_fetched == 0:
                    raise SourceResponseFormatError(
                        "Source API returned a 200 response that wasn't valid JSON."
                    ) from exc
                warning = f"Stopped fetching after page {pages_fetched}: page {page} returned malformed JSON."
                break

            if not isinstance(body, dict) or "records" not in body:
                if pages_fetched == 0:
                    raise SourceResponseFormatError(
                        "Source API response did not match the expected shape "
                        "(missing 'records' field)."
                    )
                warning = f"Stopped fetching after page {pages_fetched}: page {page} had an unexpected shape."
                break

            page_records = body.get("records") or []
            if not isinstance(page_records, list):
                warning = f"Stopped fetching after page {pages_fetched}: 'records' was not a list."
                break

            all_records.extend(page_records)
            pages_fetched += 1

            has_more = bool(body.get("has_more", False))
            logger.info(
                "source_fetch_page_complete",
                extra={"correlation_id": correlation_id, "page": page,
                       "record_count": len(page_records), "has_more": has_more},
            )
            if not has_more:
                break
            page += 1

        return FetchResult(records=all_records, pages_fetched=pages_fetched, warning=warning)