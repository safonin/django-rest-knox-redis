"""
Signal handlers for automatic cache invalidation.

Handles cache invalidation when tokens are deleted directly via ORM/admin.
"""

import logging

from django.db.models.signals import pre_delete

from knox_redis.cache import TokenCache
from knox_redis.settings import knox_redis_settings

logger = logging.getLogger(__name__)


def invalidate_token_on_delete(sender, instance, **kwargs):
    """
    Invalidate cache when an AuthToken is deleted.

    This handles direct model deletions that bypass our custom views,
    such as deletions via admin panel, management commands, or direct ORM operations.
    """
    if not knox_redis_settings.CACHE_ENABLED:
        return

    try:
        TokenCache.delete_token(instance.token_key, instance.user_id)
        logger.debug(f"Invalidated token cache for token_key={instance.token_key}")
    except Exception as e:
        logger.warning(f"Failed to invalidate token cache on delete: {e}")


def connect_signals():
    """
    Connect signals with the actual AuthToken model.

    Called from AppConfig.ready() to ensure models are loaded.
    """
    from knox.models import get_token_model

    AuthToken = get_token_model()
    pre_delete.connect(
        invalidate_token_on_delete,
        sender=AuthToken,
        dispatch_uid="knox_redis_token_delete",
    )
    logger.debug(f"Connected knox_redis signals to {AuthToken}")


def disconnect_signals():
    """
    Disconnect signals (useful for testing).
    """
    from knox.models import get_token_model

    AuthToken = get_token_model()
    pre_delete.disconnect(
        invalidate_token_on_delete,
        sender=AuthToken,
        dispatch_uid="knox_redis_token_delete",
    )
