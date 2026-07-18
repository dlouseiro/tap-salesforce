"""Tap configuration parsing and validation."""

from __future__ import annotations

REQUIRED_CONFIG_KEYS = ["auth_method", "domain", "api_type", "select_fields_by_default"]

FORCED_FULL_TABLE = {
    "BackgroundOperationResult",  # Does not support ordering by CreatedDate
}

DEFAULT_CONFIG = {
    "auth_method": None,
    "domain": None,
    "client_id": None,
    "client_secret": None,
    "refresh_token": None,
    "redirect_uri": None,
    "start_date": None,
    "soql_filters": None,
}
