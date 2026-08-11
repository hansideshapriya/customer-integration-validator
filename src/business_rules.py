"""
business_rules.py
------------------
Implementation-specific rules that go beyond "is this field syntactically
valid" -- these are the rules an implementation specialist would actually
negotiate with a customer during onboarding.

Deliberately written as a flat list of small, independent check functions
rather than one big function, so a new rule can be added by:
  1. writing a small `def rule_xxx(record) -> Optional[str]` function
  2. adding it to RULES below

Each rule function returns None if the record passes, or a human-readable
reason string if it fails. Keeping the interface this simple is what makes
the rule set "easy to understand and modify" per the project brief --
no framework, no DSL, just functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from src.config import settings
from src.models import CustomerStatus, CustomerType, SourceCustomerRecord


@dataclass
class BusinessRuleFailure:
    record_id: str
    rule: str
    message: str


def rule_no_inactive_or_churned(record: SourceCustomerRecord) -> Optional[str]:
    if record.customer_status in (CustomerStatus.INACTIVE, CustomerStatus.CHURNED):
        return (
            f"Customers with status '{record.customer_status.value}' cannot be "
            "sent to the destination system. Reactivate or exclude before go-live."
        )
    return None


def rule_enterprise_requires_company_and_website(record: SourceCustomerRecord) -> Optional[str]:
    if record.customer_type == CustomerType.ENTERPRISE:
        missing = []
        if not record.company_name:
            missing.append("company_name")
        if not record.website:
            missing.append("website")
        if missing:
            return (
                f"Enterprise-tier customers require {', '.join(missing)}, "
                "which are missing from this record."
            )
    return None


def rule_supported_region(record: SourceCustomerRecord) -> Optional[str]:
    if record.region not in settings.supported_regions:
        return (
            f"Region '{record.region}' is not one of the supported regions "
            f"({', '.join(settings.supported_regions)}) for this destination system."
        )
    return None


def rule_partner_requires_company(record: SourceCustomerRecord) -> Optional[str]:
    if record.customer_type == CustomerType.PARTNER and not record.company_name:
        return "Partner-tier customers require a company_name for revenue-share reporting."
    return None


# Ordered list of rules applied to every syntactically-valid record.
RULES: List[Callable[[SourceCustomerRecord], Optional[str]]] = [
    rule_no_inactive_or_churned,
    rule_enterprise_requires_company_and_website,
    rule_supported_region,
    rule_partner_requires_company,
]


def apply_business_rules(record: SourceCustomerRecord) -> List[BusinessRuleFailure]:
    """Run every rule in RULES against a single validated record."""
    failures: List[BusinessRuleFailure] = []
    for rule_fn in RULES:
        reason = rule_fn(record)
        if reason:
            failures.append(BusinessRuleFailure(
                record_id=record.customer_id,
                rule=rule_fn.__name__,
                message=reason,
            ))
    return failures
