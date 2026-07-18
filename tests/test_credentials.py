"""Tests for ``tap_salesforce.salesforce.credentials``.

Unittest-style so they run under both ``nose`` (CI, per ``.circleci/config.yml``)
and ``pytest``. No Salesforce network calls — dispatch logic is exercised
against ``parse_credentials`` and ``SalesforceAuth.from_credentials`` only.
"""

import unittest

from tap_salesforce.salesforce.credentials import (
    ClientCredentials,
    OAuthCredentials,
    PasswordCredentials,
    SalesforceAuth,
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
        auth = SalesforceAuth.from_credentials(
            OAuthCredentials(client_id="ci", client_secret="cs", refresh_token="rt")
        )
        self.assertIsInstance(auth, SalesforceAuthOAuth)

    def test_dispatches_client_credentials(self):
        auth = SalesforceAuth.from_credentials(
            ClientCredentials(client_id="ci", client_secret="cs", domain="picnic-nl.my")
        )
        self.assertIsInstance(auth, SalesforceAuthClientCredentials)

    def test_dispatches_password(self):
        auth = SalesforceAuth.from_credentials(
            PasswordCredentials(username="u", password="p", security_token="t")
        )
        self.assertIsInstance(auth, SalesforceAuthPassword)


if __name__ == "__main__":
    unittest.main()
