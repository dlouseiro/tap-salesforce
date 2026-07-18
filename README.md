# tap-salesforce

[Singer](https://www.singer.io/) tap that extracts data from a [Salesforce](https://www.salesforce.com/) Account and produces JSON-formatted data following the [Singer spec](https://github.com/singer-io/getting-started/blob/master/SPEC.md).

## Fork lineage

This repo is a personal fork, two levels removed from the original:

```
singer-io/tap-salesforce   (original Stitch project, last common version v1.4.24)
        └─ MeltanoLabs/tap-salesforce   ("meltano." versions in CHANGELOG.md)
                └─ dlouseiro/tap-salesforce   (this repo — "dlouseiro." versions)
```

- [`singer-io/tap-salesforce`](https://github.com/singer-io/tap-salesforce) — the original Stitch-maintained tap.
- [`MeltanoLabs/tap-salesforce`](https://github.com/MeltanoLabs/tap-salesforce) — Meltano's fork, adding `username/password/security_token` auth, concurrent execution across streams, and faster discovery.
- **This repo** — adds OAuth 2.0 (Client Credentials + browser/PKCE) authentication, a Python 3.10+ floor, and assorted fixes/config options (see `CHANGELOG.md`).

### Versioning

`CHANGELOG.md` entries and git tags are prefixed to show which fork introduced each change:

| Prefix | Meaning |
| --- | --- |
| (none) / `meltano.` | Inherited from `MeltanoLabs/tap-salesforce` (or, further back, plain-numbered entries inherited from `singer-io/tap-salesforce`) |
| `dlouseiro.` (changelog) / `dlouseiro-v*` (git tags) | Exclusive to this fork — never went upstream |

Both lineages follow semver relative to their own prior version, not to each other — a `dlouseiro.` major bump doesn't imply anything about `MeltanoLabs`' own next release, and vice versa.

## Development

This project uses [Poetry](https://python-poetry.org/) for dependency and
environment management (`pyproject.toml` / `poetry.lock`), and
[tox](https://tox.wiki/) to run the same checks CI runs — lint and the full
test matrix across every supported Python version — in one local command,
without hand-installing dependencies into your own environment. Neither is
needed by end users installing the tap (see "Install the tap" below); that's
plain `pip`.

```bash
# Clone your fork, then:
poetry install --with dev --all-extras

# Run everything CI runs, locally, in one shot:
poetry run tox

# ...or just one piece of it:
poetry run tox -e lint      # ruff check + ruff format --check
poetry run tox -e py312     # pytest on a specific Python version

# Run the tap itself from the poetry-managed environment
poetry run tap-salesforce --config config.json --discover > properties.json
```

`--all-extras` pulls in `keyring`, needed to exercise the browser auth
flow's OS-keychain path. `--with dev` pulls in `pytest`, `ruff`, `tox`, and
`pre-commit`. `.github/workflows/ci.yml` runs the exact same `tox`
environments on every push/PR to `main`, across Python 3.10–3.13
(`tox.ini` is the single source of truth for what "passing" means, shared
by CI and local runs).

**If you use `pyenv`:** `tox` needs each Python version's interpreter
directly resolvable (e.g. `python3.12` on `PATH`), which a single active
`pyenv local` version won't provide for the others. Run
`pyenv local 3.10.x 3.11.x 3.12.x 3.13.x` (your closest installed patch
versions) in the repo root so `tox` can find all four; otherwise it
silently skips whichever ones it can't find (`skip_missing_interpreters`
is enabled in `tox.ini`, so this doesn't fail the run — just narrows what
actually gets checked locally).

### Git hooks (optional but recommended)

```bash
poetry run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Wires up two speeds of check automatically:
- **On every `git commit`** — `ruff check --fix`, `ruff format`, and a few
  basic file hygiene checks (fast, seconds).
- **On every `git push`** — the full `tox` suite (slower; this is what
  used to mean manually remembering to run everything before pushing).

# Quickstart

## Install the tap

This version of `tap-salesforce` is not available on PyPI, so you fetch it directly from this fork:

```bash
python3 -m venv venv
source venv/bin/activate
pip install git+https://github.com/dlouseiro/tap-salesforce.git
```

## Create a Config file

Every config requires these top-level keys:

```json
{
  "auth_method": "browser",
  "domain": "mycompany.my",
  "api_type": "BULK2",
  "select_fields_by_default": true
}
```

- **`auth_method`** — Required. One of: `browser`, `client_credentials`, `refresh_token`, `password`.
- **`domain`** — Required. Your Salesforce My Domain string (the part before `.salesforce.com`).
  Examples: `"mycompany.my"` (production), `"mycompany--uat.sandbox.my"` (sandbox).
  For legacy flows that previously used `login.salesforce.com` or `test.salesforce.com`,
  pass `"login"` or `"test"` respectively.
- **`api_type`** — Required. One of: `REST`, `BULK`, `BULK2` (recommended).
- **`select_fields_by_default`** — Required. Whether to auto-select newly-discovered fields.

### Authentication

The tap supports four authentication methods, selected explicitly via `auth_method`.

#### `browser` — OAuth 2.0 Authorization Code + PKCE (interactive)

For local development. Opens a browser on first run; caches the refresh token
for subsequent headless runs.

```json
{
  "auth_method": "browser",
  "domain": "mycompany--uat.sandbox.my",
  "client_id": "3MVG9..."
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_id` | Yes | External Client App's consumer key |
| `domain` | Yes | Salesforce My Domain |
| `redirect_uri` | No | Pin the callback URL (e.g. `http://localhost:29110/callback`). If omitted, an ephemeral loopback port is chosen at runtime. |

The refresh token is cached in your OS keychain when the `keyring` extra is
installed (`pip install tap-salesforce[browser]`). Without it, falls back to
`~/.tap-salesforce/<domain>/<client_id>.json` (mode `0600`).

#### `client_credentials` — OAuth 2.0 Client Credentials (machine-to-machine)

For production/CI. No user interaction, runs as the app's configured "Run As" user.

```json
{
  "auth_method": "client_credentials",
  "domain": "mycompany.my",
  "client_id": "3MVG9...",
  "client_secret": "secret..."
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_id` | Yes | External Client App's consumer key |
| `client_secret` | Yes | External Client App's consumer secret |
| `domain` | Yes | Salesforce My Domain |

#### `refresh_token` — OAuth 2.0 Refresh Token (deprecated)

Pre-obtained refresh token flow. **Deprecated** — migrate to `client_credentials`.

```json
{
  "auth_method": "refresh_token",
  "domain": "mycompany.my",
  "client_id": "3MVG9...",
  "client_secret": "secret...",
  "refresh_token": "5Aep..."
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `client_id` | Yes | OAuth app's consumer key |
| `client_secret` | Yes | OAuth app's consumer secret |
| `refresh_token` | Yes | Long-lived refresh token |
| `domain` | Yes | Salesforce My Domain (or `"login"` / `"test"` for generic endpoints) |

#### `password` — Legacy SOAP login (deprecated)

Username/password/security_token flow. **Deprecated** — will stop working when
Salesforce retires SOAP login (Summer '27). Migrate to `client_credentials`.

```json
{
  "auth_method": "password",
  "domain": "test",
  "username": "user@example.com",
  "password": "mypassword",
  "security_token": "token..."
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `username` | Yes | Salesforce username |
| `password` | Yes | Salesforce password |
| `security_token` | Yes | Salesforce security token |
| `domain` | Yes | `"login"` (production), `"test"` (sandbox), or a My Domain string |

### General Configuration

All authentication methods support the following optional parameters:

```json
{
  "start_date": "2017-11-02T00:00:00Z",
  "api_version": "v60.0",
  "streams_to_discover": ["Lead", "LeadHistory"],
  "ignore_formula_fields": false,
  "state_message_threshold": 1000,
  "max_workers": 8,
  "lookback_window": 10,
  "soql_filters": {
    "Product2": "RecordType.DeveloperName = 'SomeRecordType'"
  }
}
```

- **`start_date`** — Bound on SOQL queries when searching for records.
  [RFC3339](https://www.ietf.org/rfc/rfc3339.txt) formatted (e.g. `"2018-01-08T00:00:00Z"`).

- **`api_version`** — Salesforce API version (e.g. `"v63.0"`). Defaults to `"v60.0"`.

- **`streams_to_discover`** — List of Salesforce objects to discover. If omitted, all
  objects are discovered (can take several minutes).

- **`ignore_formula_fields`** — Exclude formula fields from sync. Defaults to `false`.

- **`state_message_threshold`** — Emit STATE every N records. Defaults to `1000`.

- **`max_workers`** — Max concurrent stream extraction threads. Defaults to `8`.

- **`lookback_window`** — Seconds to subtract from bookmark on resume. Recommended: `10`.

- **`soql_filters`** — Per-stream SOQL WHERE clauses (conditions only, no leading `WHERE`).

### Migration from dlouseiro.3.x

This version introduces **breaking changes** to the authentication configuration:

| Old config | New config |
|---|---|
| `"is_sandbox": true` | Use sandbox domain: `"domain": "mycompany--uat.sandbox.my"` |
| `"browser_auth": true` | `"auth_method": "browser"` |
| (implicit shape detection) | `"auth_method": "client_credentials"` (or `"refresh_token"`, `"password"`) |
| `"domain"` not required for refresh_token/password | `"domain"` is now required for ALL methods |

## Run Discovery

```
tap-salesforce --config config.json --discover > properties.json
```

## Sync Data

```
tap-salesforce --config config.json --properties properties.json [--state state.json]
```

Copyright &copy; 2017 Stitch
