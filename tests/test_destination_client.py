"""
tests/test_destination_client.py
------------------------------------
Tests DestinationAPIClient against a mocked HTTP layer. Every code path
must return a SubmissionResult rather than raising -- these tests confirm
that holds true across success, every documented rejection status code,
retryable failures, and non-retryable failures.
"""
import requests
import responses

from src.destination_client import DestinationAPIClient
from src.models import DestinationCustomerRecord


def _customers_url(settings) -> str:
    return f"{settings.destination_api_url}/customers"


def _sample_record() -> DestinationCustomerRecord:
    return DestinationCustomerRecord(
        id="CUST-1001", name="Colin Keegan", email="colin.keegan@example.com",
        status="active", account_type="standard", region="NA",
        company="Riverside Logistics", website="https://riversidelogistics.com", notes="",
    )


class TestSuccessfulSubmission:
    @responses.activate
    def test_201_is_reported_as_success(self, test_settings):
        responses.add(
            responses.POST, _customers_url(test_settings),
            json={"id": "CUST-1001", "status": "created"}, status=201,
        )
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-1")
        assert result.success is True
        assert result.status_code == 201


class TestNonRetryableRejections:
    @responses.activate
    def test_401_is_not_retried_and_names_the_env_var(self, test_settings):
        responses.add(responses.POST, _customers_url(test_settings), status=401)
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-2")
        assert result.success is False
        assert result.retryable is False
        assert "DESTINATION_API_TOKEN" in result.message

    @responses.activate
    def test_409_conflict_is_not_retryable(self, test_settings):
        responses.add(
            responses.POST, _customers_url(test_settings),
            json={"error": "Customer already exists"}, status=409,
        )
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-3")
        assert result.success is False
        assert result.retryable is False
        assert result.status_code == 409

    @responses.activate
    def test_422_includes_the_destination_systems_error_detail(self, test_settings):
        responses.add(
            responses.POST, _customers_url(test_settings),
            json={"error": "email is required"}, status=422,
        )
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-4")
        assert result.success is False
        assert "email is required" in result.message


class TestRetryableFailures:
    @responses.activate
    def test_recovers_after_a_429(self, test_settings):
        responses.add(
            responses.POST, _customers_url(test_settings),
            status=429, headers={"Retry-After": "0"},
        )
        responses.add(
            responses.POST, _customers_url(test_settings),
            json={"id": "CUST-1001"}, status=201,
        )
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-5")
        assert result.success is True

    @responses.activate
    def test_5xx_exhausting_retries_is_reported_as_failed_but_retryable(self, test_settings):
        for _ in range(test_settings.max_retries):
            responses.add(responses.POST, _customers_url(test_settings), status=500)
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-6")
        assert result.success is False
        assert result.retryable is True

    @responses.activate
    def test_connection_error_exhausting_retries_is_reported_not_raised(self, test_settings):
        for _ in range(test_settings.max_retries):
            responses.add(
                responses.POST, _customers_url(test_settings),
                body=requests.exceptions.ConnectionError(),
            )
        client = DestinationAPIClient(test_settings)
        result = client.submit_customer(_sample_record(), correlation_id="test-7")
        assert result.success is False
        assert result.retryable is True
        assert "could not reach" in result.message.lower()