"""Tests for tap_salesforce.config."""

from tap_salesforce.config import (
    DEFAULT_CONFIG,
    FORCED_FULL_TABLE,
    REQUIRED_CONFIG_KEYS,
)


class TestConfig:
    def test_required_config_keys(self):
        assert "api_type" in REQUIRED_CONFIG_KEYS
        assert "select_fields_by_default" in REQUIRED_CONFIG_KEYS

    def test_forced_full_table_contains_known_objects(self):
        assert "BackgroundOperationResult" in FORCED_FULL_TABLE

    def test_default_config_has_expected_keys(self):
        assert "refresh_token" in DEFAULT_CONFIG
        assert "client_id" in DEFAULT_CONFIG
        assert "client_secret" in DEFAULT_CONFIG
        assert "domain" in DEFAULT_CONFIG
        assert "start_date" in DEFAULT_CONFIG
        assert "soql_filters" in DEFAULT_CONFIG

    def test_default_config_values_are_none(self):
        for value in DEFAULT_CONFIG.values():
            assert value is None
