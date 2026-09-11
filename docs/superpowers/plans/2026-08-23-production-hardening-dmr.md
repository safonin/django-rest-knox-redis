# Production Hardening and DMR Integration Implementation Plan

> **For agentic workers:** REQUIRED: use subagents for the isolated coding tasks.
> Work only in the current checkout. Do not commit, push, tag, or publish.

**Goal:** Deliver an uncommitted, production-ready 0.2.0 release candidate with
fail-closed Redis revocation, bounded token-cache TTLs, native Knox semantics, and
optional DMR SyncAuth integration.

**Architecture:** The database remains authoritative; Redis accelerates digest
lookup but every hit confirms the exact token and user in one SQL query. A
fail-closed pre-delete signal plus post-commit cleanup handles revocation without
trusting cache invalidation for authorization correctness. Cache misses and
AUTO_REFRESH delegate to Knox. DMR wraps the DRF/Knox authenticators and
translates their public result/error contracts.

**Tech Stack:** Python 3.10-3.14, Django 5.2/6.0, DRF 3.17, django-rest-knox 5.1,
django-redis 7, fakeredis, django-modern-rest 0.12+, pytest, Ruff, Hatch/uv.

**Execution order:** Tasks 1-2 and the dependency/lock portion of Task 5 may run
in parallel with disjoint files. Tasks 3-4 start only after both are integrated,
so DMR tests see the final Knox adapter and installed DMR development dependency.
The remaining metadata/CI work, documentation, and release gates follow.

---

## Chunk 1: Redis and Knox hardening

### Task 1: Add failing cache safety tests

**Files:**
- Modify: `tests/test_cache.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_views.py`
- Modify: `tests/conftest.py`

- [ ] Add full `APIClient` cache-hit and cache-miss logout tests.
- [ ] Add full logout-all tests and repeat-auth rejection.
- [ ] Add ORM instance/queryset deletion and invalidation-failure tests.
- [ ] Assert controlled 503 responses for logout/logout-all invalidation failure,
      retained DB rows, and usable still-valid credentials.
- [ ] Add cascade `User.delete`, partial multi-token failure, transaction rollback,
      and pre-delete/commit repopulation regression tests.
- [ ] Add positive bounded TTL tests for finite expiry and `TOKEN_TTL=None`.
- [ ] Add sub-second expiry, non-positive/invalid setting, UUID user key, corrupt
      payload, and schema-v1 stale-entry tests.
- [ ] Add schema-version, Redis read-outage, expiry, inactive/deleted user tests.
- [ ] Measure total SQL count for hit and miss with `CaptureQueriesContext`.
- [ ] Add AUTO_REFRESH persisted, throttled, and max-TTL tests.
- [ ] Assert AUTO_REFRESH performs zero TokenCache reads/writes, ignores existing
      entries, and prevents login population under dynamic override settings.
- [ ] Count Redis commands and network round trips separately for hit, miss,
      population, logout, logout-all, and failed invalidation.
- [ ] Run focused tests and confirm the new regressions fail for the expected
      current defects.

### Task 2: Implement the authoritative cache contract

**Files:**
- Create: `knox_redis/exceptions.py`
- Modify: `knox_redis/settings.py`
- Modify: `knox_redis/cache.py`
- Modify: `knox_redis/signals.py`
- Modify: `knox_redis/auth.py`
- Modify: `knox_redis/views.py`

- [ ] Add typed settings while retaining `CACHE_ALIAS`, `REDIS_KEY_PREFIX`, and
      `CACHE_ENABLED`; add `MAX_TOKEN_CACHE_TTL=300`.
- [ ] Add schema-versioned typed cache payload parsing.
- [ ] Set token and index TTL atomically and never beyond DB expiry; do not cache
      computed TTL values less than one second.
- [ ] Make pre-delete invalidation raise `CacheInvalidationError` on confirmed
      failure, translate it to DRF 503, and add best-effort on-commit cleanup.
- [ ] Replace `CachedAuthToken` with a one-query authoritative lookup of the
      configured Knox token model and related user.
- [ ] Delegate misses and AUTO_REFRESH to Knox public authentication.
- [ ] Delegate logout views to Knox and cache exact login instances.
- [ ] Run all Redis/DRF tests, Ruff, and Django checks.

## Chunk 2: Optional DMR integration

### Task 3: Add DMR tests

**Files:**
- Create: `tests/test_dmr.py`
- Create: `tests/dmr_urls.py`

- [ ] Use guarded imports so Python 3.10 base jobs can collect tests without DMR.
- [ ] Use per-test URL/settings overrides; do not replace the global DRF URLConf.
- [ ] Test valid cache-hit and DB-only authentication.
- [ ] Test missing, foreign, malformed, invalid, expired, and inactive tokens.
- [ ] Assert `user`, `auth`, `_auth`, and awaited `auser()`.
- [ ] Test required-terminal mode and optional mode with an explicit anonymous
      terminal `SyncAuth`; assert runtime and OpenAPI order including `{}`.
- [ ] Assert emitted 401s are non-500 and contain `WWW-Authenticate: Token`;
      optional missing/foreign credentials must fall through untouched.
- [ ] Generate OpenAPI and assert Authorization `apiKey`, ordered requirements,
      and absence of Bearer semantics.

### Task 4: Implement DMR SyncAuth adapters

**Files:**
- Create: `knox_redis/dmr.py`

- [ ] Subclass public DMR `SyncAuth` without importing DMR from package init.
- [ ] Wrap Redis and database Knox authenticators behind a shared typed base.
- [ ] Preserve Knox parsing by calling the public DRF authenticator.
- [ ] Set all request attributes on success and clear them on recognized errors.
- [ ] Translate DRF/Knox API exceptions through `controller.format_error` and
      DMR `APIError`, preserving status and challenge headers.
- [ ] Add OpenAPI `apiKey` scheme, ordered requirement, and collision-safe 401
      response spec using public DMR imports; do not use HeaderTokenSyncAuth.
- [ ] Run focused tests against exactly DMR 0.12.0 and the installed latest
      version, each with its msgspec serializer extra.

## Chunk 3: Packaging, CI, and documentation

### Task 5: Correct release metadata and matrices

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `uv.lock`
- Modify: `knox_redis/__init__.py`
- Create: `knox_redis/py.typed`
- Modify: `.github/workflows/tests.yml`
- Modify: `.github/workflows/publish.yml` if its pinned actions/tooling require
  correction.

- [ ] Set one consistent 0.2.0 version and real author/project URLs.
- [ ] Add `dmr = ["django-modern-rest>=0.12"]` without top-level import.
- [ ] Add a serializer backend only to development dependencies so real DMR
      Controller/OpenAPI tests run while the public `dmr` extra stays exact.
- [ ] Set exact runtime bounds: Python `>=3.10,<3.15`, Django `>=5.2,<6.1`, DRF `>=3.17,<3.18`,
      Knox `>=5.1,<6`, and django-redis `>=7,<8`; modernize to `license = "MIT"`.
- [ ] Add PEP 561 marker/package classifier and retain it in built artifacts.
- [ ] Use the exact core matrix: Django 5.2 on Python 3.10-3.14 and Django 6.0
      on Python 3.12-3.14.
- [ ] Use the exact DMR matrix: `(3.11,5.2,==0.12.0)`,
      `(3.12,6.0,==0.12.0)`, `(3.14,5.2,>=0.12)`, and
      `(3.14,6.0,>=0.12)`.
- [ ] Install matrix constraints once and execute with `--no-sync` or the venv
      interpreter; print/assert actual versions.
- [ ] Stop ignoring `uv.lock`, refresh it, and use it for reproducible lint/build
      jobs with `--locked`; matrix jobs use fresh one-shot constrained venvs and
      never run a later synchronizing `uv run`.
- [ ] Limit the all-extras development lock to Python 3.11-3.14 because DMR does
      not support 3.10; keep base 3.10 coverage in its fresh core matrix job.
- [ ] Verify base import without DMR.
- [ ] Pin uv in both workflows, correct the PyPI environment URL, and make future
      publish jobs validate exact artifacts before upload/publication.

### Task 6: Replace unsupported claims and document operations

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] Remove 22x/95%/7x, synthetic throughput, and indefinite-cache claims.
- [ ] Document measured hit/miss SQL and Redis operation counts.
- [ ] Document fail-closed revocation, TTL calculation, outages, and AUTO_REFRESH
      bypass.
- [ ] Add DRF and DMR installation/configuration, required/optional chain,
      database-auth, OpenAPI, and no-native-async examples.
- [ ] Correct badges, branch/repository/changelog URLs, author, and release history.

## Chunk 4: Integrated verification and review

### Task 7: Run release gates

**Files:** no intended source changes unless a gate exposes a defect.

- [ ] Run `.venv/bin/python -m pytest` with all installed extras.
- [ ] Run `.venv/bin/ruff check .`.
- [ ] Run `.venv/bin/ruff format --check .`.
- [ ] Run mypy for the shipped package API.
- [ ] Run `DJANGO_SETTINGS_MODULE=tests.settings .venv/bin/python -m django check`.
- [ ] Build 0.2.0 into a fresh temporary output directory.
- [ ] Run pinned Twine metadata checks; assert version 0.2.0, the DMR extra and
      conditional requirement, `dmr.py`/`py.typed` in the wheel, and correct sdist
      contents.
- [ ] Smoke-test the built wheel in fresh environments both without DMR and with
      the DMR extra so the source checkout cannot shadow the artifact.
- [ ] Run `git diff --check`, inspect `git diff`, and inspect
      `git status --short --untracked-files=all` plus every untracked file.
- [ ] Confirm `HEAD` is unchanged and no push/tag/release occurred.

### Task 8: Independent final review

- [ ] Review security invariants, auth semantics, test gaps, documentation, and
      packaging independently.
- [ ] Fix confirmed findings within the approved scope.
- [ ] Repeat affected gates and prepare the requested six-part final report.
