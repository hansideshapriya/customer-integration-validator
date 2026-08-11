"""
reporting.py
------------
Turns a PipelineRun into the integration report described in the
project brief -- the artifact an implementation specialist actually
hands to a stakeholder to answer "can this dataset safely go live?"

Two output shapes are provided on purpose:
  * IntegrationReport  -- structured dataclass, used by app.py to render
    the summary/tables in the UI.
  * to_markdown() / to_dict() -- flat, serializable versions for the
    "download report" feature. Markdown for humans, dict/JSON for anyone
    who wants to pipe this into another tool.

This module does no calculation beyond counting and grouping -- every
number here traces back to a RecordOutcome the pipeline already produced,
so the report can never disagree with what actually happened.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from src.pipeline import PipelineRun, RecordOutcome


@dataclass
class IntegrationReport:
    generated_at: str
    correlation_id: str

    records_retrieved: int
    records_accepted: int
    records_rejected: int
    duplicate_records: int

    validation_error_counts: Dict[str, int]      # error_type -> count
    business_rule_failure_counts: Dict[str, int]  # rule name -> count
    security_warning_counts: Dict[str, int]       # category -> count
    security_warnings_by_severity: Dict[str, int]  # "high"/"medium"/"low" -> count

    fetch_warning: str | None

    submitted: bool
    records_transmitted: int
    records_rejected_by_destination: int
    destination_failure_counts: Dict[str, int]     # status_code (as str) -> count

    rejected_record_ids: List[str] = field(default_factory=list)
    go_live_ready: bool = False

    def summary_line(self) -> str:
        if not self.records_retrieved:
            return "No records retrieved -- cannot assess go-live readiness."
        if self.go_live_ready:
            return f"All {self.records_accepted} accepted records passed validation. Dataset is ready to submit."
        return (
            f"{self.records_rejected} of {self.records_retrieved} records need attention "
            "before this dataset can safely go live."
        )


def _count_by(items, key_fn) -> Dict[str, int]:
    counter: Counter = Counter(key_fn(item) for item in items)
    return dict(sorted(counter.items(), key=lambda kv: -kv[1]))


def build_report(run: PipelineRun) -> IntegrationReport:
    """Compute an IntegrationReport from a completed PipelineRun. Pure function -- no I/O."""
    outcomes = run.outcomes
    accepted = [o for o in outcomes if o.accepted]
    rejected = [o for o in outcomes if not o.accepted]
    duplicates = [o for o in outcomes if o.is_duplicate]

    # Validation error types: parse back out of the human-readable reason
    # strings would be fragile, so instead we recompute directly from the
    # reasons' known prefixes set in pipeline._disqualifying_reasons.
    validation_errors: List[str] = []
    business_failures: List[str] = []
    for o in rejected:
        for reason in o.reasons:
            if reason.startswith("Validation error"):
                validation_errors.append(reason.split(":", 1)[0])
            elif reason.startswith("Business rule"):
                rule_name = reason.split("'")[1] if "'" in reason else "unknown_rule"
                business_failures.append(rule_name)

    all_security_warnings = [w for o in outcomes for w in o.security_warnings]

    submitted_outcomes = [o for o in outcomes if o.submission is not None]
    transmitted = [o for o in submitted_outcomes if o.submission.success]
    destination_rejected = [o for o in submitted_outcomes if not o.submission.success]
    destination_failure_counts = _count_by(
        destination_rejected, lambda o: str(o.submission.status_code or "network_error")
    )

    go_live_ready = run.records_retrieved > 0 and len(rejected) == 0 and run.fetch_warning is None

    return IntegrationReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        correlation_id=run.correlation_id,
        records_retrieved=run.records_retrieved,
        records_accepted=len(accepted),
        records_rejected=len(rejected),
        duplicate_records=len(duplicates),
        validation_error_counts=_count_by(validation_errors, lambda x: x) if validation_errors else {},
        business_rule_failure_counts=_count_by(business_failures, lambda x: x) if business_failures else {},
        security_warning_counts=_count_by(all_security_warnings, lambda w: w.category) if all_security_warnings else {},
        security_warnings_by_severity=_count_by(all_security_warnings, lambda w: w.severity) if all_security_warnings else {},
        fetch_warning=run.fetch_warning,
        submitted=run.submitted,
        records_transmitted=len(transmitted),
        records_rejected_by_destination=len(destination_rejected),
        destination_failure_counts=destination_failure_counts,
        rejected_record_ids=[o.record_id for o in rejected],
        go_live_ready=go_live_ready,
    )


def to_dict(report: IntegrationReport) -> dict:
    """JSON-serializable version of the report, for download or piping into another tool."""
    return {
        "generated_at": report.generated_at,
        "correlation_id": report.correlation_id,
        "summary": report.summary_line(),
        "go_live_ready": report.go_live_ready,
        "counts": {
            "records_retrieved": report.records_retrieved,
            "records_accepted": report.records_accepted,
            "records_rejected": report.records_rejected,
            "duplicate_records": report.duplicate_records,
            "records_transmitted": report.records_transmitted,
            "records_rejected_by_destination": report.records_rejected_by_destination,
        },
        "validation_error_counts": report.validation_error_counts,
        "business_rule_failure_counts": report.business_rule_failure_counts,
        "security_warning_counts": report.security_warning_counts,
        "security_warnings_by_severity": report.security_warnings_by_severity,
        "destination_failure_counts": report.destination_failure_counts,
        "rejected_record_ids": report.rejected_record_ids,
        "fetch_warning": report.fetch_warning,
        "submitted": report.submitted,
    }


def to_json(report: IntegrationReport) -> str:
    return json.dumps(to_dict(report), indent=2)


def to_markdown(report: IntegrationReport) -> str:
    """Human-readable report -- what gets offered as the 'Download report' file in the UI."""
    lines: List[str] = []
    lines.append("# Customer Integration Report")
    lines.append("")
    lines.append(f"Generated: {report.generated_at}")
    lines.append(f"Correlation ID: `{report.correlation_id}`")
    lines.append("")
    lines.append(f"**{report.summary_line()}**")
    lines.append("")

    if report.fetch_warning:
        lines.append(f"> ⚠️ {report.fetch_warning}")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    lines.append(f"| Records retrieved | {report.records_retrieved} |")
    lines.append(f"| Records accepted | {report.records_accepted} |")
    lines.append(f"| Records rejected | {report.records_rejected} |")
    lines.append(f"| Duplicate records flagged | {report.duplicate_records} |")
    if report.submitted:
        lines.append(f"| Records transmitted to destination | {report.records_transmitted} |")
        lines.append(f"| Records rejected by destination | {report.records_rejected_by_destination} |")
    lines.append("")

    if report.validation_error_counts:
        lines.append("## Validation errors by type")
        lines.append("")
        for error_type, count in report.validation_error_counts.items():
            lines.append(f"- `{error_type}`: {count}")
        lines.append("")

    if report.business_rule_failure_counts:
        lines.append("## Business-rule failures by rule")
        lines.append("")
        for rule, count in report.business_rule_failure_counts.items():
            lines.append(f"- `{rule}`: {count}")
        lines.append("")

    if report.security_warning_counts:
        lines.append("## Security warnings")
        lines.append("")
        lines.append(f"By severity: {report.security_warnings_by_severity}")
        lines.append("")
        for category, count in report.security_warning_counts.items():
            lines.append(f"- `{category}`: {count}")
        lines.append("")

    if report.submitted and report.destination_failure_counts:
        lines.append("## Destination rejections by status code")
        lines.append("")
        for status_code, count in report.destination_failure_counts.items():
            lines.append(f"- `{status_code}`: {count}")
        lines.append("")

    if report.rejected_record_ids:
        lines.append("## Rejected record IDs")
        lines.append("")
        lines.append(", ".join(f"`{rid}`" for rid in report.rejected_record_ids))
        lines.append("")

    return "\n".join(lines)