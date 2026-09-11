"""
Tests for TokenCache operations.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from knox_redis.cache import TokenCache
from knox_redis.exceptions import CacheInvalidationError
from knox_redis.settings import knox_redis_settings


@pytest.mark.django_db
class TestTokenCache:
    """Tests for TokenCache class."""

    def test_set_and_get_token(self, auth_token, mock_redis_client, cache_enabled):
        """Test setting and getting a token from cache."""
        instance, token = auth_token

        # Set token in cache
        result = TokenCache.set_token(instance)
        assert result is True

        # Get token from cache
        cached = TokenCache.get_token(instance.token_key)
        assert cached is not None
        assert cached["digest"] == instance.digest
        assert cached["user_id"] == instance.user_id
        assert cached["schema_version"] == 2

        ttl = mock_redis_client.ttl(TokenCache._make_token_key(instance.token_key))
        assert 0 < ttl <= 300

    def test_finite_token_ttl_never_exceeds_database_expiry(
        self, auth_token, mock_redis_client, cache_enabled
    ):
        instance, _ = auth_token
        instance.expiry = timezone.now() + timedelta(seconds=30)
        instance.save(update_fields=("expiry",))

        assert TokenCache.set_token(instance) is True

        ttl = mock_redis_client.ttl(TokenCache._make_token_key(instance.token_key))
        assert 0 < ttl <= 30

        cached_expiry = mock_redis_client.expiretime(
            TokenCache._make_token_key(instance.token_key)
        )
        assert cached_expiry <= int(instance.expiry.timestamp())

    def test_subsecond_token_is_not_cached(
        self, auth_token, mock_redis_client, cache_enabled
    ):
        instance, _ = auth_token
        instance.expiry = timezone.now() + timedelta(milliseconds=500)

        assert TokenCache.set_token(instance) is False
        assert TokenCache.get_token(instance.token_key) is None

    def test_uuid_user_id_is_django_json_serialized(
        self, mock_redis_client, cache_enabled
    ):
        user_id = uuid4()
        token = SimpleNamespace(
            digest="d" * 128,
            user_id=user_id,
            created=timezone.now(),
            expiry=None,
            token_key="a" * 15,
        )

        assert TokenCache.set_token(token) is True
        assert TokenCache.get_token(token.token_key)["user_id"] == str(user_id)

    def test_old_or_corrupt_payload_is_a_cold_miss(
        self, mock_redis_client, cache_enabled
    ):
        key = TokenCache._make_token_key("old-token-key")
        mock_redis_client.set(key, json.dumps({"digest": "old"}))
        assert TokenCache.get_token("old-token-key") is None

        mock_redis_client.set(key, b"not-json")
        assert TokenCache.get_token("old-token-key") is None

    def test_get_nonexistent_token(self, mock_redis_client, cache_enabled):
        """Test getting a token that doesn't exist in cache."""
        cached = TokenCache.get_token("nonexistent123")
        assert cached is None

    def test_delete_token(self, auth_token, mock_redis_client, cache_enabled):
        """Test deleting a token from cache."""
        instance, token = auth_token

        # Set token in cache
        TokenCache.set_token(instance)

        # Delete token
        result = TokenCache.delete_token(instance.token_key, instance.user_id)
        assert result is True

        # Verify token is deleted
        cached = TokenCache.get_token(instance.token_key)
        assert cached is None

    def test_delete_all_user_tokens(self, user, mock_redis_client, cache_enabled):
        """Test deleting all tokens for a user."""
        from knox.models import AuthToken

        # Create multiple tokens
        tokens = []
        for _ in range(3):
            instance, token = AuthToken.objects.create(user=user)
            TokenCache.set_token(instance)
            tokens.append(instance)

        # Verify all tokens are cached
        for instance in tokens:
            assert TokenCache.get_token(instance.token_key) is not None

        # Delete all user tokens
        result = TokenCache.delete_all_user_tokens(user.pk)
        assert result is True

        # Verify all tokens are deleted from cache
        for instance in tokens:
            assert TokenCache.get_token(instance.token_key) is None

    def test_update_token_expiry(self, auth_token, mock_redis_client, cache_enabled):
        """Test updating token expiry in cache."""
        instance, token = auth_token

        # Set token in cache
        TokenCache.set_token(instance)

        # Update expiry
        new_expiry = timezone.now() + timedelta(hours=5)
        result = TokenCache.update_token_expiry(instance.token_key, new_expiry)
        assert result is True

        # Verify expiry is updated
        cached = TokenCache.get_token(instance.token_key)
        assert cached["expiry"] == new_expiry.isoformat()

    def test_cache_disabled(self, auth_token, mock_redis_client, cache_disabled):
        """Test that operations return False/None when cache is disabled."""
        instance, token = auth_token

        assert TokenCache.set_token(instance) is False
        assert TokenCache.get_token(instance.token_key) is None
        assert TokenCache.delete_token(instance.token_key) is False
        assert TokenCache.delete_all_user_tokens(instance.user_id) is False

    def test_invalid_max_cache_ttl_is_rejected(self, settings):
        settings.REST_KNOX_REDIS = {"MAX_TOKEN_CACHE_TTL": 0}
        knox_redis_settings.reload()
        try:
            with pytest.raises(ImproperlyConfigured):
                _ = knox_redis_settings.MAX_TOKEN_CACHE_TTL
        finally:
            knox_redis_settings.reload()

    def test_read_outage_is_a_cold_miss(self, cache_enabled):
        with patch.object(TokenCache, "_get_redis_client", side_effect=OSError("down")):
            assert TokenCache.get_token("a" * 15) is None

    def test_strict_delete_is_fail_closed(self, cache_enabled):
        with (
            patch.object(TokenCache, "_get_redis_client", return_value=None),
            pytest.raises(CacheInvalidationError),
        ):
            TokenCache.delete_token("a" * 15, 1, raise_on_error=True)

    def test_user_token_index(self, user, mock_redis_client, cache_enabled):
        """Test that user token index is properly maintained."""
        from knox.models import AuthToken

        # Create token and cache it
        instance, token = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)

        # Check that token_key is in user's token set
        user_tokens_key = TokenCache._make_user_tokens_key(user.pk)
        members = mock_redis_client.smembers(user_tokens_key)
        assert instance.token_key.encode() in members

        # Delete token and verify it's removed from set
        TokenCache.delete_token(instance.token_key, user.pk)
        members = mock_redis_client.smembers(user_tokens_key)
        assert instance.token_key.encode() not in members

    def test_short_token_does_not_shorten_user_index_ttl(
        self, user, mock_redis_client, cache_enabled
    ):
        from knox.models import AuthToken

        long_token, _ = AuthToken.objects.create(
            user=user,
            expiry=timedelta(minutes=5),
        )
        short_token, _ = AuthToken.objects.create(
            user=user,
            expiry=timedelta(seconds=10),
        )

        assert TokenCache.set_token(long_token)
        assert TokenCache.set_token(short_token)

        index_ttl = mock_redis_client.ttl(TokenCache._make_user_tokens_key(user.pk))
        assert 290 <= index_ttl <= 300
