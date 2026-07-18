# Contributing

## Development setup

```bash
git clone https://github.com/dlouseiro/tap-salesforce.git
cd tap-salesforce
poetry install --with dev --all-extras
poetry run pre-commit install --hook-type pre-commit --hook-type pre-push
```

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

| Change type | Version bump | Example |
|---|---|---|
| Breaking config/API change | Major (`X.0.0`) | Removing a config key, changing required fields |
| New feature, backward compatible | Minor (`0.X.0`) | New auth method, new config option |
| Bug fix, docs, internal refactor | Patch (`0.0.X`) | Fix a sync bug, update README |
