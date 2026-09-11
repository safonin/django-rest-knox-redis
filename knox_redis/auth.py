"""Knox authentication with bounded Redis digest lookup acceleration."""

import binascii
from hmac import compare_digest
from typing import Any, cast

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from knox import auth as knox_auth_module
from knox import settings as knox_settings_module
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from knox.crypto import hash_token
from knox.models import get_token_model
from knox.settings import CONSTANTS
from rest_framework import exceptions

from knox_redis.cache import CachedTokenData, TokenCache
from knox_redis.settings import knox_redis_settings


def cache_is_enabled() -> bool:
    """Return whether authentication may read or populate token cache entries."""
    return bool(
        knox_redis_settings.CACHE_ENABLED
        and not knox_settings_module.knox_settings.AUTO_REFRESH
    )


def _synchronize_knox_auth_settings() -> None:
    """Keep Knox's imported settings reference current after Django overrides."""
    knox_auth_module.knox_settings = knox_settings_module.knox_settings


class TokenAuthentication(KnoxTokenAuthentication):  # type: ignore[misc]
    """Drop-in Knox authenticator whose hits remain database-authoritative."""

    def authenticate_credentials(self, token: bytes) -> tuple[Any, Any]:
        _synchronize_knox_auth_settings()
        try:
            decoded_token = token.decode("utf-8")
        except UnicodeError as exc:
            raise exceptions.AuthenticationFailed(_("Invalid token.")) from exc
        if not cache_is_enabled():
            return cast(tuple[Any, Any], super().authenticate_credentials(token))

        token_key = decoded_token[: CONSTANTS.TOKEN_KEY_LENGTH]
        cached_data = TokenCache.get_token(token_key)
        if cached_data is not None:
            result = self._authenticate_authoritative_hit(
                token,
                decoded_token,
                token_key,
                cached_data,
            )
            if result is not None:
                return result

        return self._authenticate_database_miss(token)

    def _authenticate_authoritative_hit(
        self,
        raw_token: bytes,
        decoded_token: str,
        token_key: str,
        cached_data: CachedTokenData,
    ) -> tuple[Any, Any] | None:
        try:
            digest = hash_token(decoded_token)
        except (TypeError, binascii.Error):
            return None
        if not compare_digest(digest, cached_data["digest"]):
            return None

        auth_token = (
            get_token_model()
            .objects.select_related("user")
            .filter(digest=cached_data["digest"], token_key=token_key)
            .first()
        )
        if auth_token is None:
            TokenCache.delete_token(token_key, cached_data["user_id"])
            return None
        if str(auth_token.user_id) != str(cached_data["user_id"]):
            TokenCache.delete_token(token_key, cached_data["user_id"])
            return None
        if auth_token.expiry is not None and auth_token.expiry < timezone.now():
            # Delegate Knox's deletion and token_expired signal behavior.
            return cast(
                tuple[Any, Any],
                super().authenticate_credentials(raw_token),
            )
        return cast(tuple[Any, Any], self.validate_user(auth_token))

    def _authenticate_database_miss(self, token: bytes) -> tuple[Any, Any]:
        user, auth_token = cast(
            tuple[Any, Any],
            super().authenticate_credentials(token),
        )
        if cache_is_enabled():
            TokenCache.set_token(auth_token)
        return user, auth_token
