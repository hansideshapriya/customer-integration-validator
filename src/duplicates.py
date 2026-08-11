"""
duplicates.py
-------------
Detects duplicate customer records within a batch using two matching
strategies, run independently so the report can distinguish *why*
something looks like a duplicate:

  * EXACT   -- same customer_id appears more than once. Almost always a
              re-export or pagination bug in the source system.
  * POTENTIAL -- different customer_id, but the same normalized email
              address. Often two reps entering the same customer, or a
              customer who signed up twice.

This module works on already-normalized records (see normalizer.py) so
that "JOHN@EXAMPLE.COM" and "john@example.com " are correctly treated as
the same identity.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

from src.models import SourceCustomerRecord


@dataclass
class DuplicateGroup:
    match_type: str            # "exact_id" | "potential_email"
    match_value: str
    record_ids: List[str]


def find_duplicates(records: List[SourceCustomerRecord]) -> List[DuplicateGroup]:
    """
    Given a list of validated, normalized records, return groups of
    records that share a business identifier.

    `records` should already have normalized emails (lowercase/trimmed) --
    duplicate detection assumes normalization has already happened
    upstream in the pipeline, since matching is only as good as the
    consistency of the values being compared.
    """
    by_id: Dict[str, List[str]] = defaultdict(list)
    by_email: Dict[str, List[str]] = defaultdict(list)

    for record in records:
        by_id[record.customer_id].append(record.customer_id)
        by_email[str(record.email_address).lower()].append(record.customer_id)

    groups: List[DuplicateGroup] = []

    for customer_id, occurrences in by_id.items():
        if len(occurrences) > 1:
            groups.append(DuplicateGroup("exact_id", customer_id, occurrences))

    exact_id_set = {g.match_value for g in groups}
    for email, occurrences in by_email.items():
        unique_ids = sorted(set(occurrences))
        if len(unique_ids) > 1:
            # Only report as a *potential* duplicate if these aren't already
            # flagged as an exact-ID duplicate (avoids double-reporting the
            # same underlying record pair under two categories).
            if not (len(unique_ids) == 1 and unique_ids[0] in exact_id_set):
                groups.append(DuplicateGroup("potential_email", email, unique_ids))

    return groups


def duplicate_record_ids(groups: List[DuplicateGroup]) -> set:
    """Flatten duplicate groups into the set of record IDs involved."""
    ids = set()
    for group in groups:
        ids.update(group.record_ids)
    return ids
