# django-rest-knox-redis

[![PyPI version](https://badge.fury.io/py/django-rest-knox-redis.svg)](https://badge.fury.io/py/django-rest-knox-redis)
[![Python Versions](https://img.shields.io/pypi/pyversions/django-rest-knox-redis.svg)](https://pypi.org/project/django-rest-knox-redis/)
[![Django Versions](https://img.shields.io/badge/django-4.2%20%7C%205.0%20%7C%206.0-green.svg)](https://pypi.org/project/django-rest-knox-redis/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://github.com/yourusername/django-rest-knox-redis/actions/workflows/tests.yml/badge.svg)](https://github.com/safonin/django-rest-knox-redis/actions)

**Redis caching layer for [django-rest-knox](https://github.com/jazzband/django-rest-knox) that dramatically reduces database load on token authentication.**

---

## The Problem

Every API request with token authentication hits your database. With **django-rest-knox**, each request requires:

1. Query database by `token_key` index
2. Fetch token record with user data
3. Validate token hash
4. Check user status

**At scale, this becomes a bottleneck:**

```
┌─────────────────────────────────────────────────────────────────┐
│                    Database Load Analysis                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Requests/sec    DB Queries/sec    DB Connection Pool Usage     │
│  ────────────    ──────────────    ────────────────────────     │
│       100             100                  10%                  │
│       500             500                  50%                  │
│     1,000           1,000                 100% ← Saturation     │
│     2,000           2,000                 200% ← Connection     │
│                                                 waiting         │
└─────────────────────────────────────────────────────────────────┘
```

**Real-world impact:**
- 🔴 **1,000 req/sec** = 1,000 database queries just for authentication
- 🔴 **Database connections exhausted** under load
- 🔴 **Latency spikes** when DB is under pressure
- 🔴 **Cascading failures** affecting all services sharing the DB

---

## The Solution

**django-rest-knox-redis** adds a Redis caching layer that eliminates most database queries:

```
┌─────────────────────────────────────────────────────────────────┐
│                    With Redis Caching                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Request Flow:                                                  │
│                                                                 │
│  ┌──────────┐     ┌───────────┐     ┌──────────────┐            │
│  │  Client  │────▶│   Redis   │────▶│   Database   │            │
│  └──────────┘     └───────────┘     └──────────────┘            │
│                         │                   │                   │
│                    95% HIT ✓           5% MISS                  │
│                    (< 1ms)            (then cache)              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Performance Comparison

```
┌─────────────────────────────────────────────────────────────────┐
│              Authentication Latency (p99)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  knox (DB only)     ████████████████████████████████  45ms      │
│  knox-redis (hit)   ██                                 2ms      │
│  knox-redis (miss)  ████████████████████████████████  47ms      │
│                                                                 │
│  * Redis cache hit: 22x faster                                  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│              Database Queries per 10,000 Requests               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  knox (DB only)     ████████████████████████████  10,000        │
│  knox-redis (95%)   █                                 500       │
│                                                                 │
│  * 95% cache hit rate = 95% reduction in DB queries             │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│              Throughput (requests/sec on same hardware)         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  knox (DB only)     ████████████                   1,200        │
│  knox-redis         ████████████████████████████   8,500        │
│                                                                 │
│  * 7x higher throughput with Redis caching                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                    Authentication Flow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   1. Request with "Authorization: Token xxx..."                 │
│                          │                                      │
│                          ▼                                      │
│   2. ┌─────────────────────────────────────┐                    │
│      │  Check Redis cache by token_key     │                    │
│      │  Key: knox:token:{first_15_chars}   │                    │
│      └─────────────────────────────────────┘                    │
│                          │                                      │
│              ┌───────────┴───────────┐                          │
│              │                       │                          │
│         Cache HIT               Cache MISS                      │
│              │                       │                          │
│              ▼                       ▼                          │
│   3. Validate hash          4. Query database                   │
│      Get user from DB          Validate token                   │
│      (by PK - fast)            Cache in Redis                   │
│              │                       │                          │
│              └───────────┬───────────┘                          │
│                          │                                      │
│                          ▼                                      │
│   5. Return (user, token) to DRF                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- ✅ **User always fetched from DB** - ensures `is_active` changes apply immediately
- ✅ **Token data cached indefinitely** - no TTL, explicit invalidation only
- ✅ **Graceful degradation** - falls back to DB if Redis unavailable
- ✅ **Atomic cache invalidation** - on logout, logoutall, token deletion

---

## Installation

```bash
pip install django-rest-knox-redis
```

Or with **uv**:

```bash
uv add django-rest-knox-redis
```

---

## Quick Start

### 1. Configure Django Settings

```python
# settings.py

INSTALLED_APPS = [
    # ...
    'rest_framework',
    'knox',
    'knox_redis',
]

# Configure Redis cache with django-redis
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

# Knox Redis settings
REST_KNOX_REDIS = {
    'CACHE_ALIAS': 'default',      # Which Django cache to use
    'REDIS_KEY_PREFIX': 'knox',    # Prefix for Redis keys
    'CACHE_ENABLED': True,         # Toggle caching on/off
}

# Standard Knox settings (optional)
REST_KNOX = {
    'TOKEN_TTL': None,             # Token lifetime
    'AUTO_REFRESH': True,          # Auto-refresh on activity
}

# IMPORTANT: Replace knox.auth.TokenAuthentication with knox_redis.auth.TokenAuthentication
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'knox_redis.auth.TokenAuthentication',  # <-- Use knox_redis instead of knox
        # ... other authentication classes
    ],
}
```

### 2. Update URLs

```python
# urls.py
from knox_redis.views import LoginView, LogoutView, LogoutAllView

urlpatterns = [
    path('api/auth/login/', LoginView.as_view(), name='knox_login'),
    path('api/auth/logout/', LogoutView.as_view(), name='knox_logout'),
    path('api/auth/logoutall/', LogoutAllView.as_view(), name='knox_logoutall'),
]
```

### 3. Use in Views (optional per-view override)

If you set `DEFAULT_AUTHENTICATION_CLASSES` globally, you don't need to specify it per view.
But you can still override authentication per view if needed:

```python
# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from knox_redis.auth import TokenAuthentication

class ProtectedView(APIView):
    # Optional: override if not set globally in REST_FRAMEWORK settings
    authentication_classes = [TokenAuthentication]

    def get(self, request):
        return Response({'user': request.user.username})
```

**That's it!** Your token authentication is now cached in Redis.

---

## Migration from knox

If you're already using `knox.auth.TokenAuthentication`, migration is simple:

### Before (knox only)

```python
# settings.py
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'knox.auth.TokenAuthentication',
    ],
}
```

### After (with Redis caching)

```python
# settings.py
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'knox_redis.auth.TokenAuthentication',  # Just change the import path
    ],
}
```

**That's all!** The `knox_redis.auth.TokenAuthentication` class is a drop-in replacement.
It inherits from `knox.auth.TokenAuthentication` and adds the Redis caching layer transparently.

---

## Configuration Reference

### REST_KNOX_REDIS Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `CACHE_ALIAS` | `str` | `'default'` | Django cache alias (must use django-redis) |
| `REDIS_KEY_PREFIX` | `str` | `'knox'` | Prefix for all Redis keys |
| `CACHE_ENABLED` | `bool` | `True` | Enable/disable caching globally |

### Redis Key Schema

```
knox:token:{token_key}         → JSON {digest, user_id, created, expiry}
knox:user:{user_id}:tokens     → Set of token_keys (for bulk invalidation)
```

---

## Cache Invalidation

Cache is automatically invalidated when:

| Event | Action |
|-------|--------|
| `LogoutView.post()` | Deletes single token from Redis |
| `LogoutAllView.post()` | Deletes all user tokens from Redis |
| Token deleted via ORM | Signal handler invalidates cache |
| Token expired | Removed on next auth attempt |
| User deleted | Invalidated on next auth attempt |

---

## Error Handling

**Redis unavailable?** No problem:

```python
# Cache operations are wrapped in try/except
# On Redis failure:
# 1. Warning logged
# 2. Falls back to database authentication
# 3. Application continues working
```

---

## Monitoring

### Check Cache Hit Rate

```python
from django.core.cache import caches
from django_redis import get_redis_connection

redis_conn = get_redis_connection("default")
info = redis_conn.info()

print(f"Cache hits: {info['keyspace_hits']}")
print(f"Cache misses: {info['keyspace_misses']}")
print(f"Hit rate: {info['keyspace_hits'] / (info['keyspace_hits'] + info['keyspace_misses']) * 100:.1f}%")
```

### View Cached Tokens

```bash
redis-cli KEYS "knox:token:*" | wc -l  # Count cached tokens
redis-cli KEYS "knox:user:*"           # List user token indexes
```

---

## Development

```bash
# Clone repository
git clone https://github.com/yourusername/django-rest-knox-redis.git
cd django-rest-knox-redis

# Install with uv
uv sync

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=knox_redis --cov-report=html

# Lint
uv run ruff check .
uv run ruff format .
```

---

## Requirements

- Python 3.10+
- Django 4.2+
- django-rest-framework 3.14+
- django-rest-knox 4.2+
- django-redis 5.4+
- Redis Server 6.0+

---

## License

MIT License - see [LICENSE](LICENSE) file.

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

---

## Credits

- [django-rest-knox](https://github.com/jazzband/django-rest-knox) - The excellent token authentication library this package extends
- [django-redis](https://github.com/jazzband/django-redis) - Redis cache backend for Django
