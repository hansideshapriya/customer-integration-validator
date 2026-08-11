"""
tests/test_business_rules.py
-------------------------------
Tests each business rule function in isolation, plus apply_business_rules
running the full rule set together. Uses validate_record to get a real
parsed SourceCustomerRecord, matching what pipeline.py actually passes in.
"""
from tests.conftest import make_raw_record

from src.business_rules import (
    apply_business_rules,
    rule_enterprise_requires_company_and_website,
    rule_no_inactive_or_churned,
    rule_partner_requires_company,
    rule_supported_region,
)
from src.validators import validate_record


def _valid_parsed_record(**overrides):
    result = validate_record(make_raw_record(**overrides))
    assert result.is_valid, f"Test fixture record was invalid: {result.issues}"
    return result.parsed


class TestNoInactiveOrChurned:
    def test_inactive_status_fails(self):
        record = _valid_parsed_record(customer_status="inactive")
        assert rule_no_inactive_or_churned(record) is not None

    def test_churned_status_fails(self):
        record = _valid_parsed_record(customer_status="churned")
        assert rule_no_inactive_or_churned(record) is not None

    def test_active_status_passes(self):
        record = _valid_parsed_record(customer_status="active")
        assert rule_no_inactive_or_churned(record) is None

    def test_trial_status_passes(self):
        record = _valid_parsed_record(customer_status="trial")
        assert rule_no_inactive_or_churned(record) is None


class TestEnterpriseRequiresCompanyAndWebsite:
    def test_enterprise_missing_both_fails(self):
        record = _valid_parsed_record(
            customer_type="enterprise", company_name="", website="",
        )
        failure = rule_enterprise_requires_company_and_website(record)
        assert failure is not None
        assert "company_name" in failure and "website" in failure

    def test_enterprise_with_both_fields_passes(self):
        record = _valid_parsed_record(
            customer_type="enterprise",
            company_name="Meridian Health",
            website="https://meridianhealth.com",
        )
        assert rule_enterprise_requires_company_and_website(record) is None

    def test_standard_customer_is_exempt_even_if_missing_both(self):
        record = _valid_parsed_record(
            customer_type="standard", company_name="", website="",
        )
        assert rule_enterprise_requires_company_and_website(record) is None


class TestSupportedRegion:
    def test_unsupported_region_fails(self):
        record = _valid_parsed_record(region="ANTARCTICA")
        assert rule_supported_region(record) is not None

    def test_supported_region_passes(self):
        record = _valid_parsed_record(region="EMEA")
        assert rule_supported_region(record) is None


class TestPartnerRequiresCompany:
    def test_partner_missing_company_fails(self):
        record = _valid_parsed_record(customer_type="partner", company_name="")
        assert rule_partner_requires_company(record) is not None

    def test_partner_with_company_passes(self):
        record = _valid_parsed_record(customer_type="partner", company_name="Globex GmbH")
        assert rule_partner_requires_company(record) is None

    def test_standard_customer_is_exempt(self):
        record = _valid_parsed_record(customer_type="standard", company_name="")
        assert rule_partner_requires_company(record) is None


class TestApplyBusinessRules:
    def test_clean_record_has_no_failures(self):
        record = _valid_parsed_record()
        assert apply_business_rules(record) == []

    def test_record_failing_multiple_rules_reports_all_of_them(self):
        # inactive AND unsupported region at once -- both should surface,
        # not just the first one found.
        record = _valid_parsed_record(customer_status="inactive", region="ANTARCTICA")
        failures = apply_business_rules(record)
        rule_names = {f.rule for f in failures}
        assert "rule_no_inactive_or_churned" in rule_names
        assert "rule_supported_region" in rule_names

    def test_failure_objects_carry_the_record_id(self):
        record = _valid_parsed_record(customer_id="CUST-9999", customer_status="churned")
        failures = apply_business_rules(record)
        assert all(f.record_id == "CUST-9999" for f in failures)