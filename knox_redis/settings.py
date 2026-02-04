"""
Settings for knox_redis.

Settings are loaded from Django's settings.py under REST_KNOX_REDIS key.
"""

from django.conf import settings

DEFAULTS = {
    # Which Django cache alias to use (must be configured with django-redis)
    "CACHE_ALIAS": "default",
    # Prefix for all Redis keys
    "REDIS_KEY_PREFIX": "knox",
    # Enable/disable caching entirely
    "CACHE_ENABLED": True,
}


class KnoxRedisSettings:
    """
    Lazy settings loader for knox_redis.

    Access settings via knox_redis_settings.SETTING_NAME
    """

    def __init__(self):
        self._cached_attrs = set()

    @property
    def user_settings(self):
        if not hasattr(self, "_user_settings"):
            self._user_settings = getattr(settings, "REST_KNOX_REDIS", {})
        return self._user_settings

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(f"Invalid setting: {attr}")

        if attr not in DEFAULTS:
            raise AttributeError(f"Invalid knox_redis setting: {attr}")

        val = self.user_settings.get(attr, DEFAULTS[attr])
        self._cached_attrs.add(attr)
        setattr(self, attr, val)
        return val

    def reload(self):
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
