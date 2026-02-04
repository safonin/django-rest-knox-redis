"""
TokenAuthentication with Redis caching layer.

Drop-in replacement for knox.auth.TokenAuthentication.
"""

import binascii
import logging
from datetime import datetime
from hmac import compare_digest

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from knox.crypto import hash_token
from knox.models import get_token_model
from knox.settings import CONSTANTS, knox_settings
from knox.signals import token_expired
from rest_framework import exceptions

from knox_redis.cache import TokenCache
from knox_redis.settings import knox_redis_settings

logger = logging.getLogger(__name__)

User = get_user_model()


class CachedAuthToken:
    """
    Lightweight object mimicking AuthToken for cached authentication.

    Used instead of fetching AuthToken from database when token is in cache.
    Contains all necessary attributes for DRF request._auth.
    """

    def __init__(self, cached_data: dict, user):
        self.digest = cached_data["digest"]
        self.user = user
        self.user_id = cached_data["user_id"]
        self.created = datetime.fromisoformat(cached_data["created"])
        if cached_data["expiry"]:
            expiry = datetime.fromisoformat(cached_data["expiry"])
            if timezone.is_naive(expiry):
                expiry = timezone.make_aware(expiry)
            self.expiry = expiry
        else:
            self.expiry = None
        self.token_key = cached_data["token_key"]


class TokenAuthentication(KnoxTokenAuthentication):
    """
    Knox TokenAuthentication with Redis caching layer.

    Authentication flow:
    1. Check Redis cache for token
    2. If found in cache and valid, authenticate using cached data
    3. If not in cache, fall back to database lookup
    4. If found in database, cache the token for future requests

    This is a drop-in replacement for knox.auth.TokenAuthentication.

    Usage:
        from knox_redis.auth import TokenAuthentication

        class MyView(APIView):
            authentication_classes = [TokenAuthentication]
    """

    def authenticate_credentials(self, token):
        """
        Authenticate the token with Redis cache layer.

        Overrides knox.auth.TokenAuthentication.authenticate_credentials()
        """
        msg = _("Invalid token.")
        token = token.decode("utf-8")
        token_key = token[: CONSTANTS.TOKEN_KEY_LENGTH]

        logger.debug(f"Authenticating token with key prefix: {token_key[:8]}...")

        # Step 1: Check Redis cache first
        if knox_redis_settings.CACHE_ENABLED:
            cached_data = TokenCache.get_token(token_key)
            if cached_data:
                logger.debug("Token found in Redis cache, validating...")
                result = self._authenticate_from_cache(token, cached_data, msg)
                if result:
                    logger.debug("Token authenticated from cache")
                    return result
                logger.debug("Cache validation failed, falling back to DB")
                # If cache validation failed (expired/invalid), continue to DB
        else:
            logger.debug("Redis cache is disabled")

        # Step 2: Fall back to database lookup (original knox behavior)
        logger.debug("Authenticating from database...")
        return self._authenticate_from_database(token, token_key, msg)

    def _authenticate_from_cache(self, token: str, cached_data: dict, msg: str):
        """
        Validate token using cached data.

        No database queries to AuthToken - uses CachedAuthToken instead.
        Only queries User model to check is_active status.

        Returns:
            Tuple of (user, auth_token) if valid, None if invalid/expired
        """
        try:
            digest = hash_token(token)
        except (TypeError, binascii.Error):
            return None

        # Verify digest matches
        if not compare_digest(digest, cached_data["digest"]):
            return None

        # Check expiry
        if cached_data["expiry"]:
            expiry = datetime.fromisoformat(cached_data["expiry"])
            if timezone.is_naive(expiry):
                expiry = timezone.make_aware(expiry)

            if expiry < timezone.now():
                # Token expired - invalidate cache
                TokenCache.delete_token(
                    token[: CONSTANTS.TOKEN_KEY_LENGTH], cached_data["user_id"]
                )
                return None

        # Fetch user from database (always fresh to check is_active)
        try:
            user = User.objects.get(pk=cached_data["user_id"])
        except User.DoesNotExist:
            # User deleted - invalidate cache
            TokenCache.delete_token(
                token[: CONSTANTS.TOKEN_KEY_LENGTH], cached_data["user_id"]
            )
            return None

        if not user.is_active:
            raise exceptions.AuthenticationFailed(_("User inactive or deleted."))

        # Create CachedAuthToken instead of fetching from database
        # This avoids the DB query that defeats the purpose of caching
        auth_token = CachedAuthToken(cached_data, user)

        # Note: AUTO_REFRESH is not supported for cached tokens
        # as it would require a database write, defeating the caching purpose.
        # Tokens will be refreshed when authenticated from database.

        return (user, auth_token)

    def _authenticate_from_database(self, token: str, token_key: str, msg: str):
        """
        Original knox database authentication with caching on success.

        This is the original knox authenticate_credentials() logic
        with added caching on successful authentication.
        """
        for auth_token in (
            get_token_model().objects.filter(token_key=token_key).select_related("user")
        ):
            if self._cleanup_token(auth_token):
                continue

            try:
                digest = hash_token(token)
            except (TypeError, binascii.Error) as e:
                raise exceptions.AuthenticationFailed(msg) from e

            if compare_digest(digest, auth_token.digest):
                if knox_settings.AUTO_REFRESH and auth_token.expiry:
                    self.renew_token(auth_token)

                # Cache the token for future requests
                if knox_redis_settings.CACHE_ENABLED:
                    logger.debug(f"Caching token for user {auth_token.user_id}")
                    cache_result = TokenCache.set_token(auth_token)
                    logger.debug(f"Token cache result: {cache_result}")

                return self.validate_user(auth_token)

        raise exceptions.AuthenticationFailed(msg)

    def _cleanup_token(self, auth_token) -> bool:
        """
        Check if token is expired and clean up.

        Override to also clean up Redis cache when tokens expire.

        Returns:
            True if the token has expired and been deleted
        """
        # Clean up other expired tokens for the user
        for other_token in auth_token.user.auth_token_set.all():
            if other_token.digest != auth_token.digest and other_token.expiry:
                if other_token.expiry < timezone.now():
                    # Invalidate from cache
                    if knox_redis_settings.CACHE_ENABLED:
                        TokenCache.delete_token(
                            other_token.token_key, other_token.user_id
                        )
                    other_token.delete()
                    token_expired.send(
                        sender=self.__class__,
                        username=other_token.user.get_username(),
                        source="other_token",
                    )

        # Check if the current token is expired
        if auth_token.expiry is not None:
            if auth_token.expiry < timezone.now():
                username = auth_token.user.get_username()
                # Invalidate from cache
                if knox_redis_settings.CACHE_ENABLED:
                    TokenCache.delete_token(auth_token.token_key, auth_token.user_id)
                auth_token.delete()
                token_expired.send(
                    sender=self.__class__,
                    username=username,
                    source="auth_token",
                )
                return True

        return False
