"""
Login/Logout views with Redis cache invalidation.

Drop-in replacements for knox.views.
"""

from django.contrib.auth.signals import user_logged_out
from knox.views import LoginView as KnoxLoginView
from knox.views import LogoutAllView as KnoxLogoutAllView
from knox.views import LogoutView as KnoxLogoutView

from knox_redis.auth import TokenAuthentication
from knox_redis.cache import TokenCache
from knox_redis.settings import knox_redis_settings


class LoginView(KnoxLoginView):
    """
    Login view that caches newly created tokens.

    Extends knox.views.LoginView to cache tokens on creation.

    Usage:
        from knox_redis.views import LoginView

        urlpatterns = [
            path('auth/login/', LoginView.as_view(), name='knox_login'),
        ]
    """

    def post(self, request, format=None):
        response = super().post(request, format=format)

        # If login was successful, cache the new token
        if response.status_code == 200 and knox_redis_settings.CACHE_ENABLED:
            # The token was just created, get the most recently created token
            auth_token = request.user.auth_token_set.order_by("-created").first()
            if auth_token:
                TokenCache.set_token(auth_token)

        return response


class LogoutView(KnoxLogoutView):
    """
    Logout view that invalidates Redis cache.

    Extends knox.views.LogoutView to invalidate cached token.

    Usage:
        from knox_redis.views import LogoutView

        urlpatterns = [
            path('auth/logout/', LogoutView.as_view(), name='knox_logout'),
        ]
    """

    authentication_classes = (TokenAuthentication,)

    def post(self, request, format=None):
        # Get token info before deletion for cache invalidation
        auth_token = request._auth
        token_key = auth_token.token_key
        user_id = auth_token.user_id

        # Delete from database (original knox behavior)
        auth_token.delete()

        # Invalidate cache
        if knox_redis_settings.CACHE_ENABLED:
            TokenCache.delete_token(token_key, user_id)

        # Send signal
        user_logged_out.send(
            sender=request.user.__class__, request=request, user=request.user
        )

        return self.get_post_response(request)


class LogoutAllView(KnoxLogoutAllView):
    """
    Logout from all sessions with Redis cache invalidation.

    Extends knox.views.LogoutAllView to invalidate all cached tokens.

    Usage:
        from knox_redis.views import LogoutAllView

        urlpatterns = [
            path('auth/logoutall/', LogoutAllView.as_view(), name='knox_logoutall'),
        ]
    """

    authentication_classes = (TokenAuthentication,)

    def post(self, request, format=None):
        user = request.user
        user_id = user.pk

        # Delete all tokens from database (original knox behavior)
        user.auth_token_set.all().delete()

        # Invalidate all cached tokens for this user
        if knox_redis_settings.CACHE_ENABLED:
            TokenCache.delete_all_user_tokens(user_id)

        # Send signal
        user_logged_out.send(sender=user.__class__, request=request, user=user)

        return self.get_post_response(request)
