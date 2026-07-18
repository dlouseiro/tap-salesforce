"""Authentication credential parsing and Salesforce auth backends.

The tap requires an explicit ``auth_method`` in the config to select which
OAuth flow to use. Each method has its own required fields:

- ``browser``: ``client_id``, ``domain``
- ``client_credentials``: ``client_id``, ``client_secret``, ``domain``
- ``refresh_token`` (deprecated): ``client_id``, ``client_secret``, ``refresh_token``, ``domain``
- ``password`` (deprecated): ``username``, ``password``, ``security_token``, ``domain``
"""

from __future__ import annotations

import logging
import threading
import warnings
from collections import namedtuple
from pathlib import Path

import requests
from simple_salesforce import SalesforceLogin

from tap_salesforce.salesforce import browser_auth

LOGGER = logging.getLogger(__name__)


BrowserCredentials = namedtuple("BrowserCredentials", ("client_id", "domain"))

ClientCredentials = namedtuple("ClientCredentials", ("client_id", "client_secret", "domain"))

OAuthCredentials = namedtuple("OAuthCredentials", ("client_id", "client_secret", "refresh_token", "domain"))

PasswordCredentials = namedtuple("PasswordCredentials", ("username", "password", "security_token", "domain"))


AUTH_METHODS = {
    "browser": {"required": BrowserCredentials._fields, "cls": BrowserCredentials},
    "client_credentials": {"required": ClientCredentials._fields, "cls": ClientCredentials},
    "refresh_token": {"required": OAuthCredentials._fields, "cls": OAuthCredentials},
    "password": {"required": PasswordCredentials._fields, "cls": PasswordCredentials},
}

DEPRECATED_METHODS = {"refresh_token", "password"}


def parse_credentials(config: dict):
    """Parse credentials from config using the explicit ``auth_method`` key.

    Raises a clear error if ``auth_method`` is missing or invalid, or if
    required fields for the chosen method are not populated.
    """
    auth_method = config.get("auth_method")

    if not auth_method:
        raise ValueError(
            f"Config key 'auth_method' is required. Supported values: {', '.join(sorted(AUTH_METHODS.keys()))}"
        )

    if auth_method not in AUTH_METHODS:
        raise ValueError(
            f"Invalid auth_method '{auth_method}'. Supported values: {', '.join(sorted(AUTH_METHODS.keys()))}"
        )

    if auth_method in DEPRECATED_METHODS:
        warnings.warn(
            f"auth_method='{auth_method}' is deprecated and will be removed in a future version. "
            "Migrate to 'client_credentials' (for production/CI) or 'browser' (for local dev).",
            DeprecationWarning,
            stacklevel=2,
        )

    method_spec = AUTH_METHODS[auth_method]
    required_fields = method_spec["required"]
    cls = method_spec["cls"]

    values = {field: config.get(field) for field in required_fields}
    missing = [field for field, val in values.items() if not val]

    if missing:
        raise ValueError(
            f"auth_method='{auth_method}' requires the following config keys: "
            f"{', '.join(required_fields)}. Missing: {', '.join(missing)}"
        )

    return cls(**values)


def _derive_login_url(domain: str) -> str:
    """Derive the OAuth2 token endpoint URL from the Salesforce domain.

    For ECA-based flows (client_credentials, browser), the domain IS the
    My Domain (e.g. ``mycompany.my``) and the endpoint is
    ``https://{domain}.salesforce.com/services/oauth2/token``.

    For legacy flows (refresh_token, password), the domain serves the same
    purpose — ``login`` for production, ``test`` for sandbox, or a My Domain
    string for orgs that have disabled the generic login endpoints.
    """
    if domain in ("login", "test"):
        return f"https://{domain}.salesforce.com/services/oauth2/token"
    return f"https://{domain}.salesforce.com/services/oauth2/token"


class SalesforceAuth:
    """Base class for Salesforce authentication backends."""

    def __init__(self, credentials):
        self._credentials = credentials
        self._access_token = None
        self._instance_url = None
        self.login_timer = None

    def login(self):
        """Attempt to login and set the `instance_url` and `access_token` on success."""

    @property
    def rest_headers(self):
        return {"Authorization": f"Bearer {self._access_token}"}

    @property
    def bulk_headers(self):
        return {
            "X-SFDC-Session": self._access_token,
            "Content-Type": "application/json",
        }

    @property
    def instance_url(self):
        return self._instance_url

    @classmethod
    def from_credentials(cls, credentials, redirect_uri=None):
        """Dispatch to the auth class matching the given credentials shape."""
        if isinstance(credentials, BrowserCredentials):
            return SalesforceAuthBrowser(credentials, redirect_uri=redirect_uri)

        if isinstance(credentials, ClientCredentials):
            return SalesforceAuthClientCredentials(credentials)

        if isinstance(credentials, OAuthCredentials):
            return SalesforceAuthOAuth(credentials)

        if isinstance(credentials, PasswordCredentials):
            return SalesforceAuthPassword(credentials)

        raise ValueError(f"Unrecognized credentials type: {type(credentials)}")


class SalesforceAuthOAuth(SalesforceAuth):
    """OAuth 2.0 Refresh Token grant (deprecated)."""

    REFRESH_TOKEN_EXPIRATION_PERIOD = 900

    @property
    def _login_url(self):
        return _derive_login_url(self._credentials.domain)

    @property
    def _login_body(self):
        return {
            "grant_type": "refresh_token",
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
            "refresh_token": self._credentials.refresh_token,
        }

    def login(self):
        try:
            LOGGER.info("Attempting login via OAuth2 Refresh Token")

            resp = requests.post(
                self._login_url,
                data=self._login_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            resp.raise_for_status()
            auth = resp.json()

            LOGGER.info("OAuth2 Refresh Token login successful")
            self._access_token = auth["access_token"]
            self._instance_url = auth["instance_url"]
        except Exception as e:
            error_message = str(e)
            if resp:
                error_message = error_message + f", Response from Salesforce: {resp.text}"
            raise Exception(error_message) from e
        finally:
            LOGGER.info("Starting new login timer")
            self.login_timer = threading.Timer(self.REFRESH_TOKEN_EXPIRATION_PERIOD, self.login)
            self.login_timer.start()


class SalesforceAuthPassword(SalesforceAuth):
    """Legacy SOAP username/password/security_token grant (deprecated)."""

    def login(self):
        self._access_token, host = SalesforceLogin(
            domain=self._credentials.domain,
            username=self._credentials.username,
            password=self._credentials.password,
            security_token=self._credentials.security_token,
        )
        self._instance_url = "https://" + host


class SalesforceAuthClientCredentials(SalesforceAuth):
    """OAuth 2.0 Client Credentials grant (machine-to-machine)."""

    REFRESH_TOKEN_EXPIRATION_PERIOD = 900

    def login(self):
        try:
            LOGGER.info("Attempting login via OAuth2 Client Credentials")

            self._access_token, host = SalesforceLogin(
                consumer_key=self._credentials.client_id,
                consumer_secret=self._credentials.client_secret,
                domain=self._credentials.domain,
            )
            self._instance_url = "https://" + host

            LOGGER.info("OAuth2 Client Credentials login successful")
        finally:
            LOGGER.info("Starting new login timer")
            self.login_timer = threading.Timer(self.REFRESH_TOKEN_EXPIRATION_PERIOD, self.login)
            self.login_timer.start()


class SalesforceAuthBrowser(SalesforceAuth):
    """OAuth 2.0 Authorization Code Flow with PKCE (interactive browser login).

    Intended for local developer execution. The first run opens a browser
    for login; subsequent runs reuse the cached refresh token silently.
    """

    REFRESH_TOKEN_EXPIRATION_PERIOD = 900
    DEFAULT_CACHE_DIR = Path.home() / ".tap-salesforce"

    def __init__(self, credentials, redirect_uri=None, cache_dir=None):
        super().__init__(credentials)
        self._cache_dir = Path(cache_dir) if cache_dir else self.DEFAULT_CACHE_DIR
        self._redirect_uri = redirect_uri

    def login(self):
        try:
            LOGGER.info("Attempting login via OAuth2 Authorization Code + PKCE (browser)")

            token = browser_auth.acquire_token(
                client_id=self._credentials.client_id,
                domain=self._credentials.domain,
                cache_dir=self._cache_dir,
                redirect_uri=self._redirect_uri,
            )

            self._access_token = token.access_token
            self._instance_url = token.instance_url

            LOGGER.info("Browser OAuth login successful")
        finally:
            LOGGER.info("Starting new login timer")
            self.login_timer = threading.Timer(self.REFRESH_TOKEN_EXPIRATION_PERIOD, self.login)
            self.login_timer.start()
