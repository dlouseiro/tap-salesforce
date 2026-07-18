"""Refresh-token cache for the browser OAuth flow.

Two backends, tried in this order:

1. **OS keychain** via the optional ``keyring`` package (macOS Keychain,
   GNOME Keyring / KWallet on Linux, Windows Credential Locker). Used
   automatically when ``keyring`` is installed
   (``pip install tap-salesforce[browser]``) and a working backend is
   available.
2. **Plain JSON file** at ``<cache_dir>/<domain>/<client_id>.json`` (mode
   ``0600``, directory mode ``0700``) — same posture as ``~/.sfdx/``. Used
   whenever ``keyring`` isn't installed, or its backend errors out (e.g.
   headless environments with no unlocked keychain / session, which is
   common for remote dev containers or CI).

Callers never choose a backend explicitly — :func:`load_refresh_token` and
:func:`store_refresh_token` handle the fallback transparently, so the same
code works whether or not ``keyring`` is installed or functional.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path

try:
    import keyring
except ImportError:  # keyring is an optional extra: pip install tap-salesforce[browser]
    keyring = None

LOGGER = logging.getLogger(__name__)

_SERVICE_PREFIX = "tap-salesforce"


def _keyring_service(domain: str) -> str:
    return f"{_SERVICE_PREFIX}:{domain}"


def _cache_file(cache_dir: Path, domain: str, client_id: str) -> Path:
    return cache_dir / domain / f"{client_id}.json"


def _load_from_keyring(domain: str, client_id: str) -> str | None:
    if keyring is None:
        return None
    try:
        return keyring.get_password(_keyring_service(domain), client_id)
    except Exception as e:  # keyring backends raise a variety of exception types
        LOGGER.debug("Keyring lookup failed (%s); falling back to file cache", e)
        return None


def _store_to_keyring(domain: str, client_id: str, refresh_token: str) -> bool:
    """Attempt to store in the OS keychain. Returns True on success."""
    if keyring is None:
        return False
    try:
        keyring.set_password(_keyring_service(domain), client_id, refresh_token)
        return True
    except Exception as e:  # keyring backends raise a variety of exception types
        LOGGER.debug("Keyring store failed (%s); falling back to file cache", e)
        return False


def _load_from_file(cache_dir: Path, domain: str, client_id: str) -> str | None:
    path = _cache_file(cache_dir, domain, client_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("Could not read cached refresh token from %s: %s", path, e)
        return None
    return payload.get("refresh_token")


def _store_to_file(cache_dir: Path, domain: str, client_id: str, refresh_token: str) -> None:
    domain_dir = cache_dir / domain
    domain_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ``mkdir(mode=…)`` is honoured only when the directory is created; enforce
    # it explicitly in case the tree already existed with looser permissions.
    # ``chmod`` is best-effort — silently skip on non-Unix platforms.
    with contextlib.suppress(OSError):
        os.chmod(domain_dir, 0o700)

    path = _cache_file(cache_dir, domain, client_id)
    payload = {"refresh_token": refresh_token, "obtained_at": time.time()}
    path.write_text(json.dumps(payload))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def load_refresh_token(cache_dir: Path, domain: str, client_id: str) -> str | None:
    """Return the cached refresh token for this (domain, client_id), if any.

    Tries the OS keychain first (if ``keyring`` is installed and its
    backend is working), then falls back to the on-disk JSON cache.
    """
    token = _load_from_keyring(domain, client_id)
    if token is not None:
        return token
    return _load_from_file(cache_dir, domain, client_id)


def store_refresh_token(cache_dir: Path, domain: str, client_id: str, refresh_token: str) -> None:
    """Persist the refresh token, preferring the OS keychain over disk.

    Tries the OS keychain first; if ``keyring`` isn't installed or its
    backend rejects the write, falls back to the on-disk JSON cache
    (owner-only permissions, best-effort).
    """
    if _store_to_keyring(domain, client_id, refresh_token):
        return
    _store_to_file(cache_dir, domain, client_id, refresh_token)
