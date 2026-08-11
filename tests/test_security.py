"""
tests/test_security.py
------------------------
Tests src.security against both synthetic inputs and the realistic
attack-shaped records already present in sample_data/customers.json,
so these tests double as documentation of exactly what the security
module does and does not catch.
"""
import pytest

from src.security import (
    check_text_field,
    check_url_field,
    redact_for_logging,
    scan_record,
)


class TestScriptInjectionDetection:
    def test_flags_script_tag(self):
        warnings = check_text_field("CUST-1", "first_name", "<script>alert('xss')</script>", max_len=200)
        categories = [w.category for w in warnings]
        assert "script_injection" in categories

    def test_flags_event_handler_attribute(self):
        warnings = check_text_field("CUST-1", "notes", "<img src=x onerror=alert(1)>", max_len=500)
        assert any(w.category == "script_injection" for w in warnings)

    def test_flags_javascript_scheme_in_text(self):
        warnings = check_text_field("CUST-1", "notes", "click javascript:doEvil()", max_len=500)
        assert any(w.category == "script_injection" for w in warnings)

    def test_clean_text_produces_no_script_warning(self):
        warnings = check_text_field("CUST-1", "notes", "Primary contact for onboarding.", max_len=500)
        assert not any(w.category == "script_injection" for w in warnings)


class TestSQLInjectionDetection:
    def test_flags_drop_table(self):
        warnings = check_text_field("CUST-1", "notes", "'; DROP TABLE customers; --", max_len=500)
        assert any(w.category == "sqli_pattern" for w in warnings)

    def test_flags_or_1_equals_1(self):
        warnings = check_text_field("CUST-1", "notes", "admin' OR 1=1 --", max_len=500)
        assert any(w.category == "sqli_pattern" for w in warnings)

    def test_ordinary_apostrophe_does_not_false_positive(self):
        # "O'Brien" is real, legitimate data (CUST-1012's last name in
        # sample_data) -- the SQLi patterns must not flag it.
        warnings = check_text_field("CUST-1", "last_name", "O'Brien", max_len=200)
        assert not any(w.category == "sqli_pattern" for w in warnings)


class TestSecretDetection:
    def test_flags_stripe_style_live_key(self):
        warnings = check_text_field(
            "CUST-1", "notes",
            "API key leaked in notes field: sk_live_51Hc9F3example_do_not_use_ABC123",
            max_len=500,
        )
        assert any(w.category == "possible_secret" for w in warnings)

    def test_flags_generic_password_assignment(self):
        warnings = check_text_field("CUST-1", "notes", "password: hunter2fallback", max_len=500)
        assert any(w.category == "possible_secret" for w in warnings)

    def test_clean_notes_field_has_no_secret_warning(self):
        warnings = check_text_field("CUST-1", "notes", "Cancelled subscription in Q3.", max_len=500)
        assert not any(w.category == "possible_secret" for w in warnings)


class TestUrlSafety:
    def test_flags_javascript_scheme_url(self):
        warnings = check_url_field("CUST-1", "website", "javascript:alert(document.cookie)")
        assert any(w.category == "unsafe_url" and w.severity == "high" for w in warnings)

    def test_flags_private_ip_url(self):
        warnings = check_url_field("CUST-1", "website", "http://192.168.1.50/login")
        assert any(w.category == "unsafe_url" and w.severity == "medium" for w in warnings)

    def test_flags_localhost(self):
        warnings = check_url_field("CUST-1", "website", "http://localhost/admin")
        assert any(w.category == "unsafe_url" for w in warnings)

    def test_normal_https_url_is_clean(self):
        warnings = check_url_field("CUST-1", "website", "https://riversidelogistics.com")
        assert warnings == []

    def test_empty_url_is_clean(self):
        assert check_url_field("CUST-1", "website", "") == []


class TestOversizedField:
    def test_flags_field_over_max_length(self):
        long_value = "x" * 600
        warnings = check_text_field("CUST-1", "notes", long_value, max_len=500)
        assert any(w.category == "oversized_field" for w in warnings)

    def test_field_at_exactly_max_length_not_flagged(self):
        exact_value = "x" * 500
        warnings = check_text_field("CUST-1", "notes", exact_value, max_len=500)
        assert not any(w.category == "oversized_field" for w in warnings)


class TestScanRecord:
    def test_scan_record_finds_xss_in_raw_dict(self):
        raw = {
            "customer_id": "CUST-1009",
            "first_name": "<script>alert('xss')</script>",
            "last_name": "Test",
            "notes": "<img src=x onerror=alert(1)>",
            "company_name": "N/A",
            "website": "https://example.com",
        }
        warnings = scan_record(raw)
        assert any(w.category == "script_injection" for w in warnings)
        assert all(w.record_id == "CUST-1009" for w in warnings)

    def test_scan_record_handles_missing_optional_fields_gracefully(self):
        raw = {"customer_id": "CUST-1", "first_name": "Ann", "last_name": "Lee"}
        warnings = scan_record(raw)
        assert warnings == []

    def test_scan_record_uses_unknown_when_customer_id_missing(self):
        raw = {"first_name": "<script>bad</script>", "last_name": "X"}
        warnings = scan_record(raw)
        assert warnings[0].record_id == "UNKNOWN"


class TestRedactForLogging:
    def test_redacts_sensitive_keys(self):
        payload = {"api_key": "sk_live_abc123", "customer_id": "CUST-1"}
        redacted = redact_for_logging(payload)
        assert redacted["api_key"] == "***REDACTED***"
        assert redacted["customer_id"] == "CUST-1"

    def test_truncates_pii_fields(self):
        payload = {"email_address": "colin.keegan@example.com"}
        redacted = redact_for_logging(payload)
        assert redacted["email_address"] == "col…"
        assert "example.com" not in redacted["email_address"]

    def test_does_not_mutate_original_payload(self):
        payload = {"password": "secret123"}
        redact_for_logging(payload)
        assert payload["password"] == "secret123"  # original untouched

    def test_non_sensitive_non_pii_fields_pass_through_unchanged(self):
        payload = {"customer_status": "active", "region": "NA"}
        redacted = redact_for_logging(payload)
        assert redacted == payload