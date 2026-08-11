"""
tests/test_pipeline.py
--------------------------
Full end-to-end pipeline tests: mocks the source and destination HTTP
endpoints and runs run_pipeline() exactly as app.py would call it. Uses
a small, deliberately-crafted record set covering the main paths --
clean/accepted, security-rejected, business-rule-rejected,
schema-rejected, and an exact-ID duplicate -- rather than the full
20-record sample_data set, so failures are easy to trace to one cause.
"""
import responses

from src.pipeline import run_pipeline

RECORDS = [
    {  # CUST-A: clean, should be accepted and (if submitted) transmitted
        "customer_id": "CUST-A", "first_name": "Ann", "last_name": "Lee",
        "email_address": "ann.lee@example.com", "phone": "555-0100",
        "company_name": "Lee Co", "website": "https://lee.example.com",
        "customer_status": "active", "customer_type": "standard",
        "region": "NA", "notes": "", "created_at": "2025-01-01",
    },
    {  # CUST-B: valid schema, but XSS in notes -> high-severity security reject
        "customer_id": "CUST-B", "first_name": "Bob", "last_name": "Chen",
        "email_address": "bob.chen@example.com", "phone": "",
        "company_name": "", "website": "",
        "customer_status": "active", "customer_type": "standard",
        "region": "NA", "notes": "<script>alert(1)</script>", "created_at": "2025-01-02",
    },
    {  # CUST-C: first occurrence -- should be accepted
        "customer_id": "CUST-C", "first_name": "Cara", "last_name": "Diaz",
        "email_address": "cara.diaz@example.com", "phone": "",
        "company_name": "", "website": "",
        "customer_status": "active", "customer_type": "standard",
        "region": "NA", "notes": "", "created_at": "2025-01-03",
    },
    {  # CUST-C again: exact-ID duplicate -- second occurrence should be rejected
        "customer_id": "CUST-C", "first_name": "Cara", "last_name": "Diaz",
        "email_address": "cara.diaz@example.com", "phone": "",
        "company_name": "", "website": "",
        "customer_status": "active", "customer_type": "standard",
        "region": "NA", "notes": "Duplicate export", "created_at": "2025-01-03",
    },
    {  # CUST-D: inactive status -> business-rule reject
        "customer_id": "CUST-D", "first_name": "Dana", "last_name": "Ford",
        "email_address": "dana.ford@example.com", "phone": "",
        "company_name": "", "website": "",
        "customer_status": "inactive", "customer_type": "standard",
        "region": "NA", "notes": "", "created_at": "2025-01-04",
    },
    {  # CUST-E: invalid email -> schema reject
        "customer_id": "CUST-E", "first_name": "Eli", "last_name": "Gomez",
        "email_address": "not-a-valid-email", "phone": "",
        "company_name": "", "website": "",
        "customer_status": "active", "customer_type": "standard",
        "region": "NA", "notes": "", "created_at": "2025-01-05",
    },
]


def _mock_source(settings):
    responses.add(
        responses.GET, f"{settings.source_api_url}/customers",
        json={"records": RECORDS, "has_more": False}, status=200,
    )


def _mock_destination_accepts_everything(settings):
    # Two accepted records are expected (CUST-A and the first CUST-C) --
    # register enough successful responses to cover any accepted count
    # this test set produces.
    for _ in range(5):
        responses.add(
            responses.POST, f"{settings.destination_api_url}/customers",
            json={"status": "created"}, status=201,
        )


class TestPipelineValidationOnly:
    @responses.activate
    def test_records_retrieved_matches_source_count(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        assert run.records_retrieved == len(RECORDS)

    @responses.activate
    def test_clean_record_is_accepted(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        outcome = next(o for o in run.outcomes if o.record_id == "CUST-A")
        assert outcome.accepted is True
        assert outcome.transformed_record is not None

    @responses.activate
    def test_xss_record_is_rejected_for_security(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        outcome = next(o for o in run.outcomes if o.record_id == "CUST-B")
        assert outcome.accepted is False
        assert any("Security warning" in reason for reason in outcome.reasons)

    @responses.activate
    def test_exact_duplicate_keeps_first_and_rejects_second(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        cust_c_outcomes = [o for o in run.outcomes if o.record_id == "CUST-C"]
        assert len(cust_c_outcomes) == 2
        assert cust_c_outcomes[0].accepted is True
        assert cust_c_outcomes[1].accepted is False
        assert cust_c_outcomes[1].duplicate_type == "exact_id"

    @responses.activate
    def test_inactive_status_is_rejected_by_business_rule(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        outcome = next(o for o in run.outcomes if o.record_id == "CUST-D")
        assert outcome.accepted is False
        assert any("Business rule" in reason for reason in outcome.reasons)

    @responses.activate
    def test_invalid_email_is_rejected_by_schema_validation(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        outcome = next(o for o in run.outcomes if o.record_id == "CUST-E")
        assert outcome.accepted is False
        assert any("Validation error" in reason for reason in outcome.reasons)

    @responses.activate
    def test_nothing_is_submitted_when_submit_is_false(self, test_settings):
        _mock_source(test_settings)
        run = run_pipeline(test_settings, submit=False)
        assert run.submitted is False
        assert all(o.submission is None for o in run.outcomes)


class TestPipelineWithSubmission:
    @responses.activate
    def test_only_accepted_records_are_submitted(self, test_settings):
        _mock_source(test_settings)
        _mock_destination_accepts_everything(test_settings)

        run = run_pipeline(test_settings, submit=True)

        accepted = [o for o in run.outcomes if o.accepted]
        rejected = [o for o in run.outcomes if not o.accepted]

        assert all(o.submission is not None for o in accepted)
        assert all(o.submission is None for o in rejected)

    @responses.activate
    def test_successful_submissions_are_reflected_in_the_run(self, test_settings):
        _mock_source(test_settings)
        _mock_destination_accepts_everything(test_settings)

        run = run_pipeline(test_settings, submit=True)

        outcome_a = next(o for o in run.outcomes if o.record_id == "CUST-A")
        assert outcome_a.submission.success is True