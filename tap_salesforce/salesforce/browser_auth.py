"""OAuth 2.0 Authorization Code Flow with PKCE for local Salesforce auth.

This module implements the interactive half of a Salesforce OAuth flow:

1. Open a browser to Salesforce's ``/services/oauth2/authorize`` endpoint.
2. Listen on a loopback port for the redirect — by default an ephemeral
   port is chosen at runtime, but a caller can pin a fixed ``redirect_uri``
   (port and/or host) to match a statically-registered callback URL.
3. Exchange the authorization code (PKCE-protected) at
   ``/services/oauth2/token`` for an access + refresh token pair.
4. Cache the refresh token via :mod:`tap_salesforce.salesforce.token_cache`
   (OS keychain when available, plain file otherwise) so subsequent runs
   (including headless ones on the same laptop) can silently swap the
   refresh token for a fresh access token without re-opening the browser.

The implementation deliberately uses only the Python standard library plus
``requests`` (already a hard dependency of the tap) — matching the
``simple-salesforce`` project's posture so the code stays liftable upstream.
See the companion RFC for the wider design context.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import logging
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from tap_salesforce.salesforce.token_cache import (
    load_refresh_token,
    store_refresh_token,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_SCOPES: Sequence[str] = ("api", "refresh_token")
DEFAULT_TIMEOUT_SECONDS = 120


@dataclass
class AcquiredToken:
    """Tokens returned by :func:`acquire_token`."""

    access_token: str
    instance_url: str
    refresh_token: str | None = None


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate an RFC 7636 PKCE verifier/challenge pair using ``S256``."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    challenge_bytes = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode()
    return verifier, challenge


def _pick_free_port() -> int:
    """Return an unused TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolve_redirect_uri(redirect_uri: str | None) -> tuple[str, int]:
    """Resolve the redirect URI to send to Salesforce, and the local port to bind.

    - If ``redirect_uri`` is ``None``, defaults to ``http://localhost/callback``
      with a freshly chosen ephemeral port.
    - If ``redirect_uri`` already specifies a port (e.g. because the
      External Client App's callback URL is registered with a fixed port —
      common when the port itself can't be made dynamic on the Salesforce
      side), that literal address is used unchanged.
    - If ``redirect_uri`` has no port (e.g. a bare ``http://localhost/callback``,
      or a proxied hostname with no port such as in some remote dev
      environments), an ephemeral port is chosen and appended.

    In both cases the local HTTP listener always binds on ``127.0.0.1`` —
    this matches how ``localhost`` (and proxied hostnames that ultimately
    forward back to the developer's machine) resolve.
    """
    parsed = urllib.parse.urlsplit(redirect_uri or "http://localhost/callback")
    if parsed.port is not None:
        return redirect_uri, parsed.port  # type: ignore[return-value]

    port = _pick_free_port()
    host = parsed.hostname or "localhost"
    resolved = urllib.parse.urlunsplit((parsed.scheme or "http", f"{host}:{port}", parsed.path or "/callback", "", ""))
    return resolved, port


def _token_endpoint(domain: str) -> str:
    # ``domain`` is a Salesforce My Domain string such as ``"picnic-nl.my"`` —
    # the ``.my`` suffix is already part of the domain, so we only append
    # ``.salesforce.com``. This matches ``simple-salesforce``'s URL construction
    # for the Client Credentials grant.
    return f"https://{domain}.salesforce.com/services/oauth2/token"


def _authorize_endpoint(domain: str) -> str:
    return f"https://{domain}.salesforce.com/services/oauth2/authorize"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot handler that captures the OAuth redirect query string.

    Browsers commonly issue a follow-up request to the just-loaded origin
    (e.g. an automatic ``/favicon.ico`` fetch) right after the real OAuth
    redirect lands. Since ``http.server.HTTPServer`` handles one request at
    a time, that follow-up request could arrive before the polling loop in
    ``_run_browser_flow`` reads ``server.oauth_result`` — silently
    overwriting the real ``code``/``state`` with an empty result and
    producing a false "state mismatch" failure. To avoid that, only a
    request that actually carries ``code`` or ``error`` is treated as the
    OAuth callback; anything else is answered with a bare 404 and ignored.
    """

    def do_GET(self):  # http.server contract mandates this method name
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        # ``parse_qs`` returns list values; we only ever expect one of each.
        result = {k: v[0] for k, v in params.items()}

        if "code" not in result and "error" not in result:
            self.send_response(404)
            self.end_headers()
            return

        self.server.oauth_result = result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in result:
            body = (
                b"<html><body style='font-family:sans-serif;padding:2em'>"
                b"<h1>Login successful</h1>"
                b"<p>You may close this tab.</p>"
                b"</body></html>"
            )
        else:
            body = (
                b"<html><body style='font-family:sans-serif;padding:2em'>"
                b"<h1>Login failed</h1>"
                b"<p>Check the tap-salesforce logs for details.</p>"
                b"</body></html>"
            )
        self.wfile.write(body)

    def log_message(self, format, *args):  # http.server signature is mandated
        return  # suppress default HTTP server access logs


def _run_browser_flow(
    client_id: str,
    domain: str,
    scopes: Sequence[str],
    redirect_uri: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Drive the interactive PKCE flow and return the token endpoint response."""
    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri, port = _resolve_redirect_uri(redirect_uri)

    authorize_url = (
        _authorize_endpoint(domain)
        + "?"
        + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.oauth_result = None  # type: ignore[attr-defined]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    LOGGER.info("Opening browser to complete Salesforce OAuth (listening on %s)", redirect_uri)
    try:
        webbrowser.open(authorize_url)
        deadline = time.time() + timeout_seconds
        while server.oauth_result is None and time.time() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.2)
    finally:
        server.shutdown()
        server_thread.join(timeout=5)

    result = server.oauth_result  # type: ignore[attr-defined]
    if result is None:
        raise TimeoutError(f"Did not receive Salesforce OAuth callback within {timeout_seconds}s")
    if "error" in result:
        raise RuntimeError(
            f"Salesforce OAuth error: {result['error']} ({result.get('error_description', 'no description')})"
        )
    if result.get("state") != state:
        raise RuntimeError("Salesforce OAuth state mismatch — possible CSRF")

    resp = requests.post(
        _token_endpoint(domain),
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def _exchange_refresh_token(client_id: str, refresh_token: str, domain: str) -> dict[str, Any]:
    """Exchange a stored refresh token for a fresh access token."""
    resp = requests.post(
        _token_endpoint(domain),
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def acquire_token(
    client_id: str,
    domain: str,
    cache_dir: Path,
    scopes: Sequence[str] = DEFAULT_SCOPES,
    redirect_uri: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> AcquiredToken:
    """Acquire a Salesforce access token using PKCE + refresh-token caching.

    Behaviour:

    1. If a refresh token is cached for ``(domain, client_id)``, silently
       exchange it for a fresh access token and return. If the refresh grant
       response includes a new refresh token (some External Client App
       policies rotate it on every use), the cache is updated with it.
    2. Otherwise — or if the cached refresh token is rejected by Salesforce —
       open the user's browser, complete the Authorization Code Flow with
       PKCE, cache the new refresh token, and return the access token.

    Args:
        redirect_uri: The callback URL to send to Salesforce and to bind the
            local listener to. Defaults to ``http://localhost/callback`` with
            an ephemeral port chosen at runtime. If a port is included (e.g.
            ``http://localhost:1717/callback``), that literal address is used
            — required when the External Client App's registered callback
            URL pins a specific port rather than accepting any loopback
            port. If no port is included, one is still chosen dynamically
            and appended, preserving the given host/path (useful for
            environments where the callback is proxied through a fixed
            hostname but the port itself may vary).
    """
    cached_refresh_token = load_refresh_token(cache_dir, domain, client_id)
    if cached_refresh_token is not None:
        try:
            body = _exchange_refresh_token(client_id, cached_refresh_token, domain)
            # Some External Client App refresh token policies rotate the
            # refresh token on every use. If Salesforce returned a new one,
            # persist it -- otherwise the cache would silently go stale after
            # a single reuse, forcing a full browser round-trip next time.
            rotated_refresh_token = body.get("refresh_token")
            if rotated_refresh_token and rotated_refresh_token != cached_refresh_token:
                store_refresh_token(cache_dir, domain, client_id, rotated_refresh_token)
            return AcquiredToken(
                access_token=body["access_token"],
                instance_url=body["instance_url"],
                refresh_token=rotated_refresh_token or cached_refresh_token,
            )
        except requests.HTTPError as e:
            LOGGER.warning("Cached refresh token rejected (%s); falling back to browser flow", e)

    body = _run_browser_flow(client_id, domain, scopes, redirect_uri, timeout_seconds)
    refresh_token = body.get("refresh_token")
    if refresh_token:
        store_refresh_token(cache_dir, domain, client_id, refresh_token)
    return AcquiredToken(
        access_token=body["access_token"],
        instance_url=body["instance_url"],
        refresh_token=refresh_token,
    )
