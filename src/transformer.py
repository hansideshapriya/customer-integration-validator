"""
transformer.py
--------------
Maps a validated, normalized SourceCustomerRecord onto the destination
system's schema (DestinationCustomerRecord).

This file is the single source of truth for "what does field X in the
source system become in the destination system" -- exactly the kind of
mapping document an implementation engineer would otherwise keep in a
spreadsheet. Keeping it as code means it's testable and can't silently
drift from what actually runs.
"""

from __future__ import annotations

from src.models import DestinationCustomerRecord, SourceCustomerRecord
from src.normalizer import (
    normalize_company_name,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_text,
)

# Explicit field mapping table (source field -> destination field).
# Not executed directly -- it exists so the mapping is documented in one
# place a reviewer can read without tracing through function bodies.
FIELD_MAPPING = {
    "first_name + last_name": "name",
    "email_address": "email",
    "customer_status": "status",
    "customer_type": "account_type",
    "region": "region",
    "company_name": "company",
    "website": "website",
    "notes": "notes",
    "customer_id": "id",
}


def normalize_source_record(record: SourceCustomerRecord) -> SourceCustomerRecord:
    """
    Apply normalization rules to a validated record and return a new,
    normalized copy. Done as a distinct step (rather than inside the
    Pydantic model) so validation and normalization stay independently
    testable, per the project's pipeline design.
    """
    return record.model_copy(update={
        "first_name": normalize_name(record.first_name),
        "last_name": normalize_name(record.last_name),
        "email_address": normalize_email(str(record.email_address)),
        "phone": normalize_phone(record.phone or ""),
        "company_name": normalize_company_name(record.company_name or ""),
        "notes": normalize_text(record.notes or ""),
    })


def transform_to_destination(record: SourceCustomerRecord) -> DestinationCustomerRecord:
    """
    Map a normalized source record to the destination schema.

    Assumes `record` has already passed schema validation, business-rule
    validation, and normalization -- transformation is intentionally the
    last step before transmission.
    """
    full_name = f"{record.first_name} {record.last_name}".strip()

    return DestinationCustomerRecord(
        id=record.customer_id,
        name=full_name,
        email=str(record.email_address),
        status=record.customer_status.value,
        account_type=record.customer_type.value,
        region=record.region,
        company=record.company_name or "",
        website=record.website or "",
        notes=record.notes or "",
    )
