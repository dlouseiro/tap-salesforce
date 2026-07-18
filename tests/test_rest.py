"""Tests for tap_salesforce.salesforce.rest."""

from unittest.mock import MagicMock

import pytest
from requests.exceptions import HTTPError

from tap_salesforce.salesforce.exceptions import TapSalesforceExceptionError
from tap_salesforce.salesforce.rest import Rest


class TestRestQuery:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        sf.get_start_date.return_value = "2024-01-01T00:00:00Z"
        sf._build_query_string.return_value = "SELECT Id FROM Account"
        return sf

    def test_query_delegates_to_query_recur(self):
        sf = self._make_sf_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"records": [{"Id": "001"}], "nextRecordsUrl": None}
        sf._make_request.return_value = mock_resp

        rest = Rest(sf)
        catalog_entry = {"stream": "Account", "tap_stream_id": "Account", "metadata": [], "schema": {}}
        records = list(rest.query(catalog_entry, {}))
        assert len(records) == 1
        assert records[0]["Id"] == "001"


class TestRestSyncRecords:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        return sf

    def test_paginates_with_next_records_url(self):
        sf = self._make_sf_mock()

        resp1 = MagicMock()
        resp1.json.return_value = {
            "records": [{"Id": "001"}],
            "nextRecordsUrl": "/services/data/v60.0/query/next123",
        }
        resp2 = MagicMock()
        resp2.json.return_value = {
            "records": [{"Id": "002"}],
            "nextRecordsUrl": None,
        }
        sf._make_request.side_effect = [resp1, resp2]

        rest = Rest(sf)
        records = list(rest._sync_records(
            "https://test.salesforce.com/services/data/v60.0/queryAll",
            sf.auth.rest_headers,
            {"q": "SELECT Id FROM Account"},
        ))
        assert len(records) == 2
        assert records[0]["Id"] == "001"
        assert records[1]["Id"] == "002"

    def test_single_page_no_pagination(self):
        sf = self._make_sf_mock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "records": [{"Id": "001"}, {"Id": "002"}, {"Id": "003"}],
            "nextRecordsUrl": None,
        }
        sf._make_request.return_value = mock_resp

        rest = Rest(sf)
        records = list(rest._sync_records(
            "https://test.salesforce.com/services/data/v60.0/queryAll",
            sf.auth.rest_headers,
            {"q": "SELECT Id FROM Account"},
        ))
        assert len(records) == 3


class TestRestQueryTimeout:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.instance_url = "https://test.salesforce.com"
        sf.api_version = "v60.0"
        sf.auth.rest_headers = {"Authorization": "Bearer token"}
        sf.get_start_date.return_value = "2024-01-01T00:00:00.000000Z"
        sf._build_query_string.return_value = "SELECT Id FROM Account WHERE SystemModstamp >= 2024-01-01T00:00:00Z"
        return sf

    def test_raises_after_max_retries(self):
        sf = self._make_sf_mock()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"errorCode": "QUERY_TIMEOUT"}]
        exc = HTTPError(response=mock_resp)
        exc.response = mock_resp
        sf._make_request.side_effect = exc

        rest = Rest(sf)
        catalog_entry = {"stream": "Account", "tap_stream_id": "Account", "metadata": [], "schema": {}}

        with pytest.raises(TapSalesforceExceptionError, match="Ran out of retries"):
            list(rest.query(catalog_entry, {}))

    def test_non_timeout_error_is_raised_immediately(self):
        sf = self._make_sf_mock()

        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"errorCode": "INVALID_FIELD"}]
        exc = HTTPError(response=mock_resp)
        exc.response = mock_resp
        sf._make_request.side_effect = exc

        rest = Rest(sf)
        catalog_entry = {"stream": "Account", "tap_stream_id": "Account", "metadata": [], "schema": {}}

        with pytest.raises(HTTPError):
            list(rest.query(catalog_entry, {}))
