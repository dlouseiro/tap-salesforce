"""Tests for the PKCE + endpoint helpers in ``browser_auth``.

The interactive browser flow itself is not exercised (it needs a real Salesforce
login page). We cover the helpers that are testable in isolation.
"""

import base64
import hashlib
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


if __name__ == "__main__":
    unittest.main()
