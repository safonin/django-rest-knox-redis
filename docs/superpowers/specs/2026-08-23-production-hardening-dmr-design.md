# Production hardening and DMR integration design

**Status:** Approved on 2026-08-23. This document remains uncommitted because the
repository owner explicitly prohibited commits, pushes, and releases.

## Goals

- Preserve django-rest-knox parsing, authentication, refresh, and request
  contracts.
- Prevent a successfully revoked token from remaining usable through Redis.
- Bound every authorization-bearing Redis entry by both the Knox token lifetime
  and a configurable revalidation interval.
- Add optional django-modern-rest (DMR) sync authentication without making DMR a
  runtime dependency of the base package.
- Replace unsupported performance and compatibility claims with measured,
  reproducible statements.

## Baseline defects

The pristine 0.1.3 checkout returns a `CachedAuthToken` without `delete()` after
a cache hit, so a full HTTP `LogoutView` request raises `AttributeError`. Redis
entries have no TTL. A failed `pre_delete` invalidation is ignored, allowing the
deleted DB token to authenticate from its stale entry. A hit performs one User
SQL query, while the existing test only patches an AuthToken manager and makes no
assertion. AUTO_REFRESH is skipped for hits; on a throttled miss it can cache the
in-memory expiry Knox intentionally did not persist.

## Consistency model

The database remains authoritative. Revocation is fail-closed:

1. `pre_delete` invalidates the token entry and user index in Redis.
2. A confirmed Redis failure raises `CacheInvalidationError` before the database
   delete.
3. Django's delete transaction aborts, so callers never receive a successful
   revocation while the database token remains valid.
4. Cache-hit authentication confirms the exact token row and user together in
   one SQL query. A stale or concurrently repopulated Redis entry can therefore
   never authorize a token after the database deletion commits.
5. A second best-effort invalidation runs with `transaction.on_commit()` to
   clean an entry repopulated in the pre-delete/commit window. Failure of this
   cleanup is safe because every hit still confirms the database row.
6. A Redis read outage may fall back to Knox's database authentication because
   the database check remains authoritative for both hit and fallback paths.

The availability trade-off is deliberate: logout, admin deletion, and direct ORM
deletion can return/raise a temporary service error during a Redis outage. DRF
logout views translate it to 503, while ORM/admin callers receive a typed
`CacheInvalidationError`. The database transaction rolls back and the token
remains valid because revocation did not complete.

Every token entry also gets a finite TTL. Its timeout is:

```text
min(MAX_TOKEN_CACHE_TTL, floor(token.expiry - now))
```

`MAX_TOKEN_CACHE_TTL` defaults to 300 seconds and applies when Knox
`TOKEN_TTL=None`. It must be a positive finite integer; a computed timeout of
zero or less is not cached. The user-token index receives the full configured
maximum lifetime so a later short-lived token cannot shorten another entry's
index. Cache payload schema version 2 makes old indefinite entries cold misses
after upgrade. User identifiers use Django-aware serialization so UUID and
other non-integer primary keys remain supported.

## DRF authentication and views

Cache hits validate schema and digest, then select the exact cached digest from
the configured Knox token model with its user in one SQL query. The returned
object is therefore the real, authoritatively loaded model row, so swapped token
models, model state, custom fields, and `delete()` retain normal Knox behavior.
Missing rows, changed expiry, deleted users, and inactive users are rejected.
A hit is intentionally one SQL query, not zero.

Cache misses delegate to `knox.auth.TokenAuthentication.authenticate_credentials`
and cache its successful result. The package no longer copies Knox's database
loop, cleanup, signal, or refresh logic.

When `REST_KNOX.AUTO_REFRESH=True`, token caching is disabled for both reads and
writes, including login population. Authentication delegates entirely to Knox
so `MIN_REFRESH_INTERVAL` and `AUTO_REFRESH_MAX_TTL` keep their native semantics.
Existing cache entries are ignored. Knox settings are resolved dynamically so
Django `override_settings` and the Knox reload signal cannot leave stale state.

`LoginView.create_token()` caches the exact instance returned by Knox.
`LogoutView` and `LogoutAllView` delegate their post behavior to Knox and rely on
the single fail-closed signal path, removing duplicate invalidations.

## DMR sync API

`knox_redis.dmr` is importable only when the `dmr` extra is installed and exposes:

- `KnoxRedisSyncAuth`, backed by `knox_redis.auth.TokenAuthentication`.
- `KnoxDatabaseSyncAuth`, backed by Knox's database authenticator for operations
  that require an authoritatively loaded row.

Both subclass DMR `SyncAuth`; no `AsyncAuth` adapter is provided. On success they
set `request.user`, `request.auth`, `request._auth`, and an async `request.auser`
callable. DMR continues to store the provider itself in `request.__dmr_auth__`.

Missing or foreign Authorization schemes return `None` in alternative mode.
Recognized malformed, invalid, expired, or inactive Knox credentials stop the
chain with a DMR `APIError`. `required=True` is a terminal required variant and
must be placed last if other alternatives precede it. `required=False` preserves
optional chains and can be followed by an anonymous authenticator. The adapter
never reorders the DMR sequence.

DMR errors use `controller.format_error`, return 401 (or preserve the deliberate
503 invalidation status) rather than leaking DRF exceptions, and include
`WWW-Authenticate: Token` on 401. OpenAPI declares an `apiKey` named
`Authorization` in the header and preserves DMR's security-requirement order.
Knox is intentionally not represented as HTTP Bearer.

## Supported platform and release metadata

Release version is 0.2.0. Production support is narrowed to maintained common
versions:

- Python 3.10-3.14 for the base package (`>=3.10,<3.15`).
- Django 5.2 and 6.0 (`>=5.2,<6.1`).
- DRF 3.17.x, django-rest-knox 5.1.x, django-redis 7.x.
- DMR `>=0.12` as an optional extra; DMR requires Python 3.11+.

The development lockfile covers Python 3.11-3.14 because it contains the DMR
extra; base Python 3.10 remains supported and is verified by its fresh matrix
environment. The lockfile becomes tracked for reproducible lint/build jobs. CI
uses explicit include matrices installed into fresh venvs and runs the
interpreter in that venv without a later `uv run` resync. Matrix jobs deliberately
resolve their explicit version constraints independently of the lock. DMR tests
cover both 0.12.x and the latest allowed resolution on compatible Python/Django
pairs.

## Verification

Regression coverage includes full HTTP login/logout/logout-all, ORM instance and
queryset deletion, hit/miss SQL totals, finite and unlimited Knox lifetimes,
Redis read outage, invalidation failure, user deletion/inactivity, token expiry,
AUTO_REFRESH throttling and max TTL, DMR parsing/errors/request attributes,
required and optional chains, response headers, and OpenAPI.

The package is PEP 561 typed and includes `py.typed`; mypy joins the final gate.
The remaining gates are full pytest, Ruff lint and format checks, Django system
checks, package build, metadata validation, artifact-content inspection,
tracked-and-untracked Git checks, and explicit confirmation that no commit, push,
tag, or release occurred.
