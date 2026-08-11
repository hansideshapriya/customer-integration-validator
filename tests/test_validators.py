"""
tests/test_validators.py
--------------------------
Tests src.validators against realistic valid, malformed, and missing-field
raw records. Also confirms validate_record never raises -- the whole
point of ValidationResult is that schema problems are data, not exceptions.
"""
from tests.conftest import make_raw_record

from src.validators import validate_batch, validate_record


class TestValidRecords:
    def test_valid_record_parses_successfully(self):
        result = validate_record(make_raw_record())
        assert result.is_valid is True
        assert result.parsed is not None
        assert result.issues == []

    def test_names_are_stripped_by_the_model(self):
        result = validate_record(make_raw_record(first_name="  Colin  "))
        assert result.is_valid is True
        assert result.parsed.first_name == "Colin"


class TestMissingAndMalformedFields:
    def test_missing_required_field_fails(self):
        raw = make_raw_record()
        del raw["email_address"]
        result = validate_record(raw)
        assert result.is_valid is False
        assert any("email_address" in issue.field for issue in result.issues)

    def test_invalid_email_format_fails(self):
        result = validate_record(make_raw_record(email_address="not-a-valid-email"))
        assert result.is_valid is False
        assert any("email_address" in issue.field for issue in result.issues)

    def test_blank_customer_id_fails(self):
        result = validate_record(make_raw_record(customer_id=""))
        assert result.is_valid is False

    def test_null_customer_id_fails(self):
        result = validate_record(make_raw_record(customer_id=None))
        assert result.is_valid is False

    def test_unsupported_status_value_fails(self):
        result = validate_record(make_raw_record(customer_status="bogus_status"))
        assert result.is_valid is False

    def test_unsupported_type_value_fails(self):
        result = validate_record(make_raw_record(customer_type="bogus_type"))
        assert result.is_valid is False

    def test_null_status_fails(self):
        # Mirrors CUST-1014 in sample_data -- status missing from source export.
        result = validate_record(make_raw_record(customer_status=None))
        assert result.is_valid is False

    def test_oversized_first_name_fails(self):
        result = validate_record(make_raw_record(first_name="x" * 200))
        assert result.is_valid is False

    def test_never_raises_on_completely_empty_record(self):
        # The pipeline must be able to keep processing other records even
        # if one is this badly malformed -- validate_record must return,
        # not throw.
        result = validate_record({})
        assert result.is_valid is False
        assert result.record_id == "UNKNOWN"


class TestValidateBatch:
    def test_batch_processes_all_records_independently(self):
        raw_records = [
            make_raw_record(customer_id="CUST-1"),
            make_raw_record(customer_id="CUST-2", email_address="not-valid"),
            make_raw_record(customer_id="CUST-3"),
        ]
        results = validate_batch(raw_records)
        assert len(results) == 3
        assert results[0].is_valid is True
        assert results[1].is_valid is False
        assert results[2].is_valid is True

    def test_batch_preserves_order(self):
        raw_records = [make_raw_record(customer_id=f"CUST-{i}") for i in range(5)]
        results = validate_batch(raw_records)
        assert [r.record_id for r in results] == [f"CUST-{i}" for i in range(5)]