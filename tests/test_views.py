"""
Tests for Login/Logout views with cache invalidation.
"""

import pytest
from knox.models import AuthToken
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from knox_redis.cache import TokenCache
from knox_redis.views import LoginView, LogoutAllView, LogoutView


@pytest.mark.django_db
class TestLogoutView:
    """Tests for LogoutView."""

    def test_logout_invalidates_cache(self, user, mock_redis_client, cache_enabled):
        """Test that logout removes token from cache."""
        instance, token = AuthToken.objects.create(user=user)

        # Cache the token
        TokenCache.set_token(instance)
        assert TokenCache.get_token(instance.token_key) is not None

        # Create request with authentication
        factory = APIRequestFactory()
        request = factory.post("/auth/logout/", HTTP_AUTHORIZATION=f"Token {token}")

        # Force authentication on request
        from rest_framework.request import Request

        from knox_redis.auth import TokenAuthentication

        drf_request = Request(request)
        drf_request.user = user
        drf_request._auth = instance
        drf_request.authenticators = [TokenAuthentication()]

        # Create view instance and bypass authentication
        view = LogoutView()
        view.request = drf_request
        view.format_kwarg = None

        # Call post directly
        response = view.post(drf_request)

        assert response.status_code == 204

        # Verify token is removed from cache
        assert TokenCache.get_token(instance.token_key) is None

        # Verify token is removed from database
        assert not AuthToken.objects.filter(digest=instance.digest).exists()


@pytest.mark.django_db
class TestLogoutAllView:
    """Tests for LogoutAllView."""

    def test_logoutall_invalidates_all_cached_tokens(
        self, user, mock_redis_client, cache_enabled
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

        # Create request
        factory = APIRequestFactory()
        request = factory.post("/auth/logoutall/")

        # Force authentication on request
        from rest_framework.request import Request

        drf_request = Request(request)
        drf_request.user = user
        drf_request._auth = tokens[0][0]

        # Create view instance
        view = LogoutAllView()
        view.request = drf_request
        view.format_kwarg = None

        # Call post directly
        response = view.post(drf_request)

        assert response.status_code == 204

        # Verify all tokens are removed from cache
        for instance, _ in tokens:
            assert TokenCache.get_token(instance.token_key) is None

        # Verify all tokens are removed from database
        assert user.auth_token_set.count() == 0


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
