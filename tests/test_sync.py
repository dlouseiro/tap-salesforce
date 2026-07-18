"""Tests for tap_salesforce.sync."""

from unittest.mock import MagicMock, patch

import pytest

from tap_salesforce.sync import (
    SyncService,
    fix_record_anytype,
    get_stream_version,
    remove_blacklisted_fields,
    transform_bulk_data_hook,
)


class TestRemoveBlacklistedFields:
    def test_removes_attributes(self):
        data = {"attributes": {"type": "Account"}, "Id": "001", "Name": "Test"}
        result = remove_blacklisted_fields(data)
        assert "attributes" not in result
        assert result == {"Id": "001", "Name": "Test"}

    def test_preserves_non_blacklisted_fields(self):
        data = {"Id": "001", "Name": "Test"}
        result = remove_blacklisted_fields(data)
        assert result == data

    def test_empty_dict(self):
        assert remove_blacklisted_fields({}) == {}


class TestTransformBulkDataHook:
    def test_removes_attributes_from_dict(self):
        data = {"attributes": {"type": "Account"}, "Id": "001"}
        schema = {"type": ["string", "null"]}
        result = transform_bulk_data_hook(data, "object", schema)
        assert "attributes" not in result

    def test_empty_string_to_none_when_nullable(self):
        data = ""
        schema = {"type": ["string", "null"]}
        result = transform_bulk_data_hook(data, "string", schema)
        assert result is None

    def test_empty_string_preserved_when_not_nullable(self):
        data = ""
        schema = {"type": "string"}
        result = transform_bulk_data_hook(data, "string", schema)
        assert result == ""

    def test_non_empty_string_preserved(self):
        data = "hello"
        schema = {"type": ["string", "null"]}
        result = transform_bulk_data_hook(data, "string", schema)
        assert result == "hello"


class TestFixRecordAnytype:
    def test_casts_numeric_string_to_int(self):
        rec = {"val": "42"}
        schema = {"properties": {"val": {}}}  # no type = anyType
        result = fix_record_anytype(rec, schema)
        assert result["val"] == 42.0  # int("42")=42, then float("42")=42.0

    def test_casts_float_string(self):
        rec = {"val": "3.14"}
        schema = {"properties": {"val": {}}}
        result = fix_record_anytype(rec, schema)
        assert result["val"] == 3.14

    def test_casts_true_to_boolean(self):
        rec = {"val": "true"}
        schema = {"properties": {"val": {}}}
        result = fix_record_anytype(rec, schema)
        assert result["val"] is True

    def test_casts_false_to_boolean(self):
        rec = {"val": "false"}
        schema = {"properties": {"val": {}}}
        result = fix_record_anytype(rec, schema)
        assert result["val"] is False

    def test_empty_string_to_none(self):
        rec = {"val": ""}
        schema = {"properties": {"val": {}}}
        result = fix_record_anytype(rec, schema)
        assert result["val"] is None

    def test_regular_string_preserved(self):
        rec = {"val": "hello"}
        schema = {"properties": {"val": {}}}
        result = fix_record_anytype(rec, schema)
        assert result["val"] == "hello"

    def test_field_with_type_not_modified(self):
        rec = {"val": "42"}
        schema = {"properties": {"val": {"type": "string"}}}
        result = fix_record_anytype(rec, schema)
        assert result["val"] == "42"  # not cast because type is present


class TestGetStreamVersion:
    def _make_catalog_entry(self, replication_key="SystemModstamp"):
        meta = [{"breadcrumb": [], "metadata": {}}]
        if replication_key:
            meta[0]["metadata"]["replication-key"] = replication_key
        return {
            "tap_stream_id": "Account",
            "stream": "Account",
            "metadata": meta,
            "schema": {},
        }

    def test_returns_existing_version_for_incremental(self):
        state = {"bookmarks": {"Account": {"version": 12345}}}
        entry = self._make_catalog_entry()
        version = get_stream_version(entry, state)
        assert version == 12345

    def test_generates_new_version_when_no_bookmark(self):
        state = {}
        entry = self._make_catalog_entry()
        version = get_stream_version(entry, state)
        assert isinstance(version, int)
        assert version > 0

    def test_full_table_always_generates_new_version(self):
        state = {"bookmarks": {"Account": {"version": 12345}}}
        entry = self._make_catalog_entry(replication_key=None)
        version = get_stream_version(entry, state)
        # Full table (no replication_key) always gets a new timestamp-based version
        assert version != 12345


class TestSyncService:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.pk_chunking = False
        sf.get_start_date.return_value = "2024-01-01T00:00:00Z"
        sf.query.return_value = []
        return sf

    def _make_catalog_entry(self, replication_key="SystemModstamp"):
        meta = [{"breadcrumb": [], "metadata": {}}]
        if replication_key:
            meta[0]["metadata"]["replication-key"] = replication_key
        return {
            "tap_stream_id": "Account",
            "stream": "Account",
            "metadata": meta,
            "schema": {"properties": {"Id": {"type": "string"}, "Name": {"type": ["null", "string"]}}},
        }

    @patch("tap_salesforce.sync.singer")
    def test_sync_stream_writes_state_on_empty_query(self, mock_singer):
        sf = self._make_sf_mock()
        service = SyncService(sf)
        entry = self._make_catalog_entry()
        state = {}
        service.sync_stream(entry, state, 1000)
        mock_singer.write_state.assert_called()

    @patch("tap_salesforce.sync.singer")
    def test_sync_stream_raises_on_request_error(self, mock_singer):
        sf = self._make_sf_mock()
        mock_resp = MagicMock()
        mock_resp.text = "error details"
        from requests.exceptions import RequestException
        sf.query.side_effect = RequestException(response=mock_resp)
        service = SyncService(sf)
        entry = self._make_catalog_entry()
        with pytest.raises(Exception, match="Error syncing Account"):
            service.sync_stream(entry, {}, 1000)
