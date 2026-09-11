"""Bounded Redis cache operations for Knox authentication tokens."""

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any, TypedDict, cast

from django.core.cache import caches
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from knox_redis.exceptions import CacheInvalidationError
from knox_redis.settings import knox_redis_settings

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 2


class CachedTokenData(TypedDict):
    """Validated version-2 token cache payload."""

    schema_version: int
    digest: str
    user_id: Any
    created: str
    expiry: str | None
    token_key: str


class TokenCache:
    """Redis operations with bounded entries and explicit invalidation failures."""

    @classmethod
    def _get_cache(cls) -> Any:
        return caches[knox_redis_settings.CACHE_ALIAS]

    @classmethod
    def _get_redis_client(cls) -> Any | None:
        try:
            cache = cls._get_cache()
            if not hasattr(cache, "client"):
                logger.warning(
                    "Cache backend %s is not provided by django-redis.",
                    type(cache).__name__,
                )
                return None
            return cache.client.get_client()
        except Exception as exc:
            logger.warning("Failed to get Redis client: %s", exc)
            return None

    @classmethod
    def _make_token_key(cls, token_key: str) -> str:
        return f"{knox_redis_settings.REDIS_KEY_PREFIX}:token:{token_key}"

    @classmethod
    def _make_user_tokens_key(cls, user_id: Any) -> str:
        return f"{knox_redis_settings.REDIS_KEY_PREFIX}:user:{user_id}:tokens"

    @staticmethod
    def _parse_datetime(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True

    @classmethod
    def _parse_payload(
        cls, raw_data: object, expected_token_key: str
    ) -> CachedTokenData | None:
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")
        if not isinstance(raw_data, str):
            return None

        parsed = json.loads(raw_data)
        if not isinstance(parsed, dict) or parsed.get("schema_version") != 2:
            return None
        if not isinstance(parsed.get("digest"), str):
            return None
        if parsed.get("token_key") != expected_token_key:
            return None
        if not cls._parse_datetime(parsed.get("created")):
            return None
        expiry = parsed.get("expiry")
        if expiry is not None and not cls._parse_datetime(expiry):
            return None
        if "user_id" not in parsed:
            return None
        return CachedTokenData(
            schema_version=CACHE_SCHEMA_VERSION,
            digest=parsed["digest"],
            user_id=parsed["user_id"],
            created=parsed["created"],
            expiry=expiry,
            token_key=parsed["token_key"],
        )

    @classmethod
    def get_token(cls, token_key: str) -> CachedTokenData | None:
        """Return a validated v2 payload, treating all read failures as misses."""
        if not knox_redis_settings.CACHE_ENABLED:
            return None
        try:
            client = cls._get_redis_client()
            if client is None:
                return None
            raw_data = client.get(cls._make_token_key(token_key))
            if raw_data is None:
                return None
            return cls._parse_payload(raw_data, token_key)
        except Exception as exc:
            logger.warning("Redis get_token failed: %s", exc)
            return None

    @classmethod
    def _expires_at_for_expiry(cls, expiry: datetime | None) -> int | None:
        """Return an absolute Redis expiry no later than the DB expiry."""
        maximum = cast(int, knox_redis_settings.MAX_TOKEN_CACHE_TTL)
        now = timezone.now()
        maximum_expiry = now + timedelta(seconds=maximum)
        if expiry is not None and timezone.is_naive(expiry):
            expiry = timezone.make_aware(expiry)
        if expiry is not None and (expiry - now).total_seconds() < 1:
            return None
        effective_expiry = min(expiry, maximum_expiry) if expiry else maximum_expiry
        expires_at = math.floor(effective_expiry.timestamp())
        return expires_at if expires_at > math.floor(now.timestamp()) else None

    @classmethod
    def set_token(cls, auth_token: Any) -> bool:
        """Cache an authoritative token row for no longer than its DB lifetime."""
        if not knox_redis_settings.CACHE_ENABLED:
            return False
        try:
            expires_at = cls._expires_at_for_expiry(auth_token.expiry)
            if expires_at is None:
                return False
            client = cls._get_redis_client()
            if client is None:
                return False

            data: CachedTokenData = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "digest": auth_token.digest,
                "user_id": auth_token.user_id,
                "created": auth_token.created.isoformat(),
                "expiry": (
                    auth_token.expiry.isoformat() if auth_token.expiry else None
                ),
                "token_key": auth_token.token_key,
            }
            redis_key = cls._make_token_key(auth_token.token_key)
            user_tokens_key = cls._make_user_tokens_key(auth_token.user_id)
            pipe = client.pipeline()
            pipe.set(
                redis_key,
                json.dumps(data, cls=DjangoJSONEncoder),
                exat=expires_at,
            )
            pipe.sadd(user_tokens_key, auth_token.token_key)
            # Every token entry is bounded by MAX_TOKEN_CACHE_TTL. Keeping the
            # index for that full window prevents a short-lived token from
            # shortening the index lifetime of another cached token.
            index_expires_at = cls._expires_at_for_expiry(None)
            if index_expires_at is not None:
                pipe.expireat(user_tokens_key, index_expires_at)
            pipe.execute()
            return True
        except Exception as exc:
            logger.warning("Redis set_token failed: %s", exc)
            return False

    @classmethod
    def delete_token(
        cls,
        token_key: str,
        user_id: Any = None,
        *,
        raise_on_error: bool = False,
    ) -> bool:
        """Delete one entry; optionally require Redis to confirm invalidation."""
        if not knox_redis_settings.CACHE_ENABLED:
            return False
        try:
            client = cls._get_redis_client()
            if client is None:
                raise ConnectionError("Redis client is unavailable.")
            pipe = client.pipeline()
            pipe.delete(cls._make_token_key(token_key))
            if user_id is not None:
                pipe.srem(cls._make_user_tokens_key(user_id), token_key)
            pipe.execute()
            return True
        except Exception as exc:
            if raise_on_error:
                raise CacheInvalidationError() from exc
            logger.warning("Redis delete_token failed: %s", exc)
            return False

    @classmethod
    def delete_all_user_tokens(
        cls, user_id: Any, *, raise_on_error: bool = False
    ) -> bool:
        """Delete entries listed in a user's bounded token index."""
        if not knox_redis_settings.CACHE_ENABLED:
            return False
        try:
            client = cls._get_redis_client()
            if client is None:
                raise ConnectionError("Redis client is unavailable.")
            user_tokens_key = cls._make_user_tokens_key(user_id)
            token_keys = client.smembers(user_tokens_key)
            pipe = client.pipeline()
            for token_key in token_keys:
                if isinstance(token_key, bytes):
                    token_key = token_key.decode("utf-8")
                pipe.delete(cls._make_token_key(token_key))
            pipe.delete(user_tokens_key)
            pipe.execute()
            return True
        except Exception as exc:
            if raise_on_error:
                raise CacheInvalidationError() from exc
            logger.warning("Redis delete_all_user_tokens failed: %s", exc)
            return False

    @classmethod
    def update_token_expiry(cls, token_key: str, new_expiry: datetime | None) -> bool:
        """Update a cached payload while preserving the bounded TTL contract."""
        if not knox_redis_settings.CACHE_ENABLED:
            return False
        try:
            expires_at = cls._expires_at_for_expiry(new_expiry)
            if expires_at is None:
                return cls.delete_token(token_key)
            client = cls._get_redis_client()
            if client is None:
                return False
            raw_data = client.get(cls._make_token_key(token_key))
            if raw_data is None:
                return False
            data = cls._parse_payload(raw_data, token_key)
            if data is None:
                return False
            data["expiry"] = new_expiry.isoformat() if new_expiry else None
            pipe = client.pipeline()
            pipe.set(
                cls._make_token_key(token_key),
                json.dumps(data, cls=DjangoJSONEncoder),
                exat=expires_at,
            )
            index_expires_at = cls._expires_at_for_expiry(None)
            if index_expires_at is not None:
                pipe.expireat(
                    cls._make_user_tokens_key(data["user_id"]),
                    index_expires_at,
                )
            pipe.execute()
            return True
        except Exception as exc:
            logger.warning("Redis update_token_expiry failed: %s", exc)
            return False
