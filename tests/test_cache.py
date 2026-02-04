"""
Tests for TokenCache operations.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from knox_redis.cache import TokenCache


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
