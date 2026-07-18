"""Tests for ``tap_salesforce.salesforce.credentials``.

Unittest-style so they run under both ``nose`` (CI, per ``.circleci/config.yml``)
and ``pytest``. No Salesforce network calls — dispatch logic is exercised
against ``parse_credentials`` and ``SalesforceAuth.from_credentials`` only.
"""

import unittest

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


class ParseCredentialsTests(unittest.TestCase):
    def test_password_shape(self):
        config = {
            "username": "u",
            "password": "p",
            "security_token": "t",
        }
        self.assertIsInstance(parse_credentials(config), PasswordCredentials)

    def test_oauth_refresh_token_shape(self):
        config = {
            "client_id": "ci",
            "client_secret": "cs",
            "refresh_token": "rt",
        }
        self.assertIsInstance(parse_credentials(config), OAuthCredentials)

    def test_client_credentials_shape(self):
        config = {
            "client_id": "ci",
            "client_secret": "cs",
            "domain": "picnic-nl.my",
        }
        self.assertIsInstance(parse_credentials(config), ClientCredentials)

    def test_browser_shape_inferred_when_no_secret(self):
        config = {
            "client_id": "ci",
            "domain": "picnic-nl.my",
        }
        self.assertIsInstance(parse_credentials(config), BrowserCredentials)

    def test_browser_auth_true_forces_browser_over_client_credentials(self):
        config = {
            "client_id": "ci",
            "client_secret": "cs",  # would otherwise dispatch to Client Credentials
            "domain": "picnic-nl.my",
            "browser_auth": True,
        }
        self.assertIsInstance(parse_credentials(config), BrowserCredentials)

    def test_browser_auth_true_without_required_fields_raises(self):
        config = {"browser_auth": True, "client_id": "ci"}  # missing domain
        # parse_credentials raises a bare ``Exception`` by design; keep that
        # contract, but tighten the assertion via a message match to make the
        # test explicit about what it's checking.
        with self.assertRaisesRegex(Exception, "browser_auth=True requires"):
            parse_credentials(config)

    def test_refresh_token_wins_over_client_credentials(self):
        # ``refresh_token`` populated → OAuth path takes precedence even when
        # ``domain`` is also present. Prevents accidentally re-authenticating
        # as the ECA "Run As" user when the config also carries a personal
        # refresh token.
        config = {
            "client_id": "ci",
            "client_secret": "cs",
            "refresh_token": "rt",
            "domain": "picnic-nl.my",
        }
        self.assertIsInstance(parse_credentials(config), OAuthCredentials)

    def test_empty_config_raises(self):
        with self.assertRaisesRegex(Exception, "Cannot create credentials"):
            parse_credentials({})


class FromCredentialsDispatchTests(unittest.TestCase):
    """``SalesforceAuth.from_credentials`` should map each namedtuple to its class."""

    def test_dispatches_oauth(self):
        auth = SalesforceAuth.from_credentials(OAuthCredentials(client_id="ci", client_secret="cs", refresh_token="rt"))
        self.assertIsInstance(auth, SalesforceAuthOAuth)

    def test_dispatches_client_credentials(self):
        auth = SalesforceAuth.from_credentials(
            ClientCredentials(client_id="ci", client_secret="cs", domain="picnic-nl.my")
        )
        self.assertIsInstance(auth, SalesforceAuthClientCredentials)

    def test_dispatches_browser(self):
        auth = SalesforceAuth.from_credentials(BrowserCredentials(client_id="ci", domain="picnic-nl.my"))
        self.assertIsInstance(auth, SalesforceAuthBrowser)

    def test_dispatches_browser_forwards_redirect_uri(self):
        # redirect_uri is only meaningful for the Browser slot; from_credentials
        # should forward it there without erroring for the other credential shapes.
        auth = SalesforceAuth.from_credentials(
            BrowserCredentials(client_id="ci", domain="picnic-nl.my"),
            redirect_uri="http://localhost:1717/callback",
        )
        self.assertEqual(auth._redirect_uri, "http://localhost:1717/callback")

    def test_redirect_uri_is_a_no_op_for_non_browser_credentials(self):
        # Passing redirect_uri alongside any other credential shape must not
        # raise — Client Credentials/OAuth/Password simply ignore it.
        auth = SalesforceAuth.from_credentials(
            ClientCredentials(client_id="ci", client_secret="cs", domain="picnic-nl.my"),
            redirect_uri="http://localhost:1717/callback",
        )
        self.assertIsInstance(auth, SalesforceAuthClientCredentials)

    def test_dispatches_password(self):
        auth = SalesforceAuth.from_credentials(PasswordCredentials(username="u", password="p", security_token="t"))
        self.assertIsInstance(auth, SalesforceAuthPassword)


if __name__ == "__main__":
    unittest.main()
