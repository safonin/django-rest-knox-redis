"""Typed, reloadable settings for :mod:`knox_redis`."""

from collections.abc import Mapping
from typing import Any, Final

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed

DEFAULTS: Final[dict[str, object]] = {
    # Which Django cache alias to use (must be configured with django-redis)
    "CACHE_ALIAS": "default",
    # Prefix for all Redis keys
    "REDIS_KEY_PREFIX": "knox",
    # Enable/disable caching entirely
    "CACHE_ENABLED": True,
    # Maximum time before an authoritative database revalidation is required
    "MAX_TOKEN_CACHE_TTL": 300,
}


class KnoxRedisSettings:
    """
    Lazy settings loader for knox_redis.

    Access settings via knox_redis_settings.SETTING_NAME
    """

    def __init__(self) -> None:
        self._cached_attrs: set[str] = set()

    @property
    def user_settings(self) -> Mapping[str, object]:
        if not hasattr(self, "_user_settings"):
            configured = getattr(settings, "REST_KNOX_REDIS", {})
            if not isinstance(configured, Mapping):
                raise ImproperlyConfigured("REST_KNOX_REDIS must be a mapping.")
            self._user_settings = configured
        return self._user_settings

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_"):
            raise AttributeError(f"Invalid setting: {attr}")

        if attr not in DEFAULTS:
            raise AttributeError(f"Invalid knox_redis setting: {attr}")

        value = self.user_settings.get(attr, DEFAULTS[attr])
        self._validate(attr, value)
        self._cached_attrs.add(attr)
        setattr(self, attr, value)
        return value

    @staticmethod
    def _validate(attr: str, value: object) -> None:
        if attr == "CACHE_ENABLED" and not isinstance(value, bool):
            raise ImproperlyConfigured("REST_KNOX_REDIS.CACHE_ENABLED must be bool.")
        if attr in {"CACHE_ALIAS", "REDIS_KEY_PREFIX"} and not isinstance(value, str):
            raise ImproperlyConfigured(f"REST_KNOX_REDIS.{attr} must be str.")
        if attr == "MAX_TOKEN_CACHE_TTL" and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ImproperlyConfigured(
                "REST_KNOX_REDIS.MAX_TOKEN_CACHE_TTL must be a positive integer."
            )

    def reload(self) -> None:
        """Clear cached settings (useful for testing)."""
        for attr in self._cached_attrs:
            try:
                delattr(self, attr)
            except AttributeError:
                pass
        self._cached_attrs.clear()
        if hasattr(self, "_user_settings"):
            delattr(self, "_user_settings")


knox_redis_settings = KnoxRedisSettings()


def reload_knox_redis_settings(*args: object, **kwargs: object) -> None:
    """Reload package settings when Django's override mechanism changes them."""
    if kwargs.get("setting") == "REST_KNOX_REDIS":
        knox_redis_settings.reload()


setting_changed.connect(
    reload_knox_redis_settings,
    dispatch_uid="knox_redis_reload_settings",
)
