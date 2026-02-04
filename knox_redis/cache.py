"""
Redis cache operations for knox tokens.

Uses django-redis through Django's cache framework.
"""

import json
import logging
from typing import Any

from django.core.cache import caches

from knox_redis.settings import knox_redis_settings

logger = logging.getLogger(__name__)


class TokenCache:
    """
    Redis cache operations for knox tokens.

    Key schema:
        knox:token:{token_key} -> JSON {digest, user_id, created, expiry}
        knox:user:{user_id}:tokens -> Set of token_keys

    Uses django-redis through Django cache framework.
    On Redis errors, logs warning and returns None / continues silently.
    """

    @classmethod
    def _get_cache(cls):
        """Get the configured cache backend."""
        return caches[knox_redis_settings.CACHE_ALIAS]

    @classmethod
    def _get_redis_client(cls):
        """
        Get the raw redis client from django-redis.

        Returns None if not available.
        """
        try:
            cache = cls._get_cache()
            # django-redis provides client.get_client() method
            if not hasattr(cache, "client"):
                logger.warning(
                    f"Cache backend {type(cache).__name__} does not have 'client' attribute. "
                    "Make sure you're using django-redis as your cache backend."
                )
                return None
            return cache.client.get_client()
        except Exception as e:
            logger.warning(f"Failed to get Redis client: {e}")
            return None

    @classmethod
    def _make_token_key(cls, token_key: str) -> str:
        """Generate Redis key for a token."""
        prefix = knox_redis_settings.REDIS_KEY_PREFIX
        return f"{prefix}:token:{token_key}"

    @classmethod
    def _make_user_tokens_key(cls, user_id: Any) -> str:
        """Generate Redis key for user's token set."""
        prefix = knox_redis_settings.REDIS_KEY_PREFIX
        return f"{prefix}:user:{user_id}:tokens"

    @classmethod
    def get_token(cls, token_key: str) -> dict | None:
        """
        Retrieve token data from Redis cache.

        Args:
            token_key: First 15 characters of the token (knox token_key)

        Returns:
            Dict with {digest, user_id, created, expiry, token_key} or None if not found
        """
        if not knox_redis_settings.CACHE_ENABLED:
            return None

        try:
            client = cls._get_redis_client()
            if client is None:
                return None

            redis_key = cls._make_token_key(token_key)
            data = client.get(redis_key)

            if data is None:
                return None

            # Data stored as JSON string
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            return json.loads(data)

        except Exception as e:
            logger.warning(f"Redis get_token failed: {e}")
            return None

    @classmethod
    def set_token(cls, auth_token) -> bool:
        """
        Cache an AuthToken instance in Redis.

        Also maintains the user's token index for efficient logout-all.

        Args:
            auth_token: Knox AuthToken model instance

        Returns:
            True if cached successfully, False otherwise
        """
        if not knox_redis_settings.CACHE_ENABLED:
            logger.debug("Knox Redis cache is disabled, skipping set_token")
            return False

        try:
            client = cls._get_redis_client()
            if client is None:
                logger.warning("Redis client is None, cannot cache token")
                return False

            token_key = auth_token.token_key
            redis_key = cls._make_token_key(token_key)
            user_tokens_key = cls._make_user_tokens_key(auth_token.user_id)

            # Prepare token data as JSON
            data = {
                "digest": auth_token.digest,
                "user_id": auth_token.user_id,
                "created": auth_token.created.isoformat(),
                "expiry": auth_token.expiry.isoformat() if auth_token.expiry else None,
                "token_key": auth_token.token_key,
            }

            # Use pipeline for atomic operations
            pipe = client.pipeline()
            pipe.set(redis_key, json.dumps(data))
            pipe.sadd(user_tokens_key, token_key)
            pipe.execute()

            logger.debug(f"Token cached successfully: {redis_key}")
            return True

        except Exception as e:
            logger.warning(f"Redis set_token failed: {e}")
            return False

    @classmethod
    def delete_token(cls, token_key: str, user_id: Any = None) -> bool:
        """
        Remove a specific token from cache.

        Args:
            token_key: First 15 characters of the token
            user_id: If provided, also removes from user's token index

        Returns:
            True if deleted successfully, False otherwise
        """
        if not knox_redis_settings.CACHE_ENABLED:
            return False

        try:
            client = cls._get_redis_client()
            if client is None:
                return False

            redis_key = cls._make_token_key(token_key)

            pipe = client.pipeline()
            pipe.delete(redis_key)

            if user_id is not None:
                user_tokens_key = cls._make_user_tokens_key(user_id)
                pipe.srem(user_tokens_key, token_key)

            pipe.execute()
            return True

        except Exception as e:
            logger.warning(f"Redis delete_token failed: {e}")
            return False

    @classmethod
    def delete_all_user_tokens(cls, user_id: Any) -> bool:
        """
        Remove all tokens for a user from cache.

        Used by LogoutAllView for efficient cache invalidation.

        Args:
            user_id: User's primary key

        Returns:
            True if deleted successfully, False otherwise
        """
        if not knox_redis_settings.CACHE_ENABLED:
            return False

        try:
            client = cls._get_redis_client()
            if client is None:
                return False

            user_tokens_key = cls._make_user_tokens_key(user_id)

            # Get all token keys for this user
            token_keys = client.smembers(user_tokens_key)

            if not token_keys:
                return True

            # Build list of Redis keys to delete
            pipe = client.pipeline()
            for tk in token_keys:
                if isinstance(tk, bytes):
                    tk = tk.decode("utf-8")
                redis_key = cls._make_token_key(tk)
                pipe.delete(redis_key)

            # Also delete the user's token index
            pipe.delete(user_tokens_key)
            pipe.execute()

            return True

        except Exception as e:
            logger.warning(f"Redis delete_all_user_tokens failed: {e}")
            return False

    @classmethod
    def update_token_expiry(cls, token_key: str, new_expiry) -> bool:
        """
        Update expiry field for auto-refresh functionality.

        Args:
            token_key: First 15 characters of the token
            new_expiry: New expiry datetime or None

        Returns:
            True if updated successfully, False otherwise
        """
        if not knox_redis_settings.CACHE_ENABLED:
            return False

        try:
            client = cls._get_redis_client()
            if client is None:
                return False

            redis_key = cls._make_token_key(token_key)

            # Get current data
            data = client.get(redis_key)
            if data is None:
                return False

            if isinstance(data, bytes):
                data = data.decode("utf-8")

            token_data = json.loads(data)
            token_data["expiry"] = new_expiry.isoformat() if new_expiry else None

            # Update with new expiry
            client.set(redis_key, json.dumps(token_data))
            return True

        except Exception as e:
            logger.warning(f"Redis update_token_expiry failed: {e}")
            return False
