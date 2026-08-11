"""
pipeline.py
-----------
Orchestrates the full integration run:

    fetch -> security scan -> schema validation -> normalize ->
    business rules -> duplicate detection -> transform -> (optional) submit

Every other module in src/ answers one question in isolation
("is this valid?", "is this a duplicate?", "does this violate a business
rule?"). This module's only job is sequencing those questions and
deciding, per record, whether the combined answer is "safe to transmit."

That decision is deliberately centralized here (see `_disqualifying_reasons`)
rather than scattered across modules, so there is exactly one place that
answers "why didn't record X go to the destination system?" -- which is
the question an implementation specialist actually gets asked.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.api_client import FetchResult, SourceAPIClient, SourceAPIError
from src.business_rules import BusinessRuleFailure, apply_business_rules
from src.config import Settings
from src.destination_client import DestinationAPIClient, SubmissionResult
from src.duplicates import DuplicateGroup, find_duplicates
from src.models import DestinationCustomerRecord, SourceCustomerRecord
from src.security import SecurityWarning, scan_record
from src.transformer import normalize_source_record, transform_to_destination
from src.validators import ValidationResult, validate_batch

logger = logging.getLogger("integration.pipeline")


@dataclass
class RecordOutcome:
    """
    The final, human-readable disposition of a single record, after every
    stage of the pipeline has had a chance to weigh in.

    This is the object the UI and the report actually render -- everything
    upstream (ValidationResult, BusinessRuleFailure, DuplicateGroup, ...)
    exists to help build this, not to be shown directly.
    """
    record_id: str
    accepted: bool
    reasons: List[str] = field(default_factory=list)     # why rejected (empty if accepted)
    security_warnings: List[SecurityWarning] = field(default_factory=list)
    is_duplicate: bool = False
    duplicate_type: Optional[str] = None                  # "exact_id" | "potential_email" | None
    source_record: Optional[dict] = None                  # raw, for "inspect rejected record"
    transformed_record: Optional[dict] = None              # destination shape, if accepted
    submission: Optional[SubmissionResult] = None           # populated only if submit=True


@dataclass
class PipelineRun:
    """Full result of one end-to-end integration run. Consumed by reporting.py and app.py."""
    correlation_id: str
    fetch_warning: Optional[str]
    records_retrieved: int
    outcomes: List[RecordOutcome]
    duplicate_groups: List[DuplicateGroup]
    submitted: bool


def _security_warnings_by_record(raw_records: List[dict], max_notes_length: int) -> Dict[str, List[SecurityWarning]]:
    by_record: Dict[str, List[SecurityWarning]] = {}
    for raw in raw_records:
        record_id = str(raw.get("customer_id") or "UNKNOWN")
        warnings = scan_record(raw, max_notes_length=max_notes_length)
        if warnings:
            by_record[record_id] = warnings
    return by_record


def _disqualifying_reasons(
    validation: Optional[ValidationResult],
    business_failures: List[BusinessRuleFailure],
    security_warnings: List[SecurityWarning],
    is_extra_duplicate: bool,
) -> List[str]:
    """
    The single place that decides whether a record is safe to transmit.

    A record is disqualified if ANY of the following are true:
      * it failed schema validation (unresolved validation errors)
      * it failed a business rule
      * it carries a HIGH-severity security warning (script/SQLi/secret) --
        medium/low warnings (e.g. an internal-IP URL) are surfaced to the
        implementation specialist but don't block transmission on their
        own, since they're often legitimate (an internal staging URL, say)
      * it's an extra occurrence of an exact-ID duplicate (the first
        occurrence is kept; the rest are rejected as duplicates so the
        destination system doesn't receive the same ID twice)

    Potential (email-based) duplicates are NOT auto-disqualified -- they're
    flagged for human review, since two records sharing an email could be
    a real duplicate or a shared inbox for two legitimate contacts. This
    mirrors how an implementation specialist would actually triage them.
    """
    reasons: List[str] = []

    if validation is not None and not validation.is_valid:
        for issue in validation.issues:
            reasons.append(f"Validation error on '{issue.field}': {issue.message}")

    for failure in business_failures:
        reasons.append(f"Business rule '{failure.rule}': {failure.message}")

    high_severity = [w for w in security_warnings if w.severity == "high"]
    for warning in high_severity:
        reasons.append(f"Security warning ({warning.category}) on '{warning.field}': {warning.detail}")

    if is_extra_duplicate:
        reasons.append("Duplicate customer_id already present earlier in this batch.")

    return reasons


def run_pipeline(settings: Settings, submit: bool = False) -> PipelineRun:
    """
    Execute the full pipeline once.

    submit=False (default): fetch/validate/transform only -- "dry run" /
        review mode, matching the UI's "run validation" step.
    submit=True: additionally POST every accepted record to the
        destination system -- matching the UI's "submit valid records"
        step. Never submits a record that failed any earlier stage.
    """
    correlation_id = str(uuid.uuid4())
    logger.info("pipeline_start", extra={"correlation_id": correlation_id, "submit": submit})

    source_client = SourceAPIClient(settings)
    try:
        fetch_result: FetchResult = source_client.fetch_customers(correlation_id)
    except SourceAPIError as exc:
        # Nothing to process at all -- return an empty, clearly-labeled run
        # rather than raising, so the UI can show a real error state
        # instead of crashing.
        logger.error("pipeline_source_fetch_failed", extra={"correlation_id": correlation_id, "error": str(exc)})
        return PipelineRun(
            correlation_id=correlation_id,
            fetch_warning=str(exc),
            records_retrieved=0,
            outcomes=[],
            duplicate_groups=[],
            submitted=submit,
        )

    raw_records = fetch_result.records
    logger.info(
        "pipeline_records_fetched",
        extra={"correlation_id": correlation_id, "count": len(raw_records),
               "pages": fetch_result.pages_fetched, "warning": fetch_result.warning},
    )

    # --- Security scan runs on RAW records, independent of validation ---
    security_by_id = _security_warnings_by_record(raw_records, settings.max_notes_length)

    # --- Schema validation ---
    validation_results = validate_batch(raw_records)
    validation_by_id: Dict[str, ValidationResult] = {vr.record_id: vr for vr in validation_results}

    # --- Normalize every syntactically-valid record ---
    normalized: List[SourceCustomerRecord] = [
        normalize_source_record(vr.parsed) for vr in validation_results if vr.is_valid
    ]

    # --- Duplicate detection (operates on normalized, valid records only --
    #     an invalid record can't be meaningfully compared for duplication) ---
    duplicate_groups = find_duplicates(normalized)
    exact_dup_groups = [g for g in duplicate_groups if g.match_type == "exact_id"]
    potential_dup_groups = [g for g in duplicate_groups if g.match_type == "potential_email"]

    # Track which *specific* occurrences are the "extra" (non-first) copy
    # of an exact-ID duplicate, since only those should be disqualified --
    # the first occurrence of a duplicated ID is still a legitimate record.
    seen_ids: set = set()
    extra_duplicate_flag: Dict[str, bool] = {}
    for record in normalized:
        if record.customer_id in seen_ids:
            extra_duplicate_flag[record.customer_id] = True
        else:
            seen_ids.add(record.customer_id)
            extra_duplicate_flag.setdefault(record.customer_id, False)

    potential_dup_ids = {rid for g in potential_dup_groups for rid in g.record_ids}

    # --- Business rules (on every normalized, syntactically-valid record) ---
    business_failures_by_id: Dict[str, List[BusinessRuleFailure]] = {
        record.customer_id: apply_business_rules(record) for record in normalized
    }
    normalized_by_id: Dict[str, SourceCustomerRecord] = {r.customer_id: r for r in normalized}

    # --- Assemble one RecordOutcome per raw record retrieved ---
    outcomes: List[RecordOutcome] = []
    for raw in raw_records:
        record_id = str(raw.get("customer_id") or "UNKNOWN")
        validation = validation_by_id.get(record_id)
        security_warnings = security_by_id.get(record_id, [])
        business_failures = business_failures_by_id.get(record_id, [])
        is_extra_dup = extra_duplicate_flag.get(record_id, False)
        is_potential_dup = record_id in potential_dup_ids

        reasons = _disqualifying_reasons(validation, business_failures, security_warnings, is_extra_dup)
        accepted = len(reasons) == 0

        transformed_dict: Optional[dict] = None
        if accepted:
            normalized_record = normalized_by_id[record_id]
            destination_record = transform_to_destination(normalized_record)
            transformed_dict = destination_record.model_dump()

        outcomes.append(RecordOutcome(
            record_id=record_id,
            accepted=accepted,
            reasons=reasons,
            security_warnings=security_warnings,
            is_duplicate=is_extra_dup or is_potential_dup,
            duplicate_type="exact_id" if is_extra_dup else ("potential_email" if is_potential_dup else None),
            source_record=raw,
            transformed_record=transformed_dict,
        ))

    logger.info(
        "pipeline_validation_complete",
        extra={
            "correlation_id": correlation_id,
            "accepted": sum(1 for o in outcomes if o.accepted),
            "rejected": sum(1 for o in outcomes if not o.accepted),
        },
    )

    # --- Optional submission stage ---
    if submit:
        destination_client = DestinationAPIClient(settings)
        for outcome in outcomes:
            if not outcome.accepted:
                continue
            destination_record = DestinationCustomerRecord.model_validate(outcome.transformed_record)
            outcome.submission = destination_client.submit_customer(destination_record, correlation_id)

        logger.info(
            "pipeline_submission_complete",
            extra={
                "correlation_id": correlation_id,
                "submitted": sum(1 for o in outcomes if o.submission and o.submission.success),
                "destination_rejected": sum(1 for o in outcomes if o.submission and not o.submission.success),
            },
        )

    logger.info("pipeline_end", extra={"correlation_id": correlation_id})

    return PipelineRun(
        correlation_id=correlation_id,
        fetch_warning=fetch_result.warning,
        records_retrieved=len(raw_records),
        outcomes=outcomes,
        duplicate_groups=duplicate_groups,
        submitted=submit,
    )