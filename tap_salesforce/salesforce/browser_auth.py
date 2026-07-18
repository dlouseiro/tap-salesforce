"""OAuth 2.0 Authorization Code Flow with PKCE for local Salesforce auth.

This module implements the interactive half of a Salesforce OAuth flow:

1. Open a browser to Salesforce's ``/services/oauth2/authorize`` endpoint.
2. Listen on ``http://localhost:<port>`` for the redirect.
3. Exchange the authorization code (PKCE-protected) at
   ``/services/oauth2/token`` for an access + refresh token pair.
4. Cache the refresh token via :mod:`tap_salesforce.salesforce.token_cache` so
   subsequent runs (including headless ones on the same laptop) can silently
   swap the refresh token for a fresh access token without re-opening the
   browser.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

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


def _token_endpoint(domain: str) -> str:
    # ``domain`` is a Salesforce My Domain string such as ``"picnic-nl.my"`` —
    # the ``.my`` suffix is already part of the domain, so we only append
    # ``.salesforce.com``. This matches ``simple-salesforce``'s URL construction
    # for the Client Credentials grant.
    return f"https://{domain}.salesforce.com/services/oauth2/token"


def _authorize_endpoint(domain: str) -> str:
    return f"https://{domain}.salesforce.com/services/oauth2/authorize"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot handler that captures the OAuth redirect query string."""

    def do_GET(self):  # http.server contract mandates this method name
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        # ``parse_qs`` returns list values; we only ever expect one of each.
        self.server.oauth_result = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in self.server.oauth_result:
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
    redirect_port: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Drive the interactive PKCE flow and return the token endpoint response."""
    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    port = redirect_port or _pick_free_port()
    redirect_uri = f"http://localhost:{port}/callback"

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
    redirect_port: int = 0,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> AcquiredToken:
    """Acquire a Salesforce access token using PKCE + refresh-token caching.

    Behaviour:

    1. If a refresh token is cached for ``(domain, client_id)``, silently
       exchange it for a fresh access token and return.
    2. Otherwise — or if the cached refresh token is rejected by Salesforce —
       open the user's browser, complete the Authorization Code Flow with
       PKCE, cache the new refresh token, and return the access token.
    """
    cached_refresh_token = load_refresh_token(cache_dir, domain, client_id)
    if cached_refresh_token is not None:
        try:
            body = _exchange_refresh_token(client_id, cached_refresh_token, domain)
            return AcquiredToken(
                access_token=body["access_token"],
                instance_url=body["instance_url"],
                refresh_token=cached_refresh_token,
            )
        except requests.HTTPError as e:
            LOGGER.warning("Cached refresh token rejected (%s); falling back to browser flow", e)

    body = _run_browser_flow(client_id, domain, scopes, redirect_port, timeout_seconds)
    refresh_token = body.get("refresh_token")
    if refresh_token:
        store_refresh_token(cache_dir, domain, client_id, refresh_token)
    return AcquiredToken(
        access_token=body["access_token"],
        instance_url=body["instance_url"],
        refresh_token=refresh_token,
    )
