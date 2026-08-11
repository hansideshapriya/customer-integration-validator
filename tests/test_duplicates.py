"""
tests/test_duplicates.py
---------------------------
Tests exact-ID and potential-email duplicate detection. Builds
SourceCustomerRecord instances directly via validate_record + normalize
so email comparisons reflect real normalized values, matching what
pipeline.py actually feeds into find_duplicates.
"""
from tests.conftest import make_raw_record

from src.duplicates import duplicate_record_ids, find_duplicates
from src.transformer import normalize_source_record
from src.validators import validate_record


def _normalized(**overrides):
    result = validate_record(make_raw_record(**overrides))
    assert result.is_valid, f"Test fixture record was invalid: {result.issues}"
    return normalize_source_record(result.parsed)


class TestExactIdDuplicates:
    def test_repeated_customer_id_is_flagged_exact(self):
        records = [
            _normalized(customer_id="CUST-1006", email_address="elena.novak@globex.eu"),
            _normalized(customer_id="CUST-1006", email_address="ELENA.NOVAK@globex.eu"),
        ]
        groups = find_duplicates(records)
        exact_groups = [g for g in groups if g.match_type == "exact_id"]
        assert len(exact_groups) == 1
        assert exact_groups[0].match_value == "CUST-1006"
        assert exact_groups[0].record_ids == ["CUST-1006", "CUST-1006"]

    def test_unique_ids_produce_no_exact_group(self):
        records = [
            _normalized(customer_id="CUST-1", email_address="a@example.com"),
            _normalized(customer_id="CUST-2", email_address="b@example.com"),
        ]
        groups = find_duplicates(records)
        assert [g for g in groups if g.match_type == "exact_id"] == []


class TestPotentialEmailDuplicates:
    def test_same_email_different_ids_is_flagged_potential(self):
        # Mirrors CUST-1007 / CUST-1008 in sample_data: two different IDs,
        # same email address.
        records = [
            _normalized(customer_id="CUST-1007", email_address="sam.okafor@example.com"),
            _normalized(customer_id="CUST-1008", email_address="sam.okafor@example.com"),
        ]
        groups = find_duplicates(records)
        potential_groups = [g for g in groups if g.match_type == "potential_email"]
        assert len(potential_groups) == 1
        assert set(potential_groups[0].record_ids) == {"CUST-1007", "CUST-1008"}

    def test_email_matching_is_case_and_whitespace_insensitive(self):
        records = [
            _normalized(customer_id="CUST-1", email_address="  JOHN@EXAMPLE.COM  "),
            _normalized(customer_id="CUST-2", email_address="john@example.com"),
        ]
        groups = find_duplicates(records)
        assert any(g.match_type == "potential_email" for g in groups)

    def test_distinct_emails_are_not_flagged(self):
        records = [
            _normalized(customer_id="CUST-1", email_address="a@example.com"),
            _normalized(customer_id="CUST-2", email_address="b@example.com"),
        ]
        groups = find_duplicates(records)
        assert groups == []


class TestNoDoubleReporting:
    def test_exact_id_duplicate_is_not_also_reported_as_potential(self):
        # Same ID AND same email -- should only appear as exact_id, not
        # double-counted as potential_email too.
        records = [
            _normalized(customer_id="CUST-1006", email_address="elena.novak@globex.eu"),
            _normalized(customer_id="CUST-1006", email_address="elena.novak@globex.eu"),
        ]
        groups = find_duplicates(records)
        assert len(groups) == 1
        assert groups[0].match_type == "exact_id"


class TestDuplicateRecordIds:
    def test_flattens_all_groups_into_a_single_set(self):
        records = [
            _normalized(customer_id="CUST-1006", email_address="a@example.com"),
            _normalized(customer_id="CUST-1006", email_address="a@example.com"),
            _normalized(customer_id="CUST-1007", email_address="shared@example.com"),
            _normalized(customer_id="CUST-1008", email_address="shared@example.com"),
        ]
        groups = find_duplicates(records)
        ids = duplicate_record_ids(groups)
        assert ids == {"CUST-1006", "CUST-1007", "CUST-1008"}