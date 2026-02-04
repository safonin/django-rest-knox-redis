"""
Tests for TokenAuthentication with Redis caching.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from knox.models import AuthToken
from rest_framework.exceptions import AuthenticationFailed

from knox_redis.auth import TokenAuthentication
from knox_redis.cache import TokenCache


@pytest.mark.django_db
class TestTokenAuthentication:
    """Tests for TokenAuthentication class."""

    def test_authenticate_from_database_caches_token(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that successful DB authentication caches the token."""
        instance, token = AuthToken.objects.create(user=user)

        # Verify token is not in cache initially
        assert TokenCache.get_token(instance.token_key) is None

        # Authenticate
        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        # Verify authentication worked
        assert result_user == user
        assert result_token == instance

        # Verify token is now cached
        cached = TokenCache.get_token(instance.token_key)
        assert cached is not None
        assert cached["digest"] == instance.digest

    def test_authenticate_from_cache(self, user, mock_redis_client, cache_enabled):
        """Test that cached tokens are used for authentication."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache the token
        TokenCache.set_token(instance)

        # Authenticate with cached token
        auth = TokenAuthentication()

        # Mock database query to verify it's not called for token lookup
        with patch.object(AuthToken.objects, "filter", wraps=AuthToken.objects.filter):
            result_user, result_token = auth.authenticate_credentials(token.encode())

            # The filter should be called only for fetching the token by digest,
            # not for the initial token_key lookup
            assert result_user == user
            assert result_token.digest == instance.digest

    def test_authenticate_invalid_token(self, user, mock_redis_client, cache_enabled):
        """Test authentication fails with invalid token."""
        instance, token = AuthToken.objects.create(user=user)

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(b"invalid_token_string")

    def test_authenticate_expired_token_invalidates_cache(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that expired tokens are removed from cache."""
        # Create token with short expiry, then manually expire it
        instance, token = AuthToken.objects.create(user=user)
        # Manually set expiry to past
        instance.expiry = timezone.now() - timedelta(hours=1)
        instance.save()

        # Cache the expired token
        TokenCache.set_token(instance)

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(token.encode())

        # Verify token is removed from cache
        assert TokenCache.get_token(instance.token_key) is None

    def test_authenticate_inactive_user(
        self, inactive_user, mock_redis_client, cache_enabled
    ):
        """Test authentication fails for inactive user."""
        instance, token = AuthToken.objects.create(user=inactive_user)

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth.authenticate_credentials(token.encode())

        assert "inactive" in str(exc_info.value).lower()

    def test_authenticate_deleted_user_invalidates_cache(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that tokens for deleted users are removed from cache."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache the token
        TokenCache.set_token(instance)

        # Delete the user
        user.delete()

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(token.encode())

        # Verify token is removed from cache
        assert TokenCache.get_token(instance.token_key) is None

    def test_authenticate_with_cache_disabled(self, user, cache_disabled):
        """Test authentication works when cache is disabled."""
        instance, token = AuthToken.objects.create(user=user)

        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        assert result_user == user
        assert result_token == instance

    def test_cache_miss_falls_back_to_database(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that cache miss falls back to database lookup."""
        instance, token = AuthToken.objects.create(user=user)

        # Don't cache the token - simulate cache miss

        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        assert result_user == user
        assert result_token == instance

        # Verify token is now cached after DB lookup
        cached = TokenCache.get_token(instance.token_key)
        assert cached is not None

    def test_wrong_digest_in_cache_falls_back_to_database(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that wrong digest in cache triggers database fallback."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache with wrong digest
        mock_redis_client.set(
            TokenCache._make_token_key(instance.token_key),
            f'{{"digest": "wrongdigest", "user_id": {user.pk}, "created": "2024-01-01T00:00:00", "expiry": null}}',
        )

        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        # Should still authenticate from DB
        assert result_user == user
        assert result_token == instance
