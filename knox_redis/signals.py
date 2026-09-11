"""Fail-closed cache invalidation for Knox token deletion."""

import logging
from functools import partial
from typing import Any

from django.db import transaction
from django.db.models.signals import pre_delete

from knox_redis.cache import TokenCache
from knox_redis.settings import knox_redis_settings

logger = logging.getLogger(__name__)


def invalidate_token_on_delete(
    sender: type[Any], instance: Any, using: str, **kwargs: Any
) -> None:
    """Require invalidation before delete and retry cleanup after commit."""
    del sender, kwargs
    if not knox_redis_settings.CACHE_ENABLED:
        return

    TokenCache.delete_token(
        instance.token_key,
        instance.user_id,
        raise_on_error=True,
    )
    transaction.on_commit(
        partial(TokenCache.delete_token, instance.token_key, instance.user_id),
        using=using,
    )
    logger.debug("Invalidated token cache for token_key=%s", instance.token_key)


def connect_signals() -> None:
    """Connect invalidation to the configured Knox token model."""
    from knox.models import get_token_model

    token_model = get_token_model()
    pre_delete.connect(
        invalidate_token_on_delete,
        sender=token_model,
        dispatch_uid="knox_redis_token_delete",
    )


def disconnect_signals() -> None:
    """Disconnect package signals for isolated tests."""
    from knox.models import get_token_model

    pre_delete.disconnect(
        invalidate_token_on_delete,
        sender=get_token_model(),
        dispatch_uid="knox_redis_token_delete",
    )
