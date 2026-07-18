"""Tests for tap_salesforce.salesforce.client."""

from unittest.mock import MagicMock

import pytest
import requests

from tap_salesforce.salesforce.client import SalesforceClient, raise_for_status
from tap_salesforce.salesforce.exceptions import (
    SFDCCustomNotAcceptableError,
    TapSalesforceExceptionError,
    TapSalesforceQuotaExceededError,
)


class TestRaiseForStatus:
    def test_200_does_not_raise(self):
        resp = MagicMock()
        resp.status_code = 200
        raise_for_status(resp)

    def test_406_custom_not_acceptable_raises(self):
        resp = MagicMock()
        resp.status_code = 406
        resp.reason = "CustomNotAcceptable"
        resp.url = "https://example.com"
        with pytest.raises(SFDCCustomNotAcceptableError):
            raise_for_status(resp)

    def test_500_raises_http_error(self):
        resp = MagicMock()
        resp.status_code = 500
        resp.reason = "Internal Server Error"
        resp.url = "https://example.com"
        resp.raise_for_status.side_effect = requests.HTTPError("500")
        with pytest.raises(requests.HTTPError):
            raise_for_status(resp)


class TestSalesforceClient:
    def _make_client(self, **kwargs):
        auth = MagicMock()
        auth.instance_url = "https://test.salesforce.com"
        auth.rest_headers = {"Authorization": "Bearer token"}
        return SalesforceClient(auth=auth, **kwargs)

    def test_make_request_get(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        client.session.get = MagicMock(return_value=mock_resp)

        resp = client.make_request("GET", "https://example.com")
        assert resp == mock_resp
        client.session.get.assert_called_once()

    def test_make_request_post(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        client.session.post = MagicMock(return_value=mock_resp)

        resp = client.make_request("POST", "https://example.com", body='{"key":"val"}')
        assert resp == mock_resp
        client.session.post.assert_called_once()

    def test_make_request_unsupported_method_raises(self):
        client = self._make_client()
        with pytest.raises(TapSalesforceExceptionError, match="Unsupported HTTP method"):
            client.make_request("DELETE", "https://example.com")

    def test_make_request_increments_rest_requests_on_quota_header(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Sforce-Limit-Info": "api-usage=100/15000"}
        client.session.get = MagicMock(return_value=mock_resp)

        assert client.rest_requests_attempted == 0
        client.make_request("GET", "https://example.com")
        assert client.rest_requests_attempted == 1

    def test_check_rest_quota_total_exceeded_raises(self):
        client = self._make_client(quota_percent_total=50.0)
        headers = {"Sforce-Limit-Info": "api-usage=8000/10000"}  # 80% > 50%
        with pytest.raises(TapSalesforceQuotaExceededError, match="total REST quota"):
            client.check_rest_quota_usage(headers)

    def test_check_rest_quota_per_run_exceeded_raises(self):
        client = self._make_client(quota_percent_per_run=10.0)
        client.rest_requests_attempted = 2000  # exceeds 10% of 10000 = 1000
        headers = {"Sforce-Limit-Info": "api-usage=100/10000"}
        with pytest.raises(TapSalesforceQuotaExceededError, match="per replication"):
            client.check_rest_quota_usage(headers)

    def test_check_rest_quota_no_match_does_nothing(self):
        client = self._make_client()
        headers = {"Sforce-Limit-Info": "something-unexpected"}
        client.check_rest_quota_usage(headers)  # should not raise

    def test_instance_url_from_auth(self):
        client = self._make_client()
        assert client.instance_url == "https://test.salesforce.com"

    def test_login_delegates_to_auth(self):
        client = self._make_client()
        client.login()
        client.auth.login.assert_called_once()
