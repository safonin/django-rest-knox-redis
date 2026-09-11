"""
Django settings for tests.
"""

import os

SECRET_KEY = "test-secret-key-for-knox-redis"

DEBUG = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "knox",
    "knox_redis",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Use fakeredis for testing
CACHES: dict[str, dict[str, object]] = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

if redis_test_url := os.environ.get("KNOX_REDIS_TEST_URL"):
    CACHES["default"] = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": redis_test_url,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "knox_redis.auth.TokenAuthentication",
    ],
}

REST_KNOX = {
    "TOKEN_TTL": None,  # No expiry for tests
}

REST_KNOX_REDIS = {
    "CACHE_ALIAS": "default",
    "REDIS_KEY_PREFIX": "knox_test",
    "CACHE_ENABLED": True,
}

USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

ROOT_URLCONF = "tests.urls"
