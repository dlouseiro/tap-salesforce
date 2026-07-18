"""Tests for tap_salesforce.salesforce.credentials."""

import warnings

import pytest

from tap_salesforce.salesforce.credentials import (
    BrowserCredentials,
    ClientCredentials,
    OAuthCredentials,
    PasswordCredentials,
    SalesforceAuth,
    SalesforceAuthBrowser,
    SalesforceAuthClientCredentials,
    SalesforceAuthOAuth,
    SalesforceAuthPassword,
    parse_credentials,
)


class TestParseCredentials:
    def test_browser_method(self):
        config = {"auth_method": "browser", "client_id": "ci", "domain": "mycompany.my"}
        creds = parse_credentials(config)
        assert isinstance(creds, BrowserCredentials)
        assert creds.client_id == "ci"
        assert creds.domain == "mycompany.my"

    def test_client_credentials_method(self):
        config = {
            "auth_method": "client_credentials",
            "client_id": "ci",
            "client_secret": "cs",
            "domain": "mycompany.my",
        }
        creds = parse_credentials(config)
        assert isinstance(creds, ClientCredentials)

    def test_refresh_token_method(self):
        config = {
            "auth_method": "refresh_token",
            "client_id": "ci",
            "client_secret": "cs",
            "refresh_token": "rt",
            "domain": "mycompany.my",
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            creds = parse_credentials(config)
            assert isinstance(creds, OAuthCredentials)
            assert len(w) == 1
            assert "deprecated" in str(w[0].message).lower()

    def test_password_method(self):
        config = {
            "auth_method": "password",
            "username": "u",
            "password": "p",
            "security_token": "t",
            "domain": "test",
        }
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            creds = parse_credentials(config)
            assert isinstance(creds, PasswordCredentials)
            assert len(w) == 1
            assert "deprecated" in str(w[0].message).lower()

    def test_missing_auth_method_raises(self):
        with pytest.raises(ValueError, match=r"auth_method.*required"):
            parse_credentials({"client_id": "ci", "domain": "x"})

    def test_invalid_auth_method_raises(self):
        with pytest.raises(ValueError, match="Invalid auth_method"):
            parse_credentials({"auth_method": "magic", "domain": "x"})

    def test_missing_required_fields_raises_with_details(self):
        config = {"auth_method": "browser", "client_id": "ci"}  # missing domain
        with pytest.raises(ValueError, match="Missing: domain"):
            parse_credentials(config)

    def test_missing_multiple_fields_lists_all(self):
        config = {"auth_method": "client_credentials", "domain": "x"}
        with pytest.raises(ValueError, match=r"client_id.*client_secret|client_secret.*client_id"):
            parse_credentials(config)

    def test_extra_config_keys_are_ignored(self):
        config = {
            "auth_method": "browser",
            "client_id": "ci",
            "domain": "mycompany.my",
            "extra_key": "ignored",
            "client_secret": "also_ignored",
        }
        creds = parse_credentials(config)
        assert isinstance(creds, BrowserCredentials)

    def test_refresh_token_requires_domain(self):
        config = {
            "auth_method": "refresh_token",
            "client_id": "ci",
            "client_secret": "cs",
            "refresh_token": "rt",
        }
        with pytest.raises(ValueError, match="Missing: domain"):
            parse_credentials(config)


class TestFromCredentialsDispatch:
    def test_dispatches_browser(self):
        auth = SalesforceAuth.from_credentials(BrowserCredentials(client_id="ci", domain="mycompany.my"))
        assert isinstance(auth, SalesforceAuthBrowser)

    def test_dispatches_client_credentials(self):
        auth = SalesforceAuth.from_credentials(
            ClientCredentials(client_id="ci", client_secret="cs", domain="mycompany.my")
        )
        assert isinstance(auth, SalesforceAuthClientCredentials)

    def test_dispatches_oauth(self):
        auth = SalesforceAuth.from_credentials(
            OAuthCredentials(client_id="ci", client_secret="cs", refresh_token="rt", domain="mycompany.my")
        )
        assert isinstance(auth, SalesforceAuthOAuth)

    def test_dispatches_password(self):
        auth = SalesforceAuth.from_credentials(
            PasswordCredentials(username="u", password="p", security_token="t", domain="test")
        )
        assert isinstance(auth, SalesforceAuthPassword)

    def test_browser_receives_redirect_uri(self):
        auth = SalesforceAuth.from_credentials(
            BrowserCredentials(client_id="ci", domain="mycompany.my"),
            redirect_uri="http://localhost:1717/callback",
        )
        assert auth._redirect_uri == "http://localhost:1717/callback"

    def test_redirect_uri_ignored_for_non_browser(self):
        auth = SalesforceAuth.from_credentials(
            ClientCredentials(client_id="ci", client_secret="cs", domain="mycompany.my"),
            redirect_uri="http://localhost:1717/callback",
        )
        assert isinstance(auth, SalesforceAuthClientCredentials)
