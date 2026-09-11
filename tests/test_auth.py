"""
Tests for TokenAuthentication with Redis caching.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from knox.models import AuthToken
from rest_framework.exceptions import AuthenticationFailed

from knox_redis.auth import TokenAuthentication
from knox_redis.cache import TokenCache


@pytest.mark.django_db
class TestTokenAuthentication:
    """Tests for TokenAuthentication class."""

    def test_authenticate_from_database_caches_token(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that successful DB authentication caches the token."""
        instance, token = AuthToken.objects.create(user=user)

        # Verify token is not in cache initially
        assert TokenCache.get_token(instance.token_key) is None

        # Authenticate
        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        # Verify authentication worked
        assert result_user == user
        assert result_token == instance

        # Verify token is now cached
        cached = TokenCache.get_token(instance.token_key)
        assert cached is not None
        assert cached["digest"] == instance.digest

    def test_cache_hit_uses_one_redis_command_and_one_total_sql_query(
        self, user, mock_redis_client, cache_enabled, mocker
    ):
        """A hit returns a real row with one Redis GET and one joined SQL query."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache the token
        TokenCache.set_token(instance)

        redis_get = mocker.spy(mock_redis_client, "get")
        redis_pipeline = mocker.spy(mock_redis_client, "pipeline")

        # Authenticate with cached token.
        auth = TokenAuthentication()

        with CaptureQueriesContext(connection) as queries:
            result_user, result_token = auth.authenticate_credentials(token.encode())

        assert result_user == user
        assert result_token == instance
        assert isinstance(result_token, AuthToken)
        assert result_token._state.adding is False
        assert len(queries) == 1
        assert "knox_authtoken" in queries[0]["sql"]
        assert "auth_user" in queries[0]["sql"]
        assert redis_get.call_count == 1
        redis_pipeline.assert_not_called()

    def test_cache_miss_uses_two_redis_round_trips_four_commands_and_two_sql(
        self, user, mock_redis_client, cache_enabled, mocker
    ):
        instance, token = AuthToken.objects.create(user=user)
        auth = TokenAuthentication()
        original_pipeline = mock_redis_client.pipeline
        pipelines = []

        def tracked_pipeline(*args, **kwargs):
            pipeline = original_pipeline(*args, **kwargs)
            for method in ("set", "sadd", "expireat", "execute"):
                mocker.spy(pipeline, method)
            pipelines.append(pipeline)
            return pipeline

        redis_get = mocker.spy(mock_redis_client, "get")
        mocker.patch.object(
            mock_redis_client,
            "pipeline",
            side_effect=tracked_pipeline,
        )

        with (
            patch.object(
                KnoxTokenAuthentication,
                "authenticate_credentials",
                autospec=True,
                side_effect=KnoxTokenAuthentication.authenticate_credentials,
            ) as knox_authenticate,
            CaptureQueriesContext(connection) as queries,
        ):
            result_user, result_token = auth.authenticate_credentials(token.encode())

        assert knox_authenticate.call_count == 1
        assert result_user == user
        assert result_token == instance
        assert len(queries) == 2
        assert redis_get.call_count == 1
        assert len(pipelines) == 1
        assert pipelines[0].set.call_count == 1
        assert pipelines[0].sadd.call_count == 1
        assert pipelines[0].expireat.call_count == 1
        assert pipelines[0].execute.call_count == 1

    def test_non_utf8_token_is_a_controlled_authentication_failure(self, cache_enabled):
        with pytest.raises(AuthenticationFailed, match="Invalid token"):
            TokenAuthentication().authenticate_credentials(b"\xff")

    def test_authenticate_invalid_token(self, user, mock_redis_client, cache_enabled):
        """Test authentication fails with invalid token."""
        instance, token = AuthToken.objects.create(user=user)

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(b"invalid_token_string")

    def test_authenticate_expired_token_invalidates_cache(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that expired tokens are removed from cache."""
        # Cache a valid token, then expire only the authoritative DB row.
        instance, token = AuthToken.objects.create(user=user)
        assert TokenCache.set_token(instance)
        instance.expiry = timezone.now() - timedelta(hours=1)
        instance.save()
        assert TokenCache.get_token(instance.token_key) is not None

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(token.encode())

        # Verify token is removed from cache
        assert TokenCache.get_token(instance.token_key) is None

    def test_authenticate_inactive_user(
        self, inactive_user, mock_redis_client, cache_enabled
    ):
        """Test authentication fails for inactive user."""
        instance, token = AuthToken.objects.create(user=inactive_user)

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed) as exc_info:
            auth.authenticate_credentials(token.encode())

        assert "inactive" in str(exc_info.value).lower()

    def test_authenticate_deleted_user_invalidates_cache(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that tokens for deleted users are removed from cache."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache the token
        TokenCache.set_token(instance)

        # Delete the user
        user.delete()

        auth = TokenAuthentication()

        with pytest.raises(AuthenticationFailed):
            auth.authenticate_credentials(token.encode())

        # Verify token is removed from cache
        assert TokenCache.get_token(instance.token_key) is None

    def test_authenticate_with_cache_disabled(self, user, cache_disabled):
        """Test authentication works when cache is disabled."""
        instance, token = AuthToken.objects.create(user=user)

        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        assert result_user == user
        assert result_token == instance

    def test_cache_miss_falls_back_to_database(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that cache miss falls back to database lookup."""
        instance, token = AuthToken.objects.create(user=user)

        # Don't cache the token - simulate cache miss

        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        assert result_user == user
        assert result_token == instance

        # Verify token is now cached after DB lookup
        cached = TokenCache.get_token(instance.token_key)
        assert cached is not None

    def test_wrong_digest_in_cache_falls_back_to_database(
        self, user, mock_redis_client, cache_enabled
    ):
        """Test that wrong digest in cache triggers database fallback."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache with wrong digest
        mock_redis_client.set(
            TokenCache._make_token_key(instance.token_key),
            json.dumps(
                {
                    "schema_version": 2,
                    "digest": "wrongdigest",
                    "user_id": user.pk,
                    "created": "2024-01-01T00:00:00+00:00",
                    "expiry": None,
                    "token_key": instance.token_key,
                }
            ),
        )

        auth = TokenAuthentication()
        result_user, result_token = auth.authenticate_credentials(token.encode())

        # Should still authenticate from DB
        assert result_user == user
        assert result_token == instance
        assert TokenCache.get_token(instance.token_key)["digest"] == instance.digest

    def test_stale_cache_entry_cannot_authorize_deleted_database_token(
        self, user, mock_redis_client, cache_enabled
    ):
        instance, token = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)

        # Simulate safe but unsuccessful post-commit cleanup: the DB row is gone
        # while a bounded cache entry remains.
        with patch.object(TokenCache, "delete_token", return_value=True):
            instance.delete()

        assert TokenCache.get_token(instance.token_key) is not None
        with pytest.raises(AuthenticationFailed):
            TokenAuthentication().authenticate_credentials(token.encode())

    def test_redis_read_outage_falls_back_to_database(self, user, cache_enabled):
        instance, token = AuthToken.objects.create(user=user)

        with patch.object(TokenCache, "get_token", return_value=None):
            result_user, result_token = TokenAuthentication().authenticate_credentials(
                token.encode()
            )

        assert result_user == user
        assert result_token == instance

    def test_auto_refresh_bypasses_all_cache_io(
        self, user, settings, mock_redis_client, cache_enabled
    ):
        settings.REST_KNOX = {
            "TOKEN_TTL": timedelta(hours=1),
            "AUTO_REFRESH": True,
            "MIN_REFRESH_INTERVAL": 0,
            "AUTO_REFRESH_MAX_TTL": timedelta(hours=2),
        }
        instance, token = AuthToken.objects.create(
            user=user, expiry=timedelta(minutes=5)
        )
        old_expiry = instance.expiry

        with (
            patch.object(TokenCache, "get_token") as cache_get,
            patch.object(TokenCache, "set_token") as cache_set,
        ):
            TokenAuthentication().authenticate_credentials(token.encode())

        instance.refresh_from_db()
        assert instance.expiry > old_expiry
        cache_get.assert_not_called()
        cache_set.assert_not_called()

    def test_auto_refresh_respects_min_refresh_interval(
        self, user, settings, mock_redis_client, cache_enabled
    ):
        settings.REST_KNOX = {
            "TOKEN_TTL": timedelta(minutes=5),
            "AUTO_REFRESH": True,
            "MIN_REFRESH_INTERVAL": 120,
            "AUTO_REFRESH_MAX_TTL": None,
        }
        instance, token = AuthToken.objects.create(
            user=user,
            expiry=timedelta(minutes=4),
        )
        persisted_expiry = instance.expiry

        _, returned_token = TokenAuthentication().authenticate_credentials(
            token.encode()
        )

        instance.refresh_from_db()
        assert returned_token.expiry > persisted_expiry
        assert instance.expiry == persisted_expiry

    def test_auto_refresh_respects_max_ttl(
        self, user, settings, mock_redis_client, cache_enabled
    ):
        settings.REST_KNOX = {
            "TOKEN_TTL": timedelta(hours=1),
            "AUTO_REFRESH": True,
            "MIN_REFRESH_INTERVAL": 0,
            "AUTO_REFRESH_MAX_TTL": timedelta(minutes=10),
        }
        instance, token = AuthToken.objects.create(
            user=user,
            expiry=timedelta(minutes=2),
        )

        TokenAuthentication().authenticate_credentials(token.encode())

        instance.refresh_from_db()
        expected_maximum = instance.created + timedelta(minutes=10)
        assert abs((instance.expiry - expected_maximum).total_seconds()) < 0.001

    def test_cached_inactive_user_is_rejected(
        self, inactive_user, mock_redis_client, cache_enabled
    ):
        instance, token = AuthToken.objects.create(user=inactive_user)
        assert TokenCache.set_token(instance)

        with pytest.raises(AuthenticationFailed, match="inactive"):
            TokenAuthentication().authenticate_credentials(token.encode())

    def test_stale_cache_for_deleted_user_cannot_authenticate(
        self, user, mock_redis_client, cache_enabled
    ):
        instance, token = AuthToken.objects.create(user=user)
        assert TokenCache.set_token(instance)

        with patch.object(TokenCache, "delete_token", return_value=True):
            user.delete()

        assert TokenCache.get_token(instance.token_key) is not None
        with pytest.raises(AuthenticationFailed):
            TokenAuthentication().authenticate_credentials(token.encode())
