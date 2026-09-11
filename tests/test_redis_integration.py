"""Opt-in integration checks against a real django-redis backend."""

import os
from uuid import uuid4

import pytest
from django.test import override_settings
from knox.models import AuthToken

from knox_redis.cache import TokenCache

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not os.environ.get("KNOX_REDIS_TEST_URL"),
        reason="set KNOX_REDIS_TEST_URL to run real Redis integration tests",
    ),
]


def test_real_django_redis_round_trip_and_orm_invalidation(user) -> None:
    prefix = f"knox_integration:{uuid4().hex}"
    package_settings = {
        "CACHE_ALIAS": "default",
        "REDIS_KEY_PREFIX": prefix,
        "CACHE_ENABLED": True,
        "MAX_TOKEN_CACHE_TTL": 30,
    }

    with override_settings(REST_KNOX_REDIS=package_settings):
        instance, _ = AuthToken.objects.create(user=user)
        redis_client = TokenCache._get_redis_client()
        assert redis_client is not None
        assert redis_client.ping()

        assert TokenCache.set_token(instance)
        assert TokenCache.get_token(instance.token_key)["digest"] == instance.digest
        assert (
            0 < redis_client.ttl(TokenCache._make_token_key(instance.token_key)) <= 30
        )

        instance.delete()

        assert TokenCache.get_token(instance.token_key) is None
        assert redis_client.smembers(TokenCache._make_user_tokens_key(user.pk)) == set()
