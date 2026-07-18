"""Tests for the refresh-token on-disk cache."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from tap_salesforce.salesforce import token_cache


class TokenCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_returns_none_when_missing(self):
        self.assertIsNone(token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"))

    def test_store_then_load_roundtrip(self):
        token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt-abc")
        self.assertEqual(
            token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"),
            "rt-abc",
        )

    def test_store_uses_per_domain_directory_and_per_client_file(self):
        token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt")
        expected_file = self.cache_dir / "picnic-nl.my" / "ci.json"
        self.assertTrue(expected_file.exists())
        payload = json.loads(expected_file.read_text())
        self.assertEqual(payload["refresh_token"], "rt")
        self.assertIn("obtained_at", payload)

    def test_load_returns_none_on_corrupt_json(self):
        target = self.cache_dir / "picnic-nl.my" / "ci.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not-json")
        self.assertIsNone(token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"))

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX file modes only")
    def test_store_writes_owner_only_permissions(self):
        token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt")
        file_path = self.cache_dir / "picnic-nl.my" / "ci.json"
        dir_path = self.cache_dir / "picnic-nl.my"
        file_mode = stat.S_IMODE(os.stat(file_path).st_mode)
        dir_mode = stat.S_IMODE(os.stat(dir_path).st_mode)
        self.assertEqual(file_mode, 0o600)
        self.assertEqual(dir_mode, 0o700)


if __name__ == "__main__":
    unittest.main()
