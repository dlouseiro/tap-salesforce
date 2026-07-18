"""Tests for tap_salesforce.discovery."""

from unittest.mock import MagicMock, patch

from tap_salesforce.discovery import DiscoveryService, get_replication_key


class TestGetReplicationKey:
    def test_system_modstamp_preferred(self):
        fields = [{"name": "SystemModstamp"}, {"name": "LastModifiedDate"}, {"name": "CreatedDate"}]
        assert get_replication_key("Account", fields) == "SystemModstamp"

    def test_last_modified_date_fallback(self):
        fields = [{"name": "LastModifiedDate"}, {"name": "CreatedDate"}]
        assert get_replication_key("Account", fields) == "LastModifiedDate"

    def test_created_date_fallback(self):
        fields = [{"name": "CreatedDate"}]
        assert get_replication_key("Account", fields) == "CreatedDate"

    def test_login_time_for_login_history(self):
        fields = [{"name": "LoginTime"}]
        assert get_replication_key("LoginHistory", fields) == "LoginTime"

    def test_login_time_not_used_for_other_objects(self):
        fields = [{"name": "LoginTime"}]
        assert get_replication_key("Account", fields) is None

    def test_no_replication_key_returns_none(self):
        fields = [{"name": "Id"}, {"name": "Name"}]
        assert get_replication_key("Account", fields) is None

    def test_forced_full_table_returns_none(self):
        fields = [{"name": "SystemModstamp"}, {"name": "LastModifiedDate"}]
        assert get_replication_key("BackgroundOperationResult", fields) is None


class TestDiscoveryService:
    def _make_sf_mock(self):
        sf = MagicMock()
        sf.api_type = "BULK"
        sf.select_fields_by_default = True
        sf.ignore_formula_fields = False
        sf.get_blacklisted_objects.return_value = set()
        sf.get_blacklisted_fields.return_value = {}
        return sf

    def test_build_batches_splits_at_25(self):
        sf = self._make_sf_mock()
        service = DiscoveryService(sf)
        objects = {f"Object{i}" for i in range(60)}
        batches = service._build_batches(objects)
        assert all(len(b) <= 25 for b in batches)
        total = sum(len(b) for b in batches)
        assert total == 60

    def test_build_batches_excludes_blacklisted(self):
        sf = self._make_sf_mock()
        sf.get_blacklisted_objects.return_value = {"BadObject"}
        service = DiscoveryService(sf)
        objects = {"GoodObject", "BadObject"}
        batches = service._build_batches(objects)
        all_objects = [obj for batch in batches for obj in batch]
        assert "BadObject" not in all_objects
        assert "GoodObject" in all_objects

    def test_build_batches_excludes_change_events(self):
        sf = self._make_sf_mock()
        service = DiscoveryService(sf)
        objects = {"Account", "AccountChangeEvent"}
        batches = service._build_batches(objects)
        all_objects = [obj for batch in batches for obj in batch]
        assert "AccountChangeEvent" not in all_objects
        assert "Account" in all_objects

    def test_build_catalog_entry_skips_objects_without_id(self):
        sf = self._make_sf_mock()
        service = DiscoveryService(sf)
        description = {
            "name": "NoIdObject",
            "fields": [{"name": "Name", "type": "string"}],
        }
        entry = service._build_catalog_entry(description, ["Id"])
        assert entry is None

    def test_build_catalog_entry_returns_valid_entry(self):
        sf = self._make_sf_mock()
        service = DiscoveryService(sf)
        description = {
            "name": "Account",
            "fields": [
                {"name": "Id", "type": "id"},
                {"name": "Name", "type": "string"},
                {"name": "SystemModstamp", "type": "datetime"},
            ],
        }
        entry = service._build_catalog_entry(description, ["Id"])
        assert entry is not None
        assert entry["stream"] == "Account"
        assert entry["tap_stream_id"] == "Account"
        assert "Id" in entry["schema"]["properties"]
        assert "Name" in entry["schema"]["properties"]

    def test_remove_unsupported_tag_objects(self):
        sf = self._make_sf_mock()
        service = DiscoveryService(sf)
        entries = [
            {"stream": "Account"},
            {"stream": "Account__Tag"},
            {"stream": "Lead"},
        ]
        custom_settings = ["Account"]
        tag_refs = {"Account": "Account__Tag"}
        result = service._remove_unsupported_tag_objects(entries, custom_settings, tag_refs)
        assert len(result) == 2
        assert all(e["stream"] != "Account__Tag" for e in result)

    @patch("sys.stdout")
    def test_discover_writes_json_to_stdout(self, mock_stdout):
        sf = self._make_sf_mock()
        sf.describe.return_value = {"sobjects": [{"name": "Account"}]}
        # Mock the batch describe call
        sf.describe.side_effect = [
            {"sobjects": [{"name": "Account"}]},  # first call (global)
            [  # second call (batch describe)
                {
                    "result": {
                        "name": "Account",
                        "fields": [
                            {"name": "Id", "type": "id"},
                            {"name": "SystemModstamp", "type": "datetime"},
                        ],
                    }
                }
            ],
        ]
        service = DiscoveryService(sf)
        service.discover([])
        # Verify json.dump was called (stdout.write is called)
        assert mock_stdout.write.called
