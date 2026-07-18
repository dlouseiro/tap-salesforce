"""Tests for tap_salesforce.salesforce.bulk."""

from unittest.mock import MagicMock, patch

import pytest

from tap_salesforce.salesforce.bulk import (
    BATCH_STATUS_POLLING_SLEEP,
    Bulk,
    find_parent,
)
from tap_salesforce.salesforce.exceptions import (
    TapSalesforceQuotaExceededError,
)


class TestFindParent:
    def test_clean_info_suffix(self):
        assert find_parent("AccountCleanInfo") == "Account"

    def test_field_history_suffix(self):
        assert find_parent("AccountFieldHistory") == "Account"

    def test_history_suffix(self):
        assert find_parent("AccountHistory") == "Account"

    def test_custom_object_history(self):
        # Custom__History → Custom__ → Custom__c
        assert find_parent("Custom__History") == "Custom__c"

    def test_no_suffix_match(self):
        assert find_parent("Account") == "Account"

    def test_custom_field_history(self):
        assert find_parent("MyObj__FieldHistory") == "MyObj__c"


class TestBulkCheckQuotaUsage:
    def _make_sf_mock(self, quota_percent_per_run=25, quota_percent_total=80, jobs_completed=0):
        sf = MagicMock()
        sf.quota_percent_per_run = quota_percent_per_run
        sf.quota_percent_total = quota_percent_total
        sf.jobs_completed = jobs_completed
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.data_url = "{}/services/data/{}/{}"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "DailyBulkApiBatches": {"Max": 10000, "Remaining": 9000}
        }
        sf._make_request.return_value = mock_resp
        return sf

    def test_passes_when_within_quota(self):
        sf = self._make_sf_mock()
        bulk = Bulk(sf)
        bulk.check_bulk_quota_usage()  # should not raise

    def test_raises_when_total_quota_exceeded(self):
        sf = self._make_sf_mock(quota_percent_total=5)  # 10% used > 5% threshold
        bulk = Bulk(sf)
        with pytest.raises(TapSalesforceQuotaExceededError, match="total Bulk API quota"):
            bulk.check_bulk_quota_usage()

    def test_raises_when_per_run_quota_exceeded(self):
        sf = self._make_sf_mock(quota_percent_per_run=1, jobs_completed=200)
        # max_requests_for_run = (1 * 10000) / 100 = 100; jobs_completed=200 > 100
        bulk = Bulk(sf)
        with pytest.raises(TapSalesforceQuotaExceededError, match="per replication"):
            bulk.check_bulk_quota_usage()


class TestBulkCreateJob:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.bulk_headers = {"X-SFDC-Session": "token", "Content-Type": "application/json"}

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "job123"}
        sf._make_request.return_value = mock_resp
        return sf

    def test_creates_basic_job(self):
        sf = self._make_sf_mock()
        bulk = Bulk(sf)
        catalog_entry = {"stream": "Account"}
        job_id = bulk._create_job(catalog_entry)
        assert job_id == "job123"
        sf._make_request.assert_called_once()
        call_args = sf._make_request.call_args
        assert call_args[0][0] == "POST"
        assert "job" in call_args[0][1]

    def test_creates_pk_chunking_job(self):
        sf = self._make_sf_mock()
        bulk = Bulk(sf)
        catalog_entry = {"stream": "Account"}
        bulk._create_job(catalog_entry, pk_chunking=True)
        call_args = sf._make_request.call_args
        headers = call_args[1]["headers"] if "headers" in call_args[1] else call_args[0][2]
        assert "Sforce-Enable-PKChunking" in headers

    def test_pk_chunking_with_history_sets_parent(self):
        sf = self._make_sf_mock()
        bulk = Bulk(sf)
        catalog_entry = {"stream": "AccountHistory"}
        bulk._create_job(catalog_entry, pk_chunking=True)
        call_args = sf._make_request.call_args
        headers = call_args[1]["headers"] if "headers" in call_args[1] else call_args[0][2]
        assert "parent=Account" in headers["Sforce-Enable-PKChunking"]


class TestBulkJobExists:
    def test_returns_true_when_job_found(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.bulk_headers = {"X-SFDC-Session": "token", "Content-Type": "application/json"}
        sf._make_request.return_value = MagicMock()

        bulk = Bulk(sf)
        assert bulk.job_exists("job123") is True

    def test_returns_false_on_invalid_job(self):
        from requests.exceptions import RequestException

        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.bulk_headers = {"X-SFDC-Session": "token", "Content-Type": "application/json"}

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {"exceptionCode": "InvalidJob"}
        exc = RequestException(response=mock_resp)
        exc.response = mock_resp
        sf._make_request.side_effect = exc

        bulk = Bulk(sf)
        assert bulk.job_exists("bad_job") is False


class TestBulkGetBatchResults:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.bulk_headers = {"X-SFDC-Session": "token", "Content-Type": "application/json"}
        return sf

    def test_parses_csv_results(self):
        sf = self._make_sf_mock()

        # Mock the result list response
        result_list_resp = MagicMock()
        result_list_resp.text = "<result-list><result>result1</result></result-list>"

        # Mock the CSV result response
        csv_data = "Id,Name\n001,Alice\n002,Bob\n"
        csv_resp = MagicMock()
        csv_resp.iter_content.return_value = [csv_data]

        sf._make_request.side_effect = [result_list_resp, csv_resp]

        bulk = Bulk(sf)
        catalog_entry = {"stream": "Account"}
        results = list(bulk.get_batch_results("job1", "batch1", catalog_entry))

        assert len(results) == 2
        assert results[0]["Id"] == "001"
        assert results[0]["Name"] == "Alice"
        assert results[1]["Id"] == "002"
        assert results[1]["Name"] == "Bob"

    def test_strips_null_bytes(self):
        sf = self._make_sf_mock()

        result_list_resp = MagicMock()
        result_list_resp.text = "<result-list><result>result1</result></result-list>"

        csv_data = "Id,Name\n001,Al\x00ice\n"
        csv_resp = MagicMock()
        csv_resp.iter_content.return_value = [csv_data]

        sf._make_request.side_effect = [result_list_resp, csv_resp]

        bulk = Bulk(sf)
        results = list(bulk.get_batch_results("job1", "batch1", {"stream": "Account"}))
        assert results[0]["Name"] == "Alice"


class TestBulkPollBatchStatus:
    def test_returns_when_completed(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.bulk_headers = {"X-SFDC-Session": "token", "Content-Type": "application/json"}

        completed_resp = MagicMock()
        completed_resp.text = "<batchInfo><id>b1</id><state>Completed</state></batchInfo>"
        sf._make_request.return_value = completed_resp

        bulk = Bulk(sf)
        result = bulk._poll_on_batch_status("job1", "batch1")
        assert result["state"] == "Completed"

    @patch("tap_salesforce.salesforce.bulk.time.sleep")
    def test_polls_until_completed(self, mock_sleep):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.bulk_headers = {"X-SFDC-Session": "token", "Content-Type": "application/json"}

        in_progress_resp = MagicMock()
        in_progress_resp.text = "<batchInfo><id>b1</id><state>InProgress</state></batchInfo>"

        completed_resp = MagicMock()
        completed_resp.text = "<batchInfo><id>b1</id><state>Completed</state></batchInfo>"

        sf._make_request.side_effect = [in_progress_resp, completed_resp]

        bulk = Bulk(sf)
        result = bulk._poll_on_batch_status("job1", "batch1")
        assert result["state"] == "Completed"
        mock_sleep.assert_called_once_with(BATCH_STATUS_POLLING_SLEEP)
