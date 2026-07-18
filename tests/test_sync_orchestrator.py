"""Tests for tap_salesforce.sync_orchestrator."""

from tap_salesforce.sync_orchestrator import (
    build_state,
    is_object_type,
    is_property_selected,
    pop_deselected_schema,
    stream_is_selected,
)


class TestStreamIsSelected:
    def test_returns_true_when_selected(self):
        mdata = {(): {"selected": True}}
        assert stream_is_selected(mdata) is True

    def test_returns_false_when_not_selected(self):
        mdata = {(): {"selected": False}}
        assert stream_is_selected(mdata) is False

    def test_returns_false_when_metadata_empty(self):
        assert stream_is_selected({}) is False


class TestIsObjectType:
    def test_object_type_detected(self):
        schema = {"type": "object"}
        assert is_object_type(schema) is True

    def test_non_object_type(self):
        schema = {"type": "string"}
        assert is_object_type(schema) is False

    def test_object_in_list_type(self):
        schema = {"type": ["null", "object"]}
        assert is_object_type(schema) is True

    def test_object_in_anyof(self):
        # anyOf with a plain "object" string entry (as used in actual schemas)
        schema = {"anyOf": ["object", "string"]}
        assert is_object_type(schema) is True

    def test_anyof_with_dict_entries_does_not_detect(self):
        # When anyOf contains dicts, the "in" check looks at dict keys
        # This is existing behavior preserved from the original code
        schema = {"anyOf": [{"type": "object"}, {"type": "string"}]}
        assert is_object_type(schema) is False

    def test_returns_none_when_no_type_info(self):
        schema = {"description": "something"}
        assert is_object_type(schema) is None


class TestIsPropertySelected:
    def test_automatic_inclusion_always_selected(self):
        mdata = {(): {"selected": True}, ("properties", "Id"): {"inclusion": "automatic"}}
        assert is_property_selected("Account", mdata, ("properties", "Id")) is True

    def test_unsupported_inclusion_never_selected(self):
        mdata = {(): {"selected": True}, ("properties", "BinaryField"): {"inclusion": "unsupported"}}
        assert is_property_selected("Account", mdata, ("properties", "BinaryField")) is False

    def test_explicit_selected_true(self):
        mdata = {(): {"selected": True}, ("properties", "Name"): {"selected": True}}
        assert is_property_selected("Account", mdata, ("properties", "Name")) is True

    def test_explicit_selected_false(self):
        mdata = {(): {"selected": True}, ("properties", "Name"): {"selected": False}}
        assert is_property_selected("Account", mdata, ("properties", "Name")) is False

    def test_selected_by_default(self):
        mdata = {(): {"selected": True}, ("properties", "Name"): {"selected-by-default": True}}
        assert is_property_selected("Account", mdata, ("properties", "Name")) is True

    def test_parent_false_overrides_child(self):
        # When parent (root) is not selected, child inherits False
        mdata = {(): {"selected": False}, ("properties", "Name"): {"selected": True}}
        assert is_property_selected("Account", mdata, ("properties", "Name")) is False

    def test_empty_metadata_returns_false(self):
        # With no metadata entries, parent resolves to False which propagates down
        assert is_property_selected("Account", {}, ("properties", "Name")) is False

    def test_none_breadcrumb_uses_root(self):
        mdata = {(): {"selected": True}}
        assert is_property_selected("Account", mdata, None) is True


class TestPopDeselectedSchema:
    def test_removes_unselected_properties(self):
        schema = {
            "properties": {
                "Id": {"type": "string"},
                "Name": {"type": "string"},
                "Secret": {"type": "string"},
            }
        }
        mdata = {
            (): {"selected": True},
            ("properties", "Id"): {"inclusion": "automatic"},
            ("properties", "Name"): {"selected": True},
            ("properties", "Secret"): {"inclusion": "unsupported"},
        }
        pop_deselected_schema(schema, "Account", (), mdata)
        assert "Id" in schema["properties"]
        assert "Name" in schema["properties"]
        assert "Secret" not in schema["properties"]

    def test_preserves_all_when_all_selected(self):
        schema = {
            "properties": {
                "Id": {"type": "string"},
                "Name": {"type": "string"},
            }
        }
        mdata = {
            (): {"selected": True},
            ("properties", "Id"): {"inclusion": "automatic"},
            ("properties", "Name"): {"selected": True},
        }
        pop_deselected_schema(schema, "Account", (), mdata)
        assert len(schema["properties"]) == 2


class TestBuildState:
    def _make_catalog(self, replication_method="INCREMENTAL", replication_key="SystemModstamp"):
        metadata_list = [{"breadcrumb": [], "metadata": {"replication-method": replication_method}}]
        if replication_key:
            metadata_list[0]["metadata"]["replication-key"] = replication_key
        return {
            "streams": [
                {
                    "tap_stream_id": "Account",
                    "stream": "Account",
                    "metadata": metadata_list,
                    "schema": {},
                }
            ]
        }

    def test_preserves_incremental_bookmark(self):
        raw_state = {"bookmarks": {"Account": {"SystemModstamp": "2024-01-01T00:00:00Z"}}}
        catalog = self._make_catalog()
        state = build_state(raw_state, catalog)
        assert state["bookmarks"]["Account"]["SystemModstamp"] == "2024-01-01T00:00:00Z"

    def test_preserves_bulk_job_state(self):
        raw_state = {
            "bookmarks": {
                "Account": {
                    "JobID": "job123",
                    "BatchIDs": ["batch1", "batch2"],
                    "JobHighestBookmarkSeen": "2024-01-01T00:00:00Z",
                }
            }
        }
        catalog = self._make_catalog()
        state = build_state(raw_state, catalog)
        assert state["bookmarks"]["Account"]["JobID"] == "job123"
        assert state["bookmarks"]["Account"]["BatchIDs"] == ["batch1", "batch2"]

    def test_full_table_with_no_version_sets_none(self):
        raw_state = {"bookmarks": {}}
        catalog = self._make_catalog(replication_method="FULL_TABLE", replication_key=None)
        state = build_state(raw_state, catalog)
        assert state["bookmarks"]["Account"]["version"] is None

    def test_empty_state_returns_empty(self):
        raw_state = {}
        catalog = self._make_catalog()
        state = build_state(raw_state, catalog)
        # No bookmark in raw_state means nothing to preserve
        assert "Account" not in state.get("bookmarks", {})
