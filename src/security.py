"""
security.py
-----------
Practical, explainable security checks for inbound customer data.

Scope and honesty note (see README -> Security Considerations for the full
version): this module does NOT claim to be a general-purpose security
scanner. It targets the specific risk categories that matter for a
data-import integration like this one:

  1. Script / markup injection in free-text fields (stored XSS risk if the
     destination system renders these fields in a browser without escaping).
  2. SQL-injection-shaped strings in free-text fields (defense in depth --
     the real fix is parameterized queries downstream, but flagging obvious
     payloads catches bad exports/bad actors early).
  3. Unsafe URLs: non-http(s) schemes (e.g. `javascript:`), and URLs
     pointing at private/internal IP ranges (SSRF risk if anything ever
     fetches these URLs server-side).
  4. Secrets/credential-shaped strings accidentally embedded in free-text
     fields (e.g. an API key pasted into a notes field by a rep).
  5. Oversized field values, which can indicate malformed exports or be
     used for crude denial-of-service / log-flooding.

What this module does NOT do:
  * It does not sanitize/rewrite data. It flags it. Sanitization/escaping
    decisions belong to whatever system ultimately renders or stores the
    data, because the correct escaping strategy is context-dependent.
  * It is not a substitute for parameterized queries, output encoding, or
    a real secrets-scanning tool in a production system.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urlparse

# --- Pattern libraries -----------------------------------------------------

_SCRIPT_PATTERNS = [
    re.compile(r"<\s*script\b", re.IGNORECASE),
    re.compile(r"on\w+\s*=\s*['\"]", re.IGNORECASE),  # onerror=, onclick=, ...
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"<\s*iframe\b", re.IGNORECASE),
]

_SQLI_PATTERNS = [
    re.compile(r"(\bDROP\s+TABLE\b)", re.IGNORECASE),
    re.compile(r"(\bUNION\s+SELECT\b)", re.IGNORECASE),
    re.compile(r"(--\s*$)"),
    re.compile(r"(;\s*--)"),
    re.compile(r"(\bOR\s+1\s*=\s*1\b)", re.IGNORECASE),
    re.compile(r"('\s*;\s*DROP\b)", re.IGNORECASE),
]

# Loosely matches common credential/token shapes so we don't ship customer
# reps' pasted secrets downstream. Intentionally conservative (few false
# negatives are fine; this is a warning, not a hard block).
_SECRET_PATTERNS = [
    re.compile(r"\bsk_live_[A-Za-z0-9_]{10,}\b"),          # Stripe-style live key
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                    # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),                 # GitHub PAT
    re.compile(r"(?i)\bpassword\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*\S+"),
]

_PRIVATE_URL_SCHEMES_ALLOWED = {"http", "https"}


@dataclass
class SecurityWarning:
    record_id: str
    field: str
    category: str          # e.g. "script_injection", "sqli_pattern", "unsafe_url", "possible_secret", "oversized_field"
    severity: str           # "high" | "medium" | "low"
    detail: str


def _is_private_or_non_public_host(host: str) -> bool:
    """Return True if the host resolves to something that isn't a normal public hostname/IP."""
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        # Not an IP literal -- treat bare "localhost" as unsafe too.
        return host.lower() in {"localhost"}


def check_url_field(record_id: str, field: str, value: str) -> List[SecurityWarning]:
    warnings: List[SecurityWarning] = []
    if not value:
        return warnings

    parsed = urlparse(value)

    if parsed.scheme and parsed.scheme.lower() not in _PRIVATE_URL_SCHEMES_ALLOWED:
        warnings.append(SecurityWarning(
            record_id, field, "unsafe_url", "high",
            f"URL uses disallowed scheme '{parsed.scheme}' -- possible script injection via URL field.",
        ))
        return warnings  # no point checking host on a non-http(s) scheme

    if parsed.hostname and _is_private_or_non_public_host(parsed.hostname):
        warnings.append(SecurityWarning(
            record_id, field, "unsafe_url", "medium",
            f"URL points at a private/internal/loopback address ('{parsed.hostname}') -- "
            "possible SSRF risk if this URL is ever fetched server-side.",
        ))

    return warnings


def check_text_field(record_id: str, field: str, value: str, max_len: int) -> List[SecurityWarning]:
    warnings: List[SecurityWarning] = []
    if not value:
        return warnings

    for pattern in _SCRIPT_PATTERNS:
        if pattern.search(value):
            warnings.append(SecurityWarning(
                record_id, field, "script_injection", "high",
                "Field contains markup/script-like content. Rendering this unescaped "
                "in a browser downstream could lead to stored XSS.",
            ))
            break

    for pattern in _SQLI_PATTERNS:
        if pattern.search(value):
            warnings.append(SecurityWarning(
                record_id, field, "sqli_pattern", "high",
                "Field contains a SQL-injection-shaped string. Ensure downstream "
                "systems use parameterized queries; this record is flagged for review.",
            ))
            break

    for pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            warnings.append(SecurityWarning(
                record_id, field, "possible_secret", "high",
                "Field appears to contain a credential/API key. This should never be "
                "forwarded downstream or logged; remove before transmission.",
            ))
            break

    if len(value) > max_len:
        warnings.append(SecurityWarning(
            record_id, field, "oversized_field", "low",
            f"Field length ({len(value)}) exceeds expected maximum ({max_len}). "
            "Oversized free-text fields can indicate malformed exports.",
        ))

    return warnings


def scan_record(raw_record: dict, max_notes_length: int = 500) -> List[SecurityWarning]:
    """
    Run all security checks against a single raw (pre-validation) record.

    Operates on the raw dict rather than a validated model because we want
    to catch problems even in records that will *also* fail schema
    validation -- security scanning and schema validation are independent
    concerns and both should run.
    """
    record_id = str(raw_record.get("customer_id") or "UNKNOWN")
    warnings: List[SecurityWarning] = []

    text_fields = ["first_name", "last_name", "company_name", "notes"]
    for field in text_fields:
        value = raw_record.get(field)
        if isinstance(value, str):
            limit = max_notes_length if field == "notes" else 200
            warnings.extend(check_text_field(record_id, field, value, limit))

    url_value = raw_record.get("website")
    if isinstance(url_value, str):
        warnings.extend(check_url_field(record_id, "website", url_value))

    return warnings


# --- Log redaction -----------------------------------------------------

_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "password", "token",
    "access_token", "secret", "source_api_key", "destination_api_token",
}


def redact_for_logging(payload: dict) -> dict:
    """
    Return a copy of `payload` safe to write to logs: sensitive keys are
    masked and free-text fields likely to hold PII (email, phone, notes)
    are truncated rather than logged in full.

    This is used everywhere the app logs a record or a request payload,
    so credentials and excessive PII never land in log files.
    """
    redacted = {}
    for key, value in payload.items():
        lower_key = key.lower()
        if lower_key in _SENSITIVE_KEYS:
            redacted[key] = "***REDACTED***"
        elif lower_key in {"email_address", "email", "phone", "notes"} and isinstance(value, str):
            redacted[key] = (value[:3] + "…") if value else value
        else:
            redacted[key] = value
    return redacted
