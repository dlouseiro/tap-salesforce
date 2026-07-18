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
environment management (`pyproject.toml` / `poetry.lock`). It's only needed
for local development — end users installing the tap (see "Install the tap"
below) just use `pip`.

```bash
# Clone your fork, then:
poetry install --with dev --all-extras

# Run the test suite
poetry run pytest tests/

# Lint / format check
poetry run ruff check tap_salesforce tests
poetry run ruff format --check tap_salesforce tests

# Run the tap itself from the poetry-managed environment
poetry run tap-salesforce --config config.json --discover > properties.json
```

`--all-extras` pulls in `keyring`, needed to exercise the browser auth
flow's OS-keychain path. `--with dev` pulls in `pytest` and `ruff`. CI
(`.github/workflows/ci.yml`) runs the same commands on every push/PR to
`main`, across Python 3.10–3.13.

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

**Required for OAuth 2.0 Refresh Token grant**
```
{
  "client_id": "secret_client_id",
  "client_secret": "secret_client_secret",
  "refresh_token": "abc123"
}
```

**Required for OAuth 2.0 Client Credentials grant**
```
{
  "client_id": "secret_client_id",
  "client_secret": "secret_client_secret",
  "domain": "picnic-nl.my"
}
```

The `domain` must be a Salesforce My Domain (the `login` / `test` shortcuts
are not accepted by Salesforce for this grant). The tap runs as the
Connected App / External Client App's configured "Run As" user.

**Required for OAuth 2.0 Authorization Code + PKCE (browser)**
```
{
  "client_id": "secret_client_id",
  "domain": "picnic-nl.my"
}
```

Optionally pin the browser flow explicitly (useful when the same config
file also carries a `client_secret` for prod runs):
```
{
  "client_id": "secret_client_id",
  "domain": "picnic-nl.my",
  "browser_auth": true
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
```
pip install tap-salesforce[browser]
```
Without that extra — or if the keychain backend isn't available (e.g. a
headless dev container with no unlocked session) — it falls back
automatically to a plain file at
`~/.tap-salesforce/<domain>/<client_id>.json` (mode `0600`). No
configuration needed either way; the tap tries the keychain first and
falls back transparently.

By default the tap listens on an ephemeral loopback port chosen at
runtime, so no port needs to be hardcoded. If your External Client App's
callback URL is registered with a fixed port instead (or you need a
specific host/path, e.g. behind a local proxy), set `redirect_uri`:
```
{
  "client_id": "secret_client_id",
  "domain": "picnic-nl.my",
  "redirect_uri": "http://localhost:1717/callback"
}
```
If `redirect_uri` is provided without a port (e.g. `"http://localhost/callback"`),
the tap still chooses an ephemeral port and appends it, keeping the given
host and path.

**Required for username/password based authentication (legacy — SOAP)**
```
{
  "username": "Account Email",
  "password": "Account Password",
  "security_token": "Security Token"
}
```

This flow authenticates via Salesforce's SOAP `login()` endpoint and will
stop working once Salesforce retires SOAP login (Summer '27). Migrate to
one of the OAuth flows above.

**Optional**
```
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

The `client_id` and `client_secret` keys are your OAuth Salesforce App secrets. The `refresh_token` is a secret created during the OAuth flow. For more info on the Salesforce OAuth flow, visit the [Salesforce documentation](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/intro_understanding_web_server_oauth_flow.htm).

The `start_date` is used by the tap as a bound on SOQL queries when searching for records.  This should be an [RFC3339](https://www.ietf.org/rfc/rfc3339.txt) formatted date-time, like "2018-01-08T00:00:00Z". For more details, see the [Singer best practices for dates](https://github.com/singer-io/getting-started/blob/master/BEST_PRACTICES.md#dates).

The `api_type` is used to switch the behavior of the tap between using Salesforce's "REST", "BULK" and "BULK 2.0" APIs (each using the `queryAll` operation to include deleted and archived records). When new fields are discovered in Salesforce objects, the `select_fields_by_default` key describes whether or not the tap will select those fields by default.

The `state_message_threshold` is used to throttle how often STATE messages are generated when the tap is using the "REST" API. This is a balance between not slowing down execution due to too many STATE messages produced and how many records must be fetched again if a tap fails unexpectedly. Defaults to 1000 (generate a STATE message every 1000 records).

The `max_workers` value is used to set the maximum number of threads used in order to concurrently extract data for streams. Defaults to 8 (extract data for 8 streams in paralel).

The `streams_to_discover` value may contain a list of Salesforce streams (each ending up in a target table) for which the discovery is handled.
By default, discovery is handled for all existing streams, which can take several minutes. With just several entities which users typically need it is running few seconds.
The disadvantage is that you have to keep this list in sync with the `select` section, where you specify all properties(each ending up in a table column).

The `lookback_window` (in seconds) subtracts the desired amount of seconds from the bookmark to sync past data. Recommended value: 10 seconds.

The `api_version` defines the version of the Salesforce API to use. Default: v60.0.

The `ignore_formula_fields` flag excludes Salesforce formula fields from synchronization. Formula fields are computed dynamically and don't trigger LastModifiedDate updates when their values change. This can lead to inconsistencies during incremental syncs, as changes won't be detected. Consider handling these calculations in your transformation layer instead. Default: false.

The `soql_filters` option allows you to specify additional SOQL filters per stream/object. These filters are appended to the WHERE clause and combined with the replication window when present.

Example:

```
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
