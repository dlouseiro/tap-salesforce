"""On-disk cache for refresh tokens acquired via the browser OAuth flow.

The cache lives at ``<cache_dir>/<domain>/<client_id>.json`` with permissions
locked to the current user (``0700`` on the directory, ``0600`` on the file) —
same posture as ``~/.sfdx/``. Only the refresh token is persisted; access
tokens are short-lived and re-acquired on demand.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _cache_file(cache_dir: Path, domain: str, client_id: str) -> Path:
    return cache_dir / domain / f"{client_id}.json"


def load_refresh_token(cache_dir: Path, domain: str, client_id: str) -> str | None:
    """Return the cached refresh token for this (domain, client_id) tuple, if any."""
    path = _cache_file(cache_dir, domain, client_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("Could not read cached refresh token from %s: %s", path, e)
        return None
    return payload.get("refresh_token")


def store_refresh_token(cache_dir: Path, domain: str, client_id: str, refresh_token: str) -> None:
    """Persist the refresh token with owner-only permissions (best-effort)."""
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
