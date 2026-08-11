"""
normalizer.py
-------------
Pure functions that clean up inconsistent-but-valid source data before it
is matched for duplicates or transformed into the destination schema.

Kept separate from validation and transformation on purpose:
  * validators.py answers "is this data acceptable at all?"
  * normalizer.py answers "given acceptable data, how do we make it
    consistent?"
  * transformer.py answers "how do we reshape it into the destination
    schema?"

Each function here is small, pure, and independently testable.
"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(value: str) -> str:
    """Collapse internal/leading/trailing whitespace; keep original casing.

    Example: "  John   Smith " -> "John Smith"
    """
    if not value:
        return value
    return _WHITESPACE_RE.sub(" ", value.strip())


def normalize_email(value: str) -> str:
    """Trim and lowercase an email address for consistent matching.

    Example: " JOHN@EXAMPLE.COM " -> "john@example.com"
    """
    if not value:
        return value
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    """Strip whitespace around a phone number. We deliberately do NOT
    reformat phone numbers into a single canonical format here, since
    international formats vary and getting it wrong risks corrupting
    valid data -- a good candidate for a future, region-aware improvement.
    """
    if not value:
        return value
    return value.strip()


def normalize_company_name(value: str) -> str:
    if not value:
        return value
    return _WHITESPACE_RE.sub(" ", value.strip())


def normalize_text(value: str) -> str:
    """Generic trim/collapse for free-text fields like notes."""
    if not value:
        return value
    return _WHITESPACE_RE.sub(" ", value.strip())
