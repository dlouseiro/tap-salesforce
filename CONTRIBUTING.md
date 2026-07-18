# Contributing

## Development setup

```bash
git clone https://github.com/dlouseiro/tap-salesforce.git
cd tap-salesforce
poetry install --with dev --all-extras
poetry run pre-commit install --hook-type pre-commit --hook-type pre-push
```

`--all-extras` pulls in `keyring`, needed to exercise the browser auth
flow's OS-keychain path. `--with dev` pulls in `pytest`, `ruff`, `tox`, and
`pre-commit`.

### Running checks

```bash
# Run everything CI runs, locally, in one shot:
poetry run tox

# ...or just one piece of it:
poetry run tox -e lint      # ruff check + ruff format --check
poetry run tox -e py312     # pytest on a specific Python version

# Run the tap itself from the poetry-managed environment:
poetry run tap-salesforce --config config.json --discover > properties.json
```

`.github/workflows/ci.yml` runs the exact same `tox` environments on every
push/PR to `main`, across Python 3.10-3.13. `tox.ini` is the single source of
truth for what "passing" means, shared by CI and local runs.

### pyenv note

`tox` needs each Python version's interpreter directly resolvable (e.g.
`python3.12` on `PATH`). Run `pyenv local 3.10.x 3.11.x 3.12.x 3.13.x` in the
repo root so `tox` can find all four; otherwise it silently skips whichever ones
it can't find.

### Git hooks

Pre-commit is configured with two speeds:

- **On every `git commit`** — `ruff check --fix`, `ruff format`, file hygiene (fast).
- **On every `git push`** — the full `tox` suite (slower, matches CI).

## Making changes

1. Create a branch from `main`
2. Make your changes
3. Run checks locally: `poetry run tox`
4. Open a PR against `main`

CI runs lint (ruff) and tests (pytest across Python 3.10-3.13) on every PR.

## Releasing

Releases are fully automated. To ship a new version:

1. **Bump the version** in `pyproject.toml` (semver — major for breaking changes, minor for features, patch for fixes)
2. **Add a changelog entry** in `CHANGELOG.md` with a matching header: `## dlouseiro.X.Y.Z`
3. **Open a PR** — CI will verify the version and changelog are in sync (both must be updated together, or neither)
4. **Merge the PR** — the release workflow automatically:
   - Detects the version bump
   - Creates a `dlouseiro-vX.Y.Z` git tag
   - Creates a GitHub Release with notes extracted from the changelog

That's it. No manual tagging, no manual release creation.

### Version conventions

- `pyproject.toml` contains the canonical version as plain semver (e.g. `4.0.0`)
- Git tags use the format `dlouseiro-v4.0.0`
- Changelog headers use `## dlouseiro.4.0.0`

All three refer to the same release, formatted for their respective audiences (pip/poetry, git/GitHub, humans).

### When to bump what

| Change type                      | Version bump    | Example                                         |
| -------------------------------- | --------------- | ----------------------------------------------- |
| Breaking config/API change       | Major (`X.0.0`) | Removing a config key, changing required fields |
| New feature, backward compatible | Minor (`0.X.0`) | New auth method, new config option              |
| Bug fix, docs, internal refactor | Patch (`0.0.X`) | Fix a sync bug, update README                   |
