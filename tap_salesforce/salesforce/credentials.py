import logging
import threading
from collections import namedtuple
from pathlib import Path

import requests
from simple_salesforce import SalesforceLogin

from tap_salesforce.salesforce import browser_auth

LOGGER = logging.getLogger(__name__)


OAuthCredentials = namedtuple("OAuthCredentials", ("client_id", "client_secret", "refresh_token"))

ClientCredentials = namedtuple("ClientCredentials", ("client_id", "client_secret", "domain"))

BrowserCredentials = namedtuple("BrowserCredentials", ("client_id", "domain"))

PasswordCredentials = namedtuple("PasswordCredentials", ("username", "password", "security_token"))


# Priority order (first fully-populated shape wins) — most specific first.
# ``OAuthCredentials`` and ``ClientCredentials`` both include ``client_id`` and
# ``client_secret``; ``refresh_token`` being present is the signal for the Refresh
# Token grant, so it must be checked before Client Credentials. ``BrowserCredentials``
# is a strict subset of ``ClientCredentials`` fields and therefore comes after it —
# ``client_secret`` being populated means "use Client Credentials", not Browser.
_CREDENTIAL_SHAPES = (
    OAuthCredentials,
    ClientCredentials,
    BrowserCredentials,
    PasswordCredentials,
)


def parse_credentials(config):
    # Explicit override: ``browser_auth=True`` short-circuits to the browser
    # flow even when ``client_secret`` is populated. Handy for local dev when
    # the same config file also carries the prod client_secret via Vault.
    if config.get("browser_auth") is True:
        browser = BrowserCredentials(*(config.get(key) for key in BrowserCredentials._fields))
        if all(browser):
            return browser
        raise Exception("browser_auth=True requires 'client_id' and 'domain' to be set.")

    for cls in _CREDENTIAL_SHAPES:
        creds = cls(*(config.get(key) for key in cls._fields))
        if all(creds):
            return creds

    raise Exception("Cannot create credentials from config.")


class SalesforceAuth:
    def __init__(self, credentials, is_sandbox=False):
        self.is_sandbox = is_sandbox
        self._credentials = credentials
        self._access_token = None
        self._instance_url = None
        self._auth_header = None
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
    def from_credentials(cls, credentials, is_sandbox=False, cache_dir=None, redirect_uri=None):
        """Dispatch to the auth class matching the given credentials shape.

        ``cache_dir`` and ``redirect_uri`` are only meaningful for
        :class:`SalesforceAuthBrowser` — they're accepted here (rather than
        via a blind ``**kwargs`` passthrough) so that passing them for any
        other credential shape is a no-op instead of a ``TypeError``.
        """
        if isinstance(credentials, OAuthCredentials):
            return SalesforceAuthOAuth(credentials, is_sandbox=is_sandbox)

        if isinstance(credentials, ClientCredentials):
            return SalesforceAuthClientCredentials(credentials, is_sandbox=is_sandbox)

        if isinstance(credentials, BrowserCredentials):
            return SalesforceAuthBrowser(
                credentials,
                is_sandbox=is_sandbox,
                cache_dir=cache_dir,
                redirect_uri=redirect_uri,
            )

        if isinstance(credentials, PasswordCredentials):
            return SalesforceAuthPassword(credentials, is_sandbox=is_sandbox)

        raise Exception("Invalid credentials")


class SalesforceAuthOAuth(SalesforceAuth):
    # The minimum expiration setting for SF Refresh Tokens is 15 minutes
    REFRESH_TOKEN_EXPIRATION_PERIOD = 900

    @property
    def _login_body(self):
        return {"grant_type": "refresh_token", **self._credentials._asdict()}

    @property
    def _login_url(self):
        login_url = "https://login.salesforce.com/services/oauth2/token"

        if self.is_sandbox:
            login_url = "https://test.salesforce.com/services/oauth2/token"

        return login_url

    def login(self):
        try:
            LOGGER.info("Attempting login via OAuth2")

            resp = requests.post(
                self._login_url,
                data=self._login_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            resp.raise_for_status()
            auth = resp.json()

            LOGGER.info("OAuth2 login successful")
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
    def login(self):
        # ``simple-salesforce`` >=1.0 replaced the ``sandbox`` kwarg with ``domain``:
        # ``"test"`` targets the sandbox login endpoint, ``"login"`` targets production.
        login = SalesforceLogin(
            domain="test" if self.is_sandbox else "login",
            **self._credentials._asdict(),
        )

        self._access_token, host = login
        self._instance_url = "https://" + host


class SalesforceAuthClientCredentials(SalesforceAuth):
    """OAuth 2.0 Client Credentials grant.

    Machine-to-machine authentication. Credentials are the External Client App's
    ``consumer_key`` (``client_id``) and ``consumer_secret`` (``client_secret``);
    identity is the app's configured "Run As" user. Requires a Salesforce My
    Domain (``login``/``test`` are not accepted by Salesforce for this grant).

    The Salesforce access token is short-lived, so we re-login periodically to
    keep long-running syncs healthy — matching the pattern used by
    ``SalesforceAuthOAuth``.
    """

    REFRESH_TOKEN_EXPIRATION_PERIOD = 900

    def login(self):
        try:
            LOGGER.info("Attempting login via OAuth2 Client Credentials")

            # ``simple-salesforce.SalesforceLogin`` handles the token endpoint
            # construction, HTTP Basic authorisation header, and response parsing
            # for this grant. Returns ``(access_token, sf_instance_host)``.
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

    Intended for local developer execution where each dev acts as their own
    Salesforce user. Cron / production should use
    :class:`SalesforceAuthClientCredentials` (or the legacy
    :class:`SalesforceAuthOAuth` for pre-obtained refresh tokens).

    The first run opens a browser and caches the returned refresh token —
    in the OS keychain if the optional ``keyring`` extra
    (``pip install tap-salesforce[browser]``) is installed and working,
    otherwise in a plain file at ``~/.tap-salesforce/<domain>/<client_id>.json``
    (mode ``0600``). Subsequent runs — including headless invocations on the
    same machine — swap the cached refresh token for a fresh access token
    silently. If the refresh token is rejected (e.g. revoked by admin), the
    browser step is retried.

    Access tokens are short-lived, so we re-login periodically on the same
    900s cadence as :class:`SalesforceAuthOAuth`. This uses the cached refresh
    token silently and never re-opens the browser during normal operation.

    ``redirect_uri`` is optional. If omitted, an ephemeral loopback port is
    chosen at runtime (fine for a self-authorizing External Client App with
    no fixed callback port). If provided and it includes a port, that exact
    address is used — required when the App's registered callback URL pins
    a specific port. If provided without a port, the given host/path is kept
    and a port is still chosen dynamically and appended.
    """

    REFRESH_TOKEN_EXPIRATION_PERIOD = 900
    DEFAULT_CACHE_DIR = Path.home() / ".tap-salesforce"

    def __init__(self, credentials, is_sandbox=False, cache_dir=None, redirect_uri=None):
        super().__init__(credentials, is_sandbox=is_sandbox)
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
