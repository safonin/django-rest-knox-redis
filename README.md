# django-rest-knox-redis

[![PyPI](https://img.shields.io/pypi/v/django-rest-knox-redis.svg)](https://pypi.org/project/django-rest-knox-redis/)
[![Python](https://img.shields.io/pypi/pyversions/django-rest-knox-redis.svg)](https://pypi.org/project/django-rest-knox-redis/)
[![Django](https://img.shields.io/badge/Django-5.2%20%7C%206.0-0C4B33.svg)](https://www.djangoproject.com/)
[![Tests](https://github.com/safonin/django-rest-knox-redis/actions/workflows/tests.yml/badge.svg)](https://github.com/safonin/django-rest-knox-redis/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A bounded Redis lookup layer for
[django-rest-knox](https://github.com/jazzband/django-rest-knox). It preserves
Knox's `AuthToken` request contract, makes revocation database-authoritative,
and offers an optional synchronous
[django-modern-rest](https://github.com/wemake-services/django-modern-rest)
adapter.

## Security and consistency model

Redis is an acceleration hint, never the source of truth:

1. A cache hit verifies the raw token digest against the cached digest.
2. Authentication then selects the exact token and its user from the database.
3. `request.auth` and `request._auth` receive the real Knox `AuthToken` model,
   so Knox logout and application code can call model methods normally.
4. Token deletion invalidates Redis in `pre_delete`. If Redis cannot confirm
   invalidation, deletion fails closed and the surrounding database operation
   is rolled back. A best-effort `on_commit` deletion closes the
   invalidate-before-commit race.

This deliberately trades revocation availability for consistency: logout,
logout-all, admin deletion, ORM deletion, expiry cleanup, or a user cascade can
return/raise a 503-class `CacheInvalidationError` while Redis is unavailable.
Ordinary authentication can still fall back to Knox's database path when a
Redis read or cache population fails. A stale entry left by a failed
post-commit cleanup cannot authenticate because every hit rechecks the token
row.

All cache entries are bounded:

- finite Knox tokens use `min(remaining token lifetime,
  MAX_TOKEN_CACHE_TTL)`;
- `TOKEN_TTL=None` uses `MAX_TOKEN_CACHE_TTL` as the mandatory revalidation
  bound;
- entries with less than one whole second remaining are not cached;
- the default maximum is 300 seconds.

When Knox `AUTO_REFRESH=True`, token caching is disabled and authentication is
delegated to Knox. This preserves `MIN_REFRESH_INTERVAL` and
`AUTO_REFRESH_MAX_TTL` semantics without maintaining a competing Redis expiry.

## Measured authentication operations

These counts are regression-tested with Django 6.0, django-rest-knox 5.1, one
valid token, and `AUTO_REFRESH=False`. They count all SQL, not only token-model
manager calls.

| Path | Redis commands / round trips | SQL queries | Result |
| --- | --- | ---: | --- |
| Cache hit | `GET` / 1 | 1 | Joined exact token + user row |
| Cache miss | `GET`, then pipelined `SET`, `SADD`, `EXPIREAT` / 2 | 2 | Knox lookup + Knox expired-sibling cleanup query, then cache population |
| `AUTO_REFRESH=True` | none | Knox-owned | Two reads for the simple valid-token case, plus an `UPDATE` only when Knox's refresh interval permits |

The cache therefore reduces the tested valid-token path from two SQL queries
to one; it does not eliminate database authentication queries. Previous
unreproducible claims such as “22x faster”, “95% fewer queries”, and “7x more
throughput” are intentionally not claimed. Measure end-to-end behavior against
your database, Redis topology, user model, and token population.

## Compatibility

The base package supports:

| Python | Django |
| --- | --- |
| 3.10–3.14 | 5.2 |
| 3.12–3.14 | 6.0 |

Runtime bounds are DRF 3.16.1–3.17, django-rest-knox 5.0.4–5.1, and
django-redis 6–7. The CI matrix installs each supported Python/Django
combination into a fresh environment, includes an explicit lower-bound job for
DRF 3.16.1, Knox 5.0.4, and django-redis 6.0.0, and asserts the resolved
versions. The optional DMR extra requires Python 3.11 or newer through
django-modern-rest.

## Installation

DRF only:

```bash
python -m pip install django-rest-knox-redis
```

With optional DMR integration:

```bash
python -m pip install 'django-rest-knox-redis[dmr]'
```

The extra is exactly `django-modern-rest>=0.12`; importing the base package
does not import or require DMR. The example below uses DMR's optional msgspec
serializer plugin, so install its backend too:

```bash
python -m pip install 'django-rest-knox-redis[dmr]' 'django-modern-rest[msgspec]'
```

## DRF configuration

Add the applications and a django-redis cache:

```python
# settings.py
from datetime import timedelta

INSTALLED_APPS = [
    # ...
    "rest_framework",
    "knox",
    "knox_redis",
]

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    },
}

REST_KNOX = {
    "TOKEN_TTL": timedelta(hours=10),
    "AUTO_REFRESH": False,
}

REST_KNOX_REDIS = {
    "CACHE_ALIAS": "default",
    "REDIS_KEY_PREFIX": "knox",
    "CACHE_ENABLED": True,
    "MAX_TOKEN_CACHE_TTL": 300,
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "knox_redis.auth.TokenAuthentication",
    ],
}
```

Use the Redis-aware Knox views so their full request authentication flow uses
the same authenticator:

```python
# urls.py
from django.urls import path
from knox_redis.views import LoginView, LogoutAllView, LogoutView

urlpatterns = [
    path("api/auth/login/", LoginView.as_view(), name="knox_login"),
    path("api/auth/logout/", LogoutView.as_view(), name="knox_logout"),
    path("api/auth/logout-all/", LogoutAllView.as_view(), name="knox_logout_all"),
]
```

The authenticator is also available per view:

```python
from knox_redis.auth import TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView


class ProtectedView(APIView):
    authentication_classes = (TokenAuthentication,)

    def get(self, request):
        return Response({"username": request.user.get_username()})
```

### Settings

| Setting | Type | Default | Meaning |
| --- | --- | --- | --- |
| `CACHE_ALIAS` | `str` | `"default"` | Django cache alias backed by django-redis |
| `REDIS_KEY_PREFIX` | `str` | `"knox"` | Prefix for token and user-index keys |
| `CACHE_ENABLED` | `bool` | `True` | Enables Redis reads, population, and invalidation |
| `MAX_TOKEN_CACHE_TTL` | positive `int` | `300` | Maximum seconds before database revalidation, including non-expiring Knox tokens |

Settings are reloadable with Django's `override_settings` and preserve the
0.1.x names and defaults, except that cache entries are now always bounded.

### Redis keys

```text
knox:token:{token_key}      JSON schema v2: digest, user_id, created, expiry, token_key
knox:user:{user_id}:tokens  bounded set of cached token keys
```

Unknown, old, malformed, or mismatched cache payloads are treated as misses.
The prefix is configurable for multi-application Redis deployments.

## django-modern-rest integration

The public adapters implement DMR `SyncAuth`:

- `knox_redis.dmr.KnoxRedisSyncAuth` uses the hardened Redis-aware
  authenticator;
- `knox_redis.dmr.KnoxDatabaseSyncAuth` always uses Knox's database
  authenticator.

Both return a real Knox `AuthToken` and populate `request.user`,
`request.auth`, `request._auth`, and `request.auser`. They preserve Knox header
parsing for `Authorization: Token <token>` and translate DRF authentication
errors into DMR responses with `WWW-Authenticate: Token` instead of leaking a
500.

Required authentication:

```python
from dmr import Controller
from dmr.plugins.msgspec import MsgspecSerializer
from knox_redis.dmr import KnoxRedisSyncAuth


class ProtectedController(Controller[MsgspecSerializer]):
    auth = (KnoxRedisSyncAuth(required=True),)
```

Optional authentication keeps DMR alternative order. With `required=False`
(the default), missing credentials or another scheme returns `None` so the
next authenticator can run; recognized malformed or invalid Knox credentials
remain terminal 401 responses:

```python
class OptionalController(Controller[MsgspecSerializer]):
    auth = (KnoxRedisSyncAuth(), MyAnonymousOrOtherSyncAuth())
```

OpenAPI describes Knox as `type: apiKey`, `name: Authorization`, `in: header`;
it is not represented as a Bearer scheme.

Native DMR `AsyncAuth` is not implemented. The adapters call synchronous Knox
and Django ORM APIs and must be used through DMR's synchronous authentication
path.

## Known limitations

- Cache hits validate the presented token but do not run Knox's opportunistic
  cleanup loop for other expired tokens owned by the same user. Those rows are
  removed when presented, by logout/admin/ORM cleanup, or by an application
  maintenance task.
- DRF and DMR translate `CacheInvalidationError` to 503. Direct ORM callers
  receive the exception, while uncustomized Django admin error handling renders
  it as a 500 even though the deletion transaction is safely rolled back.
- There is no native DMR async authenticator.

## Operations

- Monitor Redis `keyspace_hits`, `keyspace_misses`, command latency, and the
  application's `CacheInvalidationError`/503 rate.
- Alert on invalidation failures: they intentionally block revocation.
- Do not use `KEYS` in production. Inspect a prefix with `SCAN`, for example
  `redis-cli --scan --pattern 'knox:token:*'`.
- Keep Redis access restricted; cached data includes token digests and user
  identifiers, never raw tokens.

## Development and release checks

```bash
uv sync --locked --all-extras
uv run --locked --all-extras pytest
uv run --locked --all-extras ruff check .
uv run --locked --all-extras ruff format --check .
uv run --locked --all-extras mypy knox_redis
DJANGO_SETTINGS_MODULE=tests.settings uv run --locked --all-extras django-admin check
uv build --no-build-isolation
uv run --locked --all-extras twine check dist/*
```

See [CHANGELOG.md](CHANGELOG.md) for release notes. The package is licensed
under the [MIT License](LICENSE).
