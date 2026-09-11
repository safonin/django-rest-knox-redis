# cfm-back Dependency Compatibility Design

## Context

The 0.2.0 release candidate initially declared floors newer than the deployed
cfm-back stack. That project pins Django 6.0.4, DRF 3.16.1, Knox 5.0.4, and
django-redis 6.0.0. uv correctly rejected the candidate first for DRF 3.16.1
and then for Knox 5.0.4; django-redis 6.0.0 would have been the next conflict.
The implementation uses APIs available in all four pinned versions.

## Decision

Declare `djangorestframework>=3.16.1,<3.18`,
`django-rest-knox>=5.0.4,<6`, and `django-redis>=6,<8`. Add an explicit CI job
that pins DRF 3.16.1, Knox 5.0.4, and django-redis 6.0.0 on Django 6.0. Existing
matrix jobs continue testing the newest allowed dependency releases.

Regenerate the portable uv lock, update documentation where the supported
versions are listed, rerun validation, and rebuild the local 0.2.0 wheel and
sdist. The local artifacts may replace the earlier 0.2.0 candidates because
they have not been committed, pushed, or published.

## Rejected alternatives

- Requiring the consuming project to upgrade to DRF 3.17 expands its migration
  scope without an implementation need.
- `uv add --frozen` or `uv pip install --no-deps` suppresses the resolver but
  leaves package metadata inconsistent and makes later syncs fail.

## Verification

- Resolve a fresh environment with Django 6.0.4, DRF 3.16.1, Knox 5.0.4, and
  django-redis 6.0.0.
- Run the core test suite in that environment.
- Run the full current dependency suite, Ruff, mypy, Django checks, lock check,
  and diff check.
- Rebuild wheel and sdist, run Twine checks, inspect metadata, and install the
  rebuilt wheel in a clean environment.
