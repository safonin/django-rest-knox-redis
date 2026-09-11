"""Isolated django-modern-rest routes used by the optional integration tests."""

from __future__ import annotations

from typing import TypedDict

from django.contrib.auth.models import AnonymousUser
from dmr import Controller
from dmr.endpoint import Endpoint
from dmr.openapi.objects import SecurityScheme
from dmr.plugins.msgspec import MsgspecSerializer
from dmr.routing import Router, path
from dmr.security import SyncAuth, request_auth

from knox_redis.dmr import KnoxDatabaseSyncAuth, KnoxRedisSyncAuth


class AuthState(TypedDict):
    """Observable authentication state returned by the test controllers."""

    username: str
    token_key: str | None
    auth_matches_private: bool
    provider: str


def _auth_state(controller: Controller[MsgspecSerializer]) -> AuthState:
    request = controller.request
    token = getattr(request, "auth", None)
    provider = request_auth(request, strict=True)
    return {
        "username": request.user.get_username(),
        "token_key": getattr(token, "token_key", None),
        "auth_matches_private": token is getattr(request, "_auth", None),
        "provider": type(provider).__name__,
    }


class AnonymousSyncAuth(SyncAuth):
    """Terminal anonymous alternative for optional-auth test coverage."""

    __slots__ = ()

    @property
    def security_schemes(self) -> dict[str, SecurityScheme]:
        return {}

    @property
    def security_requirement(self) -> dict[str, list[str]]:
        return {}

    def __call__(
        self,
        endpoint: Endpoint,
        controller: Controller[MsgspecSerializer],
    ) -> AnonymousSyncAuth:
        del endpoint
        request = controller.request
        anonymous = AnonymousUser()
        request.user = anonymous
        request.auth = None
        request._auth = None

        async def auser() -> AnonymousUser:
            return anonymous

        request.auser = auser
        return self


class EarlierSyncAuth(SyncAuth):
    """Successful earlier alternative used to verify short-circuit ordering."""

    __slots__ = ()

    @property
    def security_schemes(self) -> dict[str, SecurityScheme]:
        return {
            "earlier_auth": SecurityScheme(
                type="apiKey",
                name="X-Earlier-Auth",
                security_scheme_in="header",
            )
        }

    @property
    def security_requirement(self) -> dict[str, list[str]]:
        return {"earlier_auth": []}

    def __call__(
        self,
        endpoint: Endpoint,
        controller: Controller[MsgspecSerializer],
    ) -> EarlierSyncAuth | None:
        del endpoint
        if controller.request.headers.get("X-Earlier-Auth") != "allow":
            return None
        anonymous = AnonymousUser()
        controller.request.user = anonymous
        controller.request.auth = None
        controller.request._auth = None

        async def auser() -> AnonymousUser:
            return anonymous

        controller.request.auser = auser
        return self


class RedisRequiredController(Controller[MsgspecSerializer]):
    """Require the Redis-accelerated Knox authenticator."""

    auth = (KnoxRedisSyncAuth(required=True),)

    def get(self) -> AuthState:
        return _auth_state(self)


class DatabaseRequiredController(Controller[MsgspecSerializer]):
    """Require Knox's database authenticator."""

    auth = (KnoxDatabaseSyncAuth(required=True),)

    def get(self) -> AuthState:
        return _auth_state(self)


class OptionalController(Controller[MsgspecSerializer]):
    """Try Knox, then explicitly accept an anonymous request."""

    auth = (KnoxRedisSyncAuth(), AnonymousSyncAuth())

    def get(self) -> AuthState:
        return _auth_state(self)


class OrderedRequiredController(Controller[MsgspecSerializer]):
    """Preserve an earlier alternative before terminal required Knox auth."""

    auth = (EarlierSyncAuth(), KnoxRedisSyncAuth(required=True))

    def get(self) -> AuthState:
        return _auth_state(self)


routes = [
    path(
        "dmr/redis-required/",
        RedisRequiredController.as_view(),
        name="dmr_redis_required",
    ),
    path(
        "dmr/database-required/",
        DatabaseRequiredController.as_view(),
        name="dmr_database_required",
    ),
    path("dmr/optional/", OptionalController.as_view(), name="dmr_optional"),
    path(
        "dmr/ordered-required/",
        OrderedRequiredController.as_view(),
        name="dmr_ordered_required",
    ),
]

router = Router("", routes)
urlpatterns = routes
