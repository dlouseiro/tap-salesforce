"""Tests for the PKCE + endpoint helpers in ``browser_auth``.

The interactive browser flow itself is not exercised (it needs a real Salesforce
login page). We cover the helpers that are testable in isolation.
"""

import base64
import hashlib
import http.client
import http.server
import threading
import unittest

from tap_salesforce.salesforce import browser_auth


class PkcePairTests(unittest.TestCase):
    def test_verifier_and_challenge_are_url_safe_base64(self):
        verifier, challenge = browser_auth._generate_pkce_pair()
        # Should contain only URL-safe base64 characters (no padding).
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        self.assertTrue(set(verifier).issubset(allowed))
        self.assertTrue(set(challenge).issubset(allowed))
        # Verifier length per RFC 7636 is between 43 and 128 characters.
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)

    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = browser_auth._generate_pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)

    def test_pkce_pair_is_random_per_call(self):
        pairs = {browser_auth._generate_pkce_pair() for _ in range(5)}
        self.assertEqual(len(pairs), 5)


class EndpointTests(unittest.TestCase):
    def test_authorize_and_token_endpoints_point_at_my_domain(self):
        self.assertEqual(
            browser_auth._authorize_endpoint("picnic-nl.my"),
            "https://picnic-nl.my.salesforce.com/services/oauth2/authorize",
        )
        self.assertEqual(
            browser_auth._token_endpoint("picnic-nl.my"),
            "https://picnic-nl.my.salesforce.com/services/oauth2/token",
        )


class ResolveRedirectUriTests(unittest.TestCase):
    def test_none_defaults_to_localhost_with_ephemeral_port(self):
        resolved, port = browser_auth._resolve_redirect_uri(None)
        self.assertEqual(resolved, f"http://localhost:{port}/callback")
        self.assertGreater(port, 0)

    def test_explicit_port_is_used_verbatim(self):
        resolved, port = browser_auth._resolve_redirect_uri("http://localhost:1717/callback")
        self.assertEqual(resolved, "http://localhost:1717/callback")
        self.assertEqual(port, 1717)

    def test_no_port_appends_ephemeral_port_keeping_host_and_path(self):
        resolved, port = browser_auth._resolve_redirect_uri("http://my-proxy-host/oauth/callback")
        self.assertEqual(resolved, f"http://my-proxy-host:{port}/oauth/callback")
        self.assertGreater(port, 0)

    def test_no_path_defaults_to_callback(self):
        resolved, port = browser_auth._resolve_redirect_uri("http://localhost")
        self.assertEqual(resolved, f"http://localhost:{port}/callback")

    def test_explicit_port_with_different_host_is_unchanged(self):
        resolved, port = browser_auth._resolve_redirect_uri("https://my-proxy-host:9999/callback")
        self.assertEqual(resolved, "https://my-proxy-host:9999/callback")
        self.assertEqual(port, 9999)


class CallbackHandlerTests(unittest.TestCase):
    """Exercises the real _CallbackHandler + HTTPServer (no mocking).

    Regression coverage for a race where a browser's automatic follow-up
    request to the callback origin (e.g. /favicon.ico, which carries no
    query string) could arrive and be processed before the real OAuth
    callback was read, silently overwriting server.oauth_result and
    producing a false "state mismatch" failure.
    """

    def _make_server(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), browser_auth._CallbackHandler)
        server.oauth_result = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _get(self, port, path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()

    def test_stray_request_without_code_or_error_is_ignored(self):
        server, thread = self._make_server()
        try:
            status = self._get(server.server_address[1], "/favicon.ico")
            self.assertEqual(status, 404)
            self.assertIsNone(server.oauth_result)
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_real_callback_is_captured(self):
        server, thread = self._make_server()
        try:
            status = self._get(server.server_address[1], "/callback?code=abc123&state=xyz")
            self.assertEqual(status, 200)
            self.assertEqual(server.oauth_result, {"code": "abc123", "state": "xyz"})
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_stray_request_after_real_callback_does_not_overwrite_it(self):
        server, thread = self._make_server()
        try:
            port = server.server_address[1]
            self._get(port, "/callback?code=abc123&state=xyz")
            self._get(port, "/favicon.ico")
            self.assertEqual(server.oauth_result, {"code": "abc123", "state": "xyz"})
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_error_callback_is_captured(self):
        server, thread = self._make_server()
        try:
            status = self._get(server.server_address[1], "/callback?error=access_denied")
            self.assertEqual(status, 200)
            self.assertEqual(server.oauth_result, {"error": "access_denied"})
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
