# Changelog

Entries prefixed `dlouseiro.` are releases of this personal fork
(`dlouseiro/tap-salesforce`) that never went upstream to MeltanoLabs —
its own independent version lineage, continuing from where this fork's
`setup.py` last matched upstream (`v1.9.0` / `meltano.1.5.0`, tagged
`v1.9.0`). Entries prefixed `meltano.` (or unprefixed, further below) are
inherited from upstream. Git tags matching each `dlouseiro.X.Y.Z` entry
are pushed as `dlouseiro-vX.Y.Z`.

## dlouseiro.4.0.0

- **Breaking:** Replace implicit credential shape detection with an explicit
  `auth_method` config key. The tap now requires `auth_method` to be set to
  one of: `browser`, `client_credentials`, `refresh_token`, or `password`.
  The old "first matching shape wins" dispatch is removed entirely.

- **Breaking:** `domain` is now a required config key for ALL authentication
  methods (previously only needed for Client Credentials and Browser flows).
  For legacy flows that used `login.salesforce.com` or `test.salesforce.com`,
  pass `"login"` or `"test"` as the domain value respectively.

- **Breaking:** Remove `is_sandbox` config key. Sandbox-ness is now encoded
  in the `domain` string itself (e.g. `mycompany--uat.sandbox.my` for sandbox,
  `mycompany.my` for production).

- **Breaking:** Remove `browser_auth` config key. Use `auth_method: browser`
  to explicitly select the browser flow.

- Deprecate `auth_method: refresh_token` and `auth_method: password` with
  runtime `DeprecationWarning`. These flows will be removed in a future
  major version. Migrate to `client_credentials` (production/CI) or
  `browser` (local dev).

- Modularize codebase: split monolithic `__init__.py` into focused modules
  (`config.py`, `discovery.py`, `sync_orchestrator.py`, `salesforce/client.py`,
  `salesforce/schema.py`). Wrap sync logic in `SyncService` class. Add 120+
  new unit tests (42 → 166 total) covering all previously untested modules.

## dlouseiro.3.0.1

- Fix the browser (Authorization Code + PKCE) flow silently losing its
  cached refresh token when Salesforce rotates it. On a cache-hit
  refresh exchange, if the response included a new `refresh_token`
  (some External Client App policies rotate it on every use), the
  stale cached one was kept regardless -- once Salesforce invalidated
  it, the next run had to fall back to a full browser round-trip.
  Found via real end-to-end sandbox testing: browser mode reopened a
  login window on a run that should have reused a cached token.
  Now persists the rotated token when one is returned, and leaves the
  cache untouched when the token comes back unchanged.

## dlouseiro.3.0.0

- **Breaking:** raise the minimum supported Python version to **3.10**
  (from an unenforced, undeclared floor). `setup.py` now declares
  `python_requires=">=3.10"`. Verified against 3.10, 3.11, 3.12, and 3.13
  (a full test-suite run + a real end-to-end sync against a Salesforce
  sandbox on each).
- Upgrade all direct dependencies to their latest versions as of this
  release: `requests` 2.32.2 → 2.34.2, `singer-python` `~=5.13` → `~=6.8`,
  `xmltodict` 0.11.0 → 1.0.4, `idna` 3.7 → 3.18. `cryptography` and
  `pyOpenSSL` were already unpinned and pick up their latest compatible
  releases automatically.
  - `singer-python` 6.x renamed `write_bookmark`/`get_bookmark` internals
    but kept both names as fully-compatible aliases — verified by
    introspecting the installed package; no code changes needed.
    `set_version`/`get_version`/`clear_version` had a breaking signature
    change in 6.7.0, but this tap never used those functions.
  - `xmltodict` 1.0 output shapes were verified against every call site in
    `bulk.py`, including the `force_list` single-vs-multi-child edge case
    that's the classic regression risk for this kind of upgrade.
  - Removed the now-obsolete `idna==3.7` pin comment referencing an old
    `requests`/`idna` conflict (meltano/meltano#193) — a fresh install
    with the upgraded pins has no dependency conflicts (`pip check` clean).
- Add a `test` extra (`pip install -e .[test]`) providing `pytest`. Fixes
  `.circleci/config.yml`, which previously installed `nose` — long
  unmaintained and broken on Python 3.10+ (it references
  `collections.Callable`, removed in 3.10) — replaced with `pytest`.
- Bump `ruff`'s `target-version` from `py37` to `py310`, surfacing two
  findings: modernized a `typing.Sequence` import to `collections.abc`,
  and added an explicit `strict=False` to a `zip()` call in `bulk.py`'s
  CSV row parsing (preserves existing tolerant behaviour for
  malformed/truncated rows rather than silently changing it to a hard
  failure).

## dlouseiro.2.5.1

- Add test coverage for `acquire_token`'s cache-hit / cache-hit-rejected
  / cache-miss branches (previously untested — only lower-level pieces
  like PKCE math and endpoint construction had coverage). No production
  behaviour change.

## dlouseiro.2.5.0

- Upgrade `simple-salesforce` from `<1.0` to `~=1.12`. Adapted the
  `SalesforceLogin` call in the legacy password path from the removed
  `sandbox=` kwarg to the modern `domain=` kwarg (no behaviour change).
- Add support for the **OAuth 2.0 Client Credentials** grant via new
  `client_id` + `client_secret` + `domain` config keys. Dispatched
  through `simple-salesforce`'s `SalesforceLogin`. Intended for
  machine-to-machine (cron / prod) execution as the External Client App's
  "Run As" user.
- Add support for the **OAuth 2.0 Authorization Code Flow with PKCE**
  (browser) via new `client_id` + `domain` config keys, or explicit
  `browser_auth: true`. Opens a browser on first run, caches the
  resulting refresh token at `~/.tap-salesforce/<domain>/<client_id>.json`
  (mode `0600`), and re-uses it silently on subsequent runs. Intended for
  local developer machines. Implemented with stdlib + `requests` only
  (no new runtime dependencies).

## dlouseiro.2.4.1

- Fix response handling error: a failed Bulk API 2.0 job status check
  called `.json()` on an already-parsed response object instead of the
  raw HTTP response, masking the real failure reason behind an
  `AttributeError`.

## dlouseiro.2.4.0

- Add a `soql_filters` config option: per-object SOQL filter conditions
  that are appended to the generated `WHERE` clause alongside the
  replication-key window. Backward-compatible (defaults to none).

## dlouseiro.2.3.0

- Add an `api_version` config option, replacing a previously-hardcoded
  Salesforce API version constant. Backward-compatible (defaults to the
  prior hardcoded value). Includes follow-up fixes to the Bulk API call
  and API-version string formatting made over the following week, plus
  an unrelated code-formatting cleanup.

## dlouseiro.2.2.0

- Add an `ignore_formula_fields` config option to exclude Salesforce
  formula fields from synchronization (they don't reliably trigger
  `LastModifiedDate` updates, which can cause incremental syncs to miss
  changes). Backward-compatible (defaults to `false`). An initial,
  same-day version also ignored lookup fields; that part was reverted
  before this reached upstream, leaving only formula-field exclusion.

## dlouseiro.2.1.0

- Add a `lookback_window` config option (in seconds): subtracts the
  given duration from the replication bookmark before querying, to
  re-fetch a small overlap window and guard against clock-skew/timing
  edge cases at incremental sync boundaries. Backward-compatible
  (defaults to no lookback).

## dlouseiro.2.0.0

- Baseline reset: this is the first version in the `dlouseiro.` fork
  lineage, encompassing all fork-specific changes up to this point that
  had never previously been tagged or changelogged:
  - Exclude compound fields from Bulk API queries (unsupported by that
    API).
  - Exclude location fields from Bulk API queries (unsupported by that
    API).
  - Default loose-type fields (`anyType`, `calculated`) to string type
    instead of leaving them typeless.
  - Add `.idea/` to `.gitignore`.

## meltano.1.5.0

- [#14](https://gitlab.com/meltano/tap-salesforce/-/issues/14) Apply schema filtering per property selection rules.

## meltano.1.4.27

- [#11](https://gitlab.com/meltano/tap-salesforce/-/issues/11) Don't run indefinitely when using OAuth and job runs for more than 15 minutes

## meltano.1.4.26

- [#11](https://gitlab.com/meltano/tap-salesforce/-/issues/11) Don't run indefinitely when using OAuth

## meltano.1.4.25

- [#10](https://gitlab.com/meltano/tap-salesforce/-/issues/10) Fix broken `is_sandbox` setting

## 1.4.24

- Mark json fields as `unsupported` instead of throwing exception. If, in the future, we find streams with json fields that have records, we can consider supporting the json field type. [commit](https://github.com/singer-io/tap-salesforce/commit/85e3811b9cb5673e23cab8e7b011d2a3d3064d0f)

## 1.4.23

- Protect against empty strings for quota config fields [commit](https://github.com/singer-io/tap-salesforce/commit/1133726e20af434d82af8761ba3ad006f49f0b42)

## 1.4.22

- Filter out \*ChangeEvent tables from discovery as neither REST nor BULK can sync them [#62](https://github.com/singer-io/tap-salesforce/pull/62)

## 1.4.21

- Move the transformer outside of the record write-loop to quiet logging [#61](https://github.com/singer-io/tap-salesforce/pull/61)

## 1.4.20

- (Bulk Rest API) Sync the second half of the date range After a timeout occurs and the date window is halved [#60](https://github.com/singer-io/tap-salesforce/pull/60)

## 1.4.19

- (Bulk API) Removes failed jobs that don't exists in Salesforce from state when encountered [#57](https://github.com/singer-io/tap-salesforce/pull/57)
- (All APIs) Makes `BackgroundOperationResult` sync full table, since it cannot be sorted by `CreatedDate` [#58](https://github.com/singer-io/tap-salesforce/pull/58)
- Update version of `requests` to `2.20.0` in response to CVE 2018-18074 [#59](https://github.com/singer-io/tap-salesforce/pull/59)

## 1.4.18

- Increases the `field_size_limit` on the CSV reader to enable larger fields coming through without error [#53](https://github.com/singer-io/tap-salesforce/pull/53)

## 1.4.17

- Adds the suffix "FieldHistory" to those checked for when finding the parent object to fix the `OpportunityFieldHistory` stream [#52](https://github.com/singer-io/tap-salesforce/pull/52)

## 1.4.16

- Fixes a few bugs with PK chunking including allowing a custom table to be chunked by its parent table [#51](https://github.com/singer-io/tap-salesforce/pull/51)

## 1.4.15

- Added a correct else condition to fix an error being raised during the PK Chunking query [#50](https://github.com/singer-io/tap-salesforce/pull/50)

## 1.4.14

- Updated the usage of singer-python's Transformer to reduce its scope [#48](https://github.com/singer-io/tap-salesforce/pull/48)

## 1.4.13

- Updated the JSON schema generated for Salesforce Date types to use `anyOf` so when a bad date comes through we use the String instead [#47](https://github.com/singer-io/tap-salesforce/pull/47)

## 1.4.12

- Bug fix for metadata when resuming bulk sync jobs.

## 1.4.11

- Moved ContentFolderItem to query restricted objects list since the REST API requires specific IDs to query this object.

## 1.4.10

- Read replication-method, replication-key from metadata instead of Catalog. Publish key-properties as table-key-properties metadata instead of including on the Catalog.

## 1.4.9

- Fixes logging output when an HTTP error occurs

## 1.4.8

- Bumps singer-python dependency to help with formatting dates < 1000

## 1.4.7

- Fixes a bug with datetime conversion during the generation of the SF query string [#40](https://github.com/singer-io/tap-salesforce/pull/40)

## 1.4.6

- Fixes more bugs with exception handling where the REST API was not capturing the correct error [#39](https://github.com/singer-io/tap-salesforce/pull/39)

## 1.4.5

- Fixes a schema issue with 'location' fields that come back as JSON objects [#36](https://github.com/singer-io/tap-salesforce/pull/36)
- Fixes a bug where a `"version"` in the state would not be preserved due to truthiness [#37](https://github.com/singer-io/tap-salesforce/pull/37)
- Fixes a bug in exception handling where rendering an exception as a string would cause an additional exception [#38](https://github.com/singer-io/tap-salesforce/pull/38)

## 1.4.4

- Fixes automatic property selection when select-fields-by-default is true [#35](https://github.com/singer-io/tap-salesforce/pull/35)

## 1.4.3

- Adds the `AttachedContentNote` and `QuoteTemplateRichTextData` objects to the list of query-incompatible Salesforce objects so they are excluded from discovery / catalogs [#34](https://github.com/singer-io/tap-salesforce/pull/34)

## 1.4.2

- Adds backoff for the `_make_request` function to prevent failures in certain cases [#33](https://github.com/singer-io/tap-salesforce/pull/33)

## 1.4.1

- Adds detection for certain SF Objects whose parents can be used as the parent during PK Chunking [#32](https://github.com/singer-io/tap-salesforce/pull/32)

## 1.4.0

- Fixes a logic bug in the build_state function
- Improves upon streaming bulk results by first writing the file to a tempfile and then consuming it [#31](https://github.com/singer-io/tap-salesforce/pull/31)

## 1.3.9

- Updates the retrieval of a bulk result set to be downloaded entirely instead of streaming [#30](https://github.com/singer-io/tap-salesforce/pull/30)

## 1.3.8

- Removes `multipleOf` JSON Schema parameters for latitude / longitude fields that are part of an Address object

## 1.3.7

- Adds a check to make sure the start_date has time information associated with it
- Adds more robust parsing for select_fields_by_default

## 1.3.6

- Fixes a bug with running the tap when provided a Catalog containing streams without a replication key [#27](https://github.com/singer-io/tap-salesforce/pull/27)

## 1.3.5

- Bumps the dependency singer-python's version to 5.0.4

## 1.3.4

- Fixes a bug where bookmark state would not get set after resuming a PK Chunked Bulk Sync [#24](https://github.com/singer-io/tap-salesforce/pull/24)

## 1.3.3

- Adds additional logging and state management during a PK Chunked Bulk Sync

## 1.3.2

- Fixes a bad variable name

## 1.3.1

- Uses the correct datetime to string function for chunked bookmarks

## 1.3.0

- Adds a feature for resuming a PK-Chunked Bulk API job [#22](https://github.com/singer-io/tap-salesforce/pull/22)
- Fixes an issue where a Salesforce's field data containing NULL bytes would cause an error reading the CSV response [#21](https://github.com/singer-io/tap-salesforce/pull/21)
- Fixes an issue where the timed `login()` thread could die and never call a new login [#20](https://github.com/singer-io/tap-salesforce/pull/20)

## 1.2.2

- Fixes a bug with with yield records when the Bulk job is successful [#19](https://github.com/singer-io/tap-salesforce/pull/19)

## 1.2.1

- Fixes a bug with a missing pk_chunking attribute

## 1.2.0

- Adds support for Bulk API jobs which time out to be retried with Salesforce's PK Chunking feature enabled

## 1.1.1

- Allows compound fields to be supported with the exception of "address" types
- Adds additional unsupported Bulk API Objects

## 1.1.0

- Support for time_extracted property on Singer messages

## 1.0.0

- Initial release
