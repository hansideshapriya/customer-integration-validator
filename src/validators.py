"""
validators.py
-------------
Wraps Pydantic schema validation and converts its exceptions into a
structured, serializable `ValidationIssue` list -- something the UI and
the integration report can render without ever showing a raw traceback
to the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from pydantic import ValidationError

from src.models import SourceCustomerRecord


@dataclass
class ValidationIssue:
    record_id: str
    field: str
    error_type: str
    message: str


@dataclass
class ValidationResult:
    record_id: str
    raw_record: dict
    is_valid: bool
    parsed: Optional[SourceCustomerRecord]
    issues: List[ValidationIssue]


def validate_record(raw_record: dict) -> ValidationResult:
    """
    Validate a single raw record against SourceCustomerRecord.

    Never raises -- schema problems are data, not exceptions, from the
    caller's point of view. This is what lets the pipeline keep processing
    the other 999 records in a batch when record #37 is malformed.
    """
    record_id = str(raw_record.get("customer_id") or "UNKNOWN")

    try:
        parsed = SourceCustomerRecord.model_validate(raw_record)
        return ValidationResult(record_id, raw_record, True, parsed, [])
    except ValidationError as exc:
        issues = [
            ValidationIssue(
                record_id=record_id,
                field=".".join(str(p) for p in err["loc"]) or "(record)",
                error_type=err["type"],
                message=err["msg"],
            )
            for err in exc.errors()
        ]
        return ValidationResult(record_id, raw_record, False, None, issues)


def validate_batch(raw_records: List[dict]) -> List[ValidationResult]:
    return [validate_record(r) for r in raw_records]
