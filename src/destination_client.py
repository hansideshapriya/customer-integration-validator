"""
destination_client.py
----------------------
HTTP client for submitting transformed customer records to the
destination system, one record per request.

Mirrors api_client.py's defensive posture (timeouts, retries, typed
errors) but is written separately rather than sharing a base class,
because source (GET, paginated, read-only) and destination (POST,
per-record, side-effecting) have different failure semantics -- most
importantly, retries on the destination side must be idempotency-aware
(see README -> Architecture Decisions).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from src.config import Settings
from src.models import DestinationCustomerRecord
from src.security import redact_for_logging

logger = logging.getLogger("integration.destination_client")


@dataclass
class SubmissionResult:
    record_id: str
    success: bool
    status_code: Optional[int]
    message: str
    retryable: bool = False


class DestinationAPIClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self._settings = settings
        self._session = session or requests.Session()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.destination_api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def submit_customer(self, record: DestinationCustomerRecord, correlation_id: str) -> SubmissionResult:
        """
        Submit a single transformed record to the destination system.

        Every code path returns a SubmissionResult rather than raising --
        a partial failure (record 12 of 50 rejected) is an expected,
        normal outcome for a batch integration, not an exceptional one.
        """
        url = f"{self._settings.destination_api_url}/customers"
        payload = record.model_dump()

        last_result: Optional[SubmissionResult] = None

        for attempt in range(1, self._settings.max_retries + 1):
            try:
                logger.info(
                    "destination_submit_attempt",
                    extra={"correlation_id": correlation_id, "record_id": record.id,
                           "attempt": attempt, "payload": redact_for_logging(payload)},
                )
                response = self._session.post(
                    url, json=payload, headers=self._headers(),
                    timeout=self._settings.request_timeout_seconds,
                )
            except requests.Timeout:
                last_result = SubmissionResult(record.id, False, None, "Destination API timed out.", retryable=True)
            except requests.ConnectionError:
                last_result = SubmissionResult(record.id, False, None, "Could not reach destination API.", retryable=True)
            else:
                if response.status_code in (200, 201):
                    return SubmissionResult(record.id, True, response.status_code, "Accepted by destination system.")

                if response.status_code == 401 or response.status_code == 403:
                    return SubmissionResult(
                        record.id, False, response.status_code,
                        "Destination API rejected credentials (401/403). Check DESTINATION_API_TOKEN.",
                        retryable=False,
                    )

                if response.status_code == 409:
                    return SubmissionResult(
                        record.id, False, response.status_code,
                        "Destination system reports this record already exists (conflict).",
                        retryable=False,
                    )

                if response.status_code == 422:
                    detail = _safe_error_detail(response)
                    return SubmissionResult(
                        record.id, False, response.status_code,
                        f"Destination system rejected record: {detail}",
                        retryable=False,
                    )

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", self._settings.retry_backoff_seconds))
                    last_result = SubmissionResult(record.id, False, 429, "Destination API rate limit exceeded.", retryable=True)
                    time.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    last_result = SubmissionResult(
                        record.id, False, response.status_code,
                        f"Destination API server error ({response.status_code}).", retryable=True,
                    )
                else:
                    return SubmissionResult(
                        record.id, False, response.status_code,
                        f"Destination API returned unexpected status {response.status_code}.", retryable=False,
                    )

            if attempt < self._settings.max_retries:
                time.sleep(self._settings.retry_backoff_seconds * (2 ** (attempt - 1)))

        return last_result or SubmissionResult(record.id, False, None, "Submission failed after retries.", retryable=True)


def _safe_error_detail(response: requests.Response) -> str:
    try:
        body = response.json()
        return str(body.get("error", "validation failed"))
    except ValueError:
        return "validation failed"
