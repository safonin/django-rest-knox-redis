"""Knox views wired to the authoritative Redis-aware authenticator."""

from typing import Any

from knox.views import LoginView as KnoxLoginView
from knox.views import LogoutAllView as KnoxLogoutAllView
from knox.views import LogoutView as KnoxLogoutView

from knox_redis.auth import TokenAuthentication, cache_is_enabled
from knox_redis.cache import TokenCache


class LoginView(KnoxLoginView):  # type: ignore[misc]
    """Create a Knox token and cache that exact model instance when enabled."""

    def create_token(self) -> tuple[Any, str]:
        instance, token = super().create_token()
        if cache_is_enabled():
            TokenCache.set_token(instance)
        return instance, token


class LogoutView(KnoxLogoutView):  # type: ignore[misc]
    """Delegate deletion to Knox; the fail-closed signal owns invalidation."""

    authentication_classes = (TokenAuthentication,)


class LogoutAllView(KnoxLogoutAllView):  # type: ignore[misc]
    """Delegate bulk deletion to Knox and the fail-closed token signal."""

    authentication_classes = (TokenAuthentication,)
