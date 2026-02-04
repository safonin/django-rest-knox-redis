"""
Pytest fixtures for knox_redis tests.
"""

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def user(db):
    """Create a test user."""
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
    )


@pytest.fixture
def inactive_user(db):
    """Create an inactive test user."""
    return User.objects.create_user(
        username="inactiveuser",
        email="inactive@example.com",
        password="testpass123",
        is_active=False,
    )


@pytest.fixture
def auth_token(user):
    """Create an auth token for the test user."""
    from knox.models import AuthToken

    instance, token = AuthToken.objects.create(user=user)
    return instance, token


@pytest.fixture
def mock_redis_client(mocker):
    """Mock Redis client for testing without real Redis."""
    import fakeredis

    fake_redis = fakeredis.FakeStrictRedis()

    def get_fake_client():
        return fake_redis

    mocker.patch(
        "knox_redis.cache.TokenCache._get_redis_client",
        side_effect=get_fake_client,
    )

    return fake_redis


@pytest.fixture
def cache_enabled(settings):
    """Ensure cache is enabled for tests."""
    settings.REST_KNOX_REDIS = {
        "CACHE_ALIAS": "default",
        "REDIS_KEY_PREFIX": "knox_test",
        "CACHE_ENABLED": True,
    }
    # Reload settings
    from knox_redis.settings import knox_redis_settings

    knox_redis_settings.reload()
    yield
    knox_redis_settings.reload()


@pytest.fixture
def cache_disabled(settings):
    """Disable cache for tests."""
    settings.REST_KNOX_REDIS = {
        "CACHE_ALIAS": "default",
        "REDIS_KEY_PREFIX": "knox_test",
        "CACHE_ENABLED": False,
    }
    from knox_redis.settings import knox_redis_settings

    knox_redis_settings.reload()
    yield
    knox_redis_settings.reload()
