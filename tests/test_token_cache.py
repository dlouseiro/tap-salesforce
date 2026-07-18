"""Tests for the refresh-token cache (keyring-first, file-fallback)."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tap_salesforce.salesforce import token_cache


class _FakeKeyring:
    """In-memory stand-in for a working `keyring` backend."""

    def __init__(self):
        self._store = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password


class _BrokenKeyring:
    """Simulates `keyring` being installed but its backend not working
    (e.g. headless environment with no unlocked keychain/session)."""

    def get_password(self, service, username):
        raise RuntimeError("no backend available")

    def set_password(self, service, username, password):
        raise RuntimeError("no backend available")


class FileCacheTests(unittest.TestCase):
    """Exercises the file backend directly, with keyring forced off.

    Forcing ``token_cache.keyring`` to ``None`` makes these tests
    deterministic regardless of whether the real ``keyring`` package
    happens to be installed (or working) in the environment running them.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)
        self._keyring_patch = patch.object(token_cache, "keyring", None)
        self._keyring_patch.start()

    def tearDown(self):
        self._keyring_patch.stop()
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


class KeyringBackendTests(unittest.TestCase):
    """Exercises the keyring-first / file-fallback dispatch in load/store."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_store_prefers_keyring_over_file_when_available(self):
        fake = _FakeKeyring()
        with patch.object(token_cache, "keyring", fake):
            token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt-keyring")
            self.assertEqual(
                token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"),
                "rt-keyring",
            )
        # Nothing should have been written to disk — the keyring succeeded.
        with patch.object(token_cache, "keyring", None):
            self.assertIsNone(token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"))

    def test_load_falls_back_to_file_when_keyring_not_installed(self):
        with patch.object(token_cache, "keyring", None):
            token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt-file")
            self.assertEqual(
                token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"),
                "rt-file",
            )

    def test_store_falls_back_to_file_when_keyring_backend_broken(self):
        with patch.object(token_cache, "keyring", _BrokenKeyring()):
            token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt-fallback")
        # Confirm it landed on disk, not silently dropped.
        with patch.object(token_cache, "keyring", None):
            self.assertEqual(
                token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"),
                "rt-fallback",
            )

    def test_load_falls_back_to_file_when_keyring_backend_broken(self):
        with patch.object(token_cache, "keyring", None):
            token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci", "rt-existing")
        with patch.object(token_cache, "keyring", _BrokenKeyring()):
            self.assertEqual(
                token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci"),
                "rt-existing",
            )

    def test_keyring_entries_are_isolated_per_domain_and_client(self):
        fake = _FakeKeyring()
        with patch.object(token_cache, "keyring", fake):
            token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci-a", "rt-a")
            token_cache.store_refresh_token(self.cache_dir, "picnic-de.my", "ci-a", "rt-b")
            token_cache.store_refresh_token(self.cache_dir, "picnic-nl.my", "ci-c", "rt-c")
            self.assertEqual(token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci-a"), "rt-a")
            self.assertEqual(token_cache.load_refresh_token(self.cache_dir, "picnic-de.my", "ci-a"), "rt-b")
            self.assertEqual(token_cache.load_refresh_token(self.cache_dir, "picnic-nl.my", "ci-c"), "rt-c")


if __name__ == "__main__":
    unittest.main()
