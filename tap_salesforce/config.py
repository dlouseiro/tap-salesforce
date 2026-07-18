"""Tap configuration parsing and validation."""

from __future__ import annotations

from tap_salesforce.salesforce.credentials import (
    BrowserCredentials,
    ClientCredentials,
    OAuthCredentials,
    PasswordCredentials,
)

REQUIRED_CONFIG_KEYS = ["api_type", "select_fields_by_default"]

OAUTH_CONFIG_KEYS = OAuthCredentials._fields
CLIENT_CREDENTIALS_CONFIG_KEYS = ClientCredentials._fields
BROWSER_CONFIG_KEYS = BrowserCredentials._fields
PASSWORD_CONFIG_KEYS = PasswordCredentials._fields

FORCED_FULL_TABLE = {
    "BackgroundOperationResult",  # Does not support ordering by CreatedDate
}

DEFAULT_CONFIG = {
    "refresh_token": None,
    "client_id": None,
    "client_secret": None,
    "domain": None,
    "browser_auth": None,
    "redirect_uri": None,
    "start_date": None,
    "soql_filters": None,
}
