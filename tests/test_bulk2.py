"""Tests for tap_salesforce.salesforce.bulk2."""

import json
from unittest.mock import MagicMock, patch

import pytest

from tap_salesforce.salesforce.bulk2 import Bulk2


class TestBulk2CreateJob:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        sf.get_start_date.return_value = "2024-01-01T00:00:00Z"
        sf._build_query_string.return_value = "SELECT Id FROM Account"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "job456"}
        sf._make_request.return_value = mock_resp
        return sf

    def test_creates_job_with_correct_body(self):
        sf = self._make_sf_mock()
        bulk2 = Bulk2(sf)
        catalog_entry = {"stream": "Account", "tap_stream_id": "Account", "metadata": [], "schema": {}}
        state = {}
        job_id = bulk2._create_job(catalog_entry, state)
        assert job_id == "job456"
        call_args = sf._make_request.call_args
        body = json.loads(call_args[1]["body"] if "body" in call_args[1] else call_args[0][3])
        assert body["operation"] == "queryAll"
        assert body["query"] == "SELECT Id FROM Account"


class TestBulk2WaitForJob:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        return sf

    @patch("tap_salesforce.salesforce.bulk2.time.sleep")
    def test_waits_until_job_complete(self, mock_sleep):
        sf = self._make_sf_mock()
        mock_resp_in_progress = MagicMock()
        mock_resp_in_progress.json.return_value = {"state": "InProgress"}
        mock_resp_complete = MagicMock()
        mock_resp_complete.json.return_value = {"state": "JobComplete"}
        sf._make_request.side_effect = [mock_resp_in_progress, mock_resp_complete]

        bulk2 = Bulk2(sf)
        bulk2._wait_for_job("job456")
        mock_sleep.assert_called_once()

    def test_raises_on_failed_job(self):
        sf = self._make_sf_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"state": "Failed", "errorMessage": "something broke"}
        sf._make_request.return_value = mock_resp

        bulk2 = Bulk2(sf)
        with pytest.raises(Exception, match="Job failed"):
            bulk2._wait_for_job("job456")


class TestBulk2GetNextBatch:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        return sf

    def test_yields_single_batch(self):
        sf = self._make_sf_mock()
        mock_resp = MagicMock()
        mock_resp.content = b"Id,Name\n001,Alice\n"
        mock_resp.headers = {"Sforce-Locator": "null"}
        sf._make_request.return_value = mock_resp

        bulk2 = Bulk2(sf)
        batches = list(bulk2._get_next_batch("job456"))
        assert len(batches) == 1
        assert b"Id,Name" in batches[0]

    def test_yields_multiple_batches_with_locator(self):
        sf = self._make_sf_mock()
        resp1 = MagicMock()
        resp1.content = b"Id,Name\n001,Alice\n"
        resp1.headers = {"Sforce-Locator": "next123"}

        resp2 = MagicMock()
        resp2.content = b"Id,Name\n002,Bob\n"
        resp2.headers = {"Sforce-Locator": "null"}

        sf._make_request.side_effect = [resp1, resp2]

        bulk2 = Bulk2(sf)
        batches = list(bulk2._get_next_batch("job456"))
        assert len(batches) == 2


class TestBulk2Query:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        sf.get_start_date.return_value = "2024-01-01T00:00:00Z"
        sf._build_query_string.return_value = "SELECT Id,Name FROM Account"
        return sf

    @patch.object(Bulk2, "_wait_for_job")
    def test_end_to_end_query(self, mock_wait):
        sf = self._make_sf_mock()

        # Create job response
        create_resp = MagicMock()
        create_resp.json.return_value = {"id": "job789"}

        # Get results response
        results_resp = MagicMock()
        results_resp.content = b"Id,Name\n001,Alice\n002,Bob\n"
        results_resp.headers = {"Sforce-Locator": "null"}

        sf._make_request.side_effect = [create_resp, results_resp]

        bulk2 = Bulk2(sf)
        catalog_entry = {"stream": "Account", "tap_stream_id": "Account", "metadata": [], "schema": {}}
        records = list(bulk2.query(catalog_entry, {}))
        assert len(records) == 2
        assert records[0]["Id"] == "001"
        assert records[1]["Name"] == "Bob"
