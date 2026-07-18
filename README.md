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

**Required**
```
{
  "api_type": "BULK2",
  "select_fields_by_default": true,
}
```

### Authentication

The tap supports four authentication flows. Pick one per environment; when
multiple credential shapes are populated, the first one in this list wins:

1. **OAuth 2.0 Refresh Token grant** — pre-obtained refresh token, headless.
2. **OAuth 2.0 Client Credentials grant** — machine-to-machine, no user context.
3. **OAuth 2.0 Authorization Code + PKCE (browser)** — interactive local login.
4. **Legacy SOAP username/password/security_token** — retired by Salesforce Summer '27.

#### Shared authentication parameters

The following parameters are used by multiple authentication methods:

- **`domain`** — Salesforce My Domain (e.g., `"picnic-nl.my"`) used by Client Credentials
  and browser authentication. This is the custom domain suffix created in your Salesforce org
  (the part before `.salesforce.com`). For these methods, the `login` and `test` domain shortcuts
  are not accepted by Salesforce. Only used if you're using one of those auth flows; not needed
  for Refresh Token or legacy SOAP auth.

- **`is_sandbox`** — Boolean flag to authenticate against Salesforce's sandbox environment
  (`test.salesforce.com` instead of `login.salesforce.com`). Supported by all four authentication
  flows; defaults to `false` (production). Set to `true` or the string `"true"` for sandbox.

#### OAuth 2.0 Refresh Token grant

**Required parameters:**
- `client_id` — OAuth app's client ID (consumer key).
- `client_secret` — OAuth app's client secret (consumer secret).
- `refresh_token` — Long-lived refresh token obtained during the OAuth flow.

```json
{
  "client_id": "secret_client_id",
  "client_secret": "secret_client_secret",
  "refresh_token": "abc123"
}
```

Use this for headless sync scenarios (cron, CI/CD, scheduled tasks) where you've
already completed an OAuth authorization and stored the refresh token.

#### OAuth 2.0 Client Credentials grant

**Required parameters:**
- `client_id` — External Client App's consumer key.
- `client_secret` — External Client App's consumer secret.
- `domain` — Salesforce My Domain (e.g., `"picnic-nl.my"`). The `login` / `test`
  shortcuts are not accepted by Salesforce for this grant.

```json
{
  "client_id": "secret_client_id",
  "client_secret": "secret_client_secret",
  "domain": "picnic-nl.my"
}
```

The tap runs as the Connected App / External Client App's configured "Run As" user,
with machine-to-machine authentication and no individual user context.

#### OAuth 2.0 Authorization Code + PKCE (browser)

**Required parameters:**
- `client_id` — OAuth app's client ID.
- `domain` — Salesforce My Domain (e.g., `"picnic-nl.my"`).

```json
{
  "client_id": "secret_client_id",
  "domain": "picnic-nl.my"
}
```

On the first run, the tap opens a browser window so you can log in with
your personal Salesforce user; the resulting refresh token is cached and
reused silently on subsequent runs. If the refresh token is later rejected
(revoked, expired, etc.) the browser step is retried. Intended for local
developer machines only — cron/production should use Client Credentials
or the Refresh Token grant.

The refresh token is cached in your OS keychain (macOS Keychain, GNOME
Keyring/KWallet, Windows Credential Locker) when the optional `keyring`
extra is installed:
```bash
pip install tap-salesforce[browser]
```

Without that extra — or if the keychain backend isn't available (e.g. a
headless dev container with no unlocked session) — it falls back
automatically to a plain file at
`~/.tap-salesforce/<domain>/<client_id>.json` (mode `0600`). No
configuration needed either way; the tap tries the keychain first and
falls back transparently.

**Optional parameters for this flow:**
- `browser_auth` — Explicitly pin the browser flow (useful when the same config
  file also carries a `client_secret` for prod runs; set to `true` to force browser auth
  even if `client_secret` is present). Defaults to `false` (browser auth is chosen
  only if `client_secret` is not present and `refresh_token` is not present).

  ```json
  {
    "client_id": "secret_client_id",
    "domain": "picnic-nl.my",
    "browser_auth": true
  }
  ```

- `redirect_uri` — Override the callback URL for the OAuth redirect. By default the tap
  listens on an ephemeral loopback port chosen at runtime (e.g., `http://localhost:PORT/callback`),
  so no port needs to be hardcoded. Use this when:
  - Your External Client App's registered callback URL pins a specific port (e.g.,
    `http://localhost:1717/callback` — the tap will listen on that exact port).
  - You need a specific host/path behind a local proxy (e.g., `http://proxy.internal:8080/oauth/callback`).
  - If provided without a port (e.g., `"http://localhost/callback"`), the tap still
    chooses an ephemeral port and appends it, keeping the given host and path.

  ```json
  {
    "client_id": "secret_client_id",
    "domain": "picnic-nl.my",
    "redirect_uri": "http://localhost:1717/callback"
  }
  ```

#### Legacy SOAP username/password/security_token

**Required parameters:**
- `username` — Salesforce account email address.
- `password` — Salesforce account password.
- `security_token` — Security token issued by Salesforce.

```json
{
  "username": "account@example.com",
  "password": "mypassword",
  "security_token": "security_token_value"
}
```

This flow authenticates via Salesforce's SOAP `login()` endpoint and will
stop working once Salesforce retires SOAP login (Summer '27). Migrate to
one of the OAuth flows above.

### General Configuration

All authentication methods support the following optional configuration parameters:

```json
{
  "ignore_formula_fields": false,
  "start_date": "2017-11-02T00:00:00Z",
  "state_message_threshold": 1000,
  "max_workers": 8,
  "streams_to_discover": ["Lead", "LeadHistory"],
  "lookback_window": 10,
  "api_version": "v60.0",
  "soql_filters": {
    "Product2": "RecordType.DeveloperName = 'SomeRecordType'"
  }
}
```

#### General parameters

- **`start_date`** — Used by the tap as a bound on SOQL queries when searching for records.
  Should be an [RFC3339](https://www.ietf.org/rfc/rfc3339.txt) formatted date-time, like
  `"2018-01-08T00:00:00Z"`. For more details, see the [Singer best practices for dates](https://github.com/singer-io/getting-started/blob/master/BEST_PRACTICES.md#dates).

- **`api_type`** — Required; switch between Salesforce API backends:
  - `"REST"` — Use the REST API for queries.
  - `"BULK"` — Use Salesforce Bulk API v1 (queryAll includes deleted/archived records).
  - `"BULK2"` — Use Salesforce Bulk API v2 (queryAll includes deleted/archived records); recommended.

- **`select_fields_by_default`** — Required; whether to automatically select newly-discovered
  fields during discovery. Defaults to `false`.

- **`api_version`** — Salesforce API version to use (e.g., `"v60.0"`). Defaults to `"v60.0"`.

#### Discovery & sync parameters

- **`streams_to_discover`** — List of Salesforce objects (streams) to discover during discovery mode.
  By default all streams are discovered, which can take several minutes. Specifying a subset
  speeds up discovery but requires keeping the list in sync with your `select` section.
  Example: `["Lead", "LeadHistory"]`.

- **`ignore_formula_fields`** — Boolean; exclude Salesforce formula fields from synchronization.
  Formula fields are computed dynamically and don't trigger `LastModifiedDate` updates when their
  values change, leading to inconsistencies during incremental syncs. Consider handling these
  calculations in your transformation layer instead. Defaults to `false`.

#### Performance & query parameters

- **`state_message_threshold`** — Throttle how often STATE messages are generated when using the
  REST API (balance between throughput and recovery cost if a sync fails). Defaults to `1000`
  (generate a STATE message every 1000 records).

- **`max_workers`** — Maximum number of worker threads for concurrent stream extraction.
  Defaults to `8` (extract up to 8 streams in parallel).

- **`lookback_window`** — Number of seconds to subtract from the bookmark when resuming an
  incremental sync (rewind the start date to catch up on any records that may have been missed).
  Recommended value: `10` seconds.

- **`soql_filters`** — Object mapping stream/object names to additional SOQL WHERE clause filters.
  Filters are appended to the WHERE clause and combined with the replication window. Useful for
  record-type filtering or other business logic.

  Example:
  ```json
  "soql_filters": {
    "Product2": "RecordType.DeveloperName = 'SomeRecordType'"
  }
  ```

## Run Discovery

To run discovery mode, execute the tap with the config file.

```
tap-salesforce --config config.json --discover > properties.json
```

## Sync Data

To sync data, select fields in the `properties.json` output and run the tap.

```
tap-salesforce --config config.json --properties properties.json [--state state.json]
```

Copyright &copy; 2017 Stitch
