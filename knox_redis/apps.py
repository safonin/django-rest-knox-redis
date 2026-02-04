"""
Django app configuration for knox_redis.
"""

from django.apps import AppConfig


class KnoxRedisConfig(AppConfig):
    """Django app configuration for knox_redis."""

    name = "knox_redis"
    verbose_name = "Knox Redis Cache"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """
        Called when Django starts.

        Connects signal handlers for automatic cache invalidation.
        """
        from knox_redis.signals import connect_signals

        connect_signals()
