"""
Tests for Login/Logout views with cache invalidation.
"""

from unittest.mock import patch

import pytest
from django.db import transaction
from knox.models import AuthToken
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from knox_redis.auth import TokenAuthentication
from knox_redis.cache import TokenCache
from knox_redis.exceptions import CacheInvalidationError
from knox_redis.views import LoginView


@pytest.mark.django_db
class TestLogoutView:
    """Tests for LogoutView."""

    def test_logout_cache_hit_uses_full_authentication_flow(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        instance, token = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)
        assert TokenCache.get_token(instance.token_key) is not None

        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        response = api_client.post("/auth/logout/")

        assert response.status_code == 204
        assert TokenCache.get_token(instance.token_key) is None
        assert not AuthToken.objects.filter(digest=instance.digest).exists()

        assert api_client.post("/auth/logout/").status_code == 401

    def test_logout_cache_miss_uses_full_authentication_flow(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        instance, token = AuthToken.objects.create(user=user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        response = api_client.post("/auth/logout/")

        assert response.status_code == 204
        assert not AuthToken.objects.filter(digest=instance.digest).exists()
        assert TokenCache.get_token(instance.token_key) is None

    @pytest.mark.django_db(transaction=True)
    def test_logout_invalidation_failure_returns_503_and_keeps_token(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        instance, token = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        with patch.object(
            TokenCache,
            "delete_token",
            side_effect=CacheInvalidationError(),
        ):
            response = api_client.post("/auth/logout/")

        assert response.status_code == 503
        assert AuthToken.objects.filter(digest=instance.digest).exists()
        result_user, result_token = TokenAuthentication().authenticate_credentials(
            token.encode()
        )
        assert result_user == user
        assert result_token.digest == instance.digest


@pytest.mark.django_db
class TestLogoutAllView:
    """Tests for LogoutAllView."""

    def test_logoutall_invalidates_all_cached_tokens_full_flow(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        """Test that logout-all removes all user tokens from cache."""
        # Create multiple tokens
        tokens = []
        for _ in range(3):
            instance, token = AuthToken.objects.create(user=user)
            TokenCache.set_token(instance)
            tokens.append((instance, token))

        # Verify all tokens are cached
        for instance, _ in tokens:
            assert TokenCache.get_token(instance.token_key) is not None

        api_client.credentials(HTTP_AUTHORIZATION=f"Token {tokens[0][1]}")
        response = api_client.post("/auth/logoutall/")

        assert response.status_code == 204

        # Verify all tokens are removed from cache
        for instance, _ in tokens:
            assert TokenCache.get_token(instance.token_key) is None

        # Verify all tokens are removed from database
        assert user.auth_token_set.count() == 0

        assert api_client.post("/auth/logoutall/").status_code == 401

    def test_logoutall_cache_miss_uses_full_authentication_flow(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        tokens = [AuthToken.objects.create(user=user) for _ in range(2)]
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {tokens[0][1]}")

        response = api_client.post("/auth/logoutall/")

        assert response.status_code == 204
        assert user.auth_token_set.count() == 0

    @pytest.mark.django_db(transaction=True)
    def test_logoutall_partial_invalidation_failure_rolls_back_all_rows(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        tokens = [AuthToken.objects.create(user=user) for _ in range(3)]
        for instance, _ in tokens:
            TokenCache.set_token(instance)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {tokens[0][1]}")

        failure = CacheInvalidationError()
        with patch.object(
            TokenCache,
            "delete_token",
            side_effect=[True, failure],
        ):
            response = api_client.post("/auth/logoutall/")

        assert response.status_code == 503
        assert user.auth_token_set.count() == 3
        for instance, raw_token in tokens:
            result_user, result_token = TokenAuthentication().authenticate_credentials(
                raw_token.encode()
            )
            assert result_user == user
            assert result_token.digest == instance.digest


@pytest.mark.django_db
class TestLoginView:
    """Tests for LoginView."""

    def test_login_caches_new_token(self, user, mock_redis_client, cache_enabled):
        """Test that login caches the newly created token."""
        # Create a request for login
        factory = APIRequestFactory()
        request = factory.post("/auth/login/")

        # Force authentication on request
        from rest_framework.request import Request

        drf_request = Request(request)
        drf_request.user = user
        drf_request._auth = None

        # Get token count before
        initial_count = user.auth_token_set.count()

        # Create view instance with AllowAny permission for testing
        view = LoginView()
        view.permission_classes = [AllowAny]
        view.request = drf_request
        view.format_kwarg = None

        # Call post directly
        response = view.post(drf_request)

        assert response.status_code == 200

        # Verify new token was created
        assert user.auth_token_set.count() == initial_count + 1

        # Get the new token
        new_token = user.auth_token_set.order_by("-created").first()

        # Verify token is cached
        cached = TokenCache.get_token(new_token.token_key)
        assert cached is not None
        assert cached["digest"] == new_token.digest

    def test_login_full_flow_caches_the_exact_created_instance(
        self, user, api_client, mock_redis_client, cache_enabled
    ):
        api_client.force_authenticate(user=user)

        with patch.object(TokenCache, "set_token", wraps=TokenCache.set_token) as cache:
            response = api_client.post("/auth/login/")

        assert response.status_code == 200
        assert cache.call_count == 1
        created = cache.call_args.args[0]
        assert AuthToken.objects.filter(digest=created.digest, user=user).exists()
        assert TokenCache.get_token(created.token_key)["digest"] == created.digest

    def test_auto_refresh_prevents_login_cache_population(
        self, user, api_client, settings, mock_redis_client, cache_enabled
    ):
        settings.REST_KNOX = {"TOKEN_TTL": None, "AUTO_REFRESH": True}
        api_client.force_authenticate(user=user)

        with patch.object(TokenCache, "set_token") as cache:
            response = api_client.post("/auth/login/")

        assert response.status_code == 200
        cache.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestOrmInvalidation:
    def test_direct_orm_deletion_invalidates_cached_token(
        self, user, mock_redis_client, cache_enabled
    ):
        instance, _ = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)

        instance.delete()

        assert TokenCache.get_token(instance.token_key) is None

    def test_queryset_deletion_invalidates_every_cached_token(
        self, user, mock_redis_client, cache_enabled
    ):
        tokens = [AuthToken.objects.create(user=user)[0] for _ in range(3)]
        for instance in tokens:
            TokenCache.set_token(instance)

        AuthToken.objects.filter(user=user).delete()

        assert all(
            TokenCache.get_token(instance.token_key) is None for instance in tokens
        )

    def test_user_cascade_failure_rolls_back_user_and_token(
        self, user, mock_redis_client, cache_enabled
    ):
        instance, _ = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)
        user_pk = user.pk
        digest = instance.digest

        with (
            patch.object(
                TokenCache,
                "delete_token",
                side_effect=CacheInvalidationError(),
            ),
            pytest.raises(CacheInvalidationError),
        ):
            user.delete()

        assert user.__class__.objects.filter(pk=user_pk).exists()
        assert AuthToken.objects.filter(digest=digest).exists()

    def test_database_rollback_leaves_valid_token_recoverable(
        self, user, mock_redis_client, cache_enabled
    ):
        instance, raw_token = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)
        digest = instance.digest
        token_key = instance.token_key

        with pytest.raises(RuntimeError), transaction.atomic():
            instance.delete()
            raise RuntimeError("force rollback")

        assert AuthToken.objects.filter(digest=digest).exists()
        assert TokenCache.get_token(token_key) is None

        result_user, result_token = TokenAuthentication().authenticate_credentials(
            raw_token.encode()
        )
        assert result_user == user
        assert result_token.digest == digest

    def test_on_commit_cleanup_removes_precommit_repopulation(
        self, user, mock_redis_client, cache_enabled
    ):
        instance, _ = AuthToken.objects.create(user=user)
        TokenCache.set_token(instance)
        token_key = instance.token_key
        original_delete = TokenCache.delete_token
        repopulated = False

        def delete_then_repopulate(*args, **kwargs):
            nonlocal repopulated
            result = original_delete(*args, **kwargs)
            if kwargs.get("raise_on_error") and not repopulated:
                repopulated = True
                TokenCache.set_token(instance)
            return result

        with patch.object(
            TokenCache, "delete_token", side_effect=delete_then_repopulate
        ):
            instance.delete()

        assert repopulated is True
        assert TokenCache.get_token(token_key) is None
