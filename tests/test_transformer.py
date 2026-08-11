"""
tests/test_transformer.py
----------------------------
Tests normalization and source->destination transformation. Uses
validate_record to get a real, valid SourceCustomerRecord rather than
constructing the Pydantic model directly, so these tests exercise the
same path the pipeline actually uses.
"""
from tests.conftest import make_raw_record

from src.transformer import normalize_source_record, transform_to_destination
from src.validators import validate_record


def _valid_parsed_record(**overrides):
    result = validate_record(make_raw_record(**overrides))
    assert result.is_valid, f"Test fixture record was invalid: {result.issues}"
    return result.parsed


class TestNormalizeSourceRecord:
    def test_collapses_whitespace_in_names(self):
        parsed = _valid_parsed_record(first_name="  Colin  ")
        normalized = normalize_source_record(parsed)
        assert normalized.first_name == "Colin"

    def test_lowercases_and_trims_email(self):
        parsed = _valid_parsed_record(email_address=" COLIN.KEEGAN@EXAMPLE.COM ")
        normalized = normalize_source_record(parsed)
        assert normalized.email_address == "colin.keegan@example.com"

    def test_does_not_mutate_the_original_record(self):
        parsed = _valid_parsed_record(first_name="  Colin  ")
        normalize_source_record(parsed)
        # model_copy returns a new object -- the original parsed record
        # (already stripped by the model's own validator) must be untouched.
        assert parsed.first_name == "Colin"

    def test_normalizes_company_name_and_notes(self):
        parsed = _valid_parsed_record(
            company_name="  Riverside   Logistics ",
            notes="  Some   note  here ",
        )
        normalized = normalize_source_record(parsed)
        assert normalized.company_name == "Riverside Logistics"
        assert normalized.notes == "Some note here"


class TestTransformToDestination:
    def test_maps_all_fields_correctly(self):
        parsed = _valid_parsed_record(
            first_name="Colin",
            last_name="Keegan",
            email_address="colin.keegan@example.com",
            customer_status="active",
            customer_type="standard",
            region="NA",
            company_name="Riverside Logistics",
            website="https://riversidelogistics.com",
        )
        normalized = normalize_source_record(parsed)
        destination = transform_to_destination(normalized)

        assert destination.id == "CUST-1001"
        assert destination.name == "Colin Keegan"
        assert destination.email == "colin.keegan@example.com"
        assert destination.status == "active"
        assert destination.account_type == "standard"
        assert destination.region == "NA"
        assert destination.company == "Riverside Logistics"
        assert destination.website == "https://riversidelogistics.com"

    def test_full_name_concatenation_matches_the_brief_example(self):
        # Matches the exact example from the project brief:
        # first_name "Colin" + last_name "K" -> name "Colin K"
        parsed = _valid_parsed_record(first_name="Colin", last_name="K")
        normalized = normalize_source_record(parsed)
        destination = transform_to_destination(normalized)
        assert destination.name == "Colin K"

    def test_missing_optional_fields_become_empty_strings_not_none(self):
        parsed = _valid_parsed_record(company_name="", website="", notes="")
        normalized = normalize_source_record(parsed)
        destination = transform_to_destination(normalized)
        assert destination.company == ""
        assert destination.website == ""
        assert destination.notes == ""