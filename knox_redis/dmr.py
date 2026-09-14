"""Optional django-modern-rest authentication adapters for Knox tokens.

This module intentionally is not imported from :mod:`knox_redis`. Install the
``dmr`` extra before importing it. Only DMR's synchronous authentication API is
supported; the wrapped Knox and Django ORM authenticators are synchronous.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from dmr import APIError, HeaderSpec, ResponseSpec
from dmr.errors import ErrorType
from dmr.metadata import EndpointMetadata
from dmr.openapi.objects import Reference, SecurityRequirement, SecurityScheme
from dmr.security import SyncAuth
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from rest_framework import exceptions
from rest_framework.views import exception_handler
from typing_extensions import override

from knox_redis.auth import TokenAuthentication as RedisTokenAuthentication

if TYPE_CHECKING:
    from dmr.controller import Controller
    from dmr.endpoint import Endpoint
    from dmr.serializer import BaseSerializer

__all__ = ("KnoxDatabaseSyncAuth", "KnoxRedisSyncAuth")


class _BaseKnoxSyncAuth(SyncAuth):
    """Adapt a public DRF Knox authenticator to DMR's sync auth contract."""

    __slots__ = ("required", "security_scheme_name")

    authentication_class: ClassVar[type[KnoxTokenAuthentication]]
    # Stock Knox can delete an expired token. With knox_redis installed that
    # deletion is fail-closed too, so both adapters can legitimately emit 503.
    additional_response_statuses: ClassVar[tuple[HTTPStatus, ...]] = (
        HTTPStatus.SERVICE_UNAVAILABLE,
    )

    def __init__(
        self,
        *,
        required: bool = False,
        security_scheme_name: str = "knox_token",
    ) -> None:
        """Configure alternative or terminal-required authentication."""
        self.required = required
        self.security_scheme_name = security_scheme_name

    @property
    @override
    def security_schemes(self) -> dict[str, SecurityScheme | Reference]:
        """Describe Knox's non-Bearer Authorization header for OpenAPI."""
        return {
            self.security_scheme_name: SecurityScheme(
                type="apiKey",
                name="Authorization",
                security_scheme_in="header",
                description="Knox token authentication: Token <token>",
            )
        }

    @property
    @override
    def security_requirement(self) -> SecurityRequirement:
        """Require this scheme without changing DMR alternative ordering."""
        return {self.security_scheme_name: []}

    @property
    def www_authenticate_challenge(self) -> str:
        """Advertise the Knox authorization scheme to DMR 0.15 and newer."""
        return "Token"

    @override
    def provide_response_specs(
        self,
        metadata: EndpointMetadata,
        controller_cls: type[Controller[BaseSerializer]],
        existing_responses: Mapping[HTTPStatus, ResponseSpec],
    ) -> list[ResponseSpec]:
        """Document adapter errors without replacing controller responses."""
        del metadata
        responses: list[ResponseSpec] = []
        if HTTPStatus.UNAUTHORIZED not in existing_responses:
            responses.append(
                ResponseSpec(
                    controller_cls.error_model,
                    status_code=HTTPStatus.UNAUTHORIZED,
                    headers={
                        "WWW-Authenticate": HeaderSpec(
                            description="Knox authentication challenge.",
                        )
                    },
                    description="Raised when Knox authentication is unsuccessful.",
                )
            )
        for status_code in self.additional_response_statuses:
            if status_code not in existing_responses:
                responses.append(
                    ResponseSpec(
                        controller_cls.error_model,
                        status_code=status_code,
                        description="Raised when Knox authentication cannot complete.",
                    )
                )
        return responses

    @override
    def __call__(
        self,
        endpoint: Endpoint,
        controller: Controller[BaseSerializer],
    ) -> _BaseKnoxSyncAuth | None:
        """Authenticate with Knox while preserving its header parsing rules."""
        del endpoint
        authenticator = self.authentication_class()
        try:
            authenticated = authenticator.authenticate(controller.request)
        except UnicodeError:
            self._clear_request_attrs(controller.request)
            self._raise_api_error(
                exceptions.AuthenticationFailed("Invalid token."),
                authenticator,
                controller,
            )
        except exceptions.APIException as exc:
            self._clear_request_attrs(controller.request)
            self._raise_api_error(exc, authenticator, controller)

        if authenticated is None:
            if not self.required:
                return None
            self._clear_request_attrs(controller.request)
            self._raise_api_error(
                exceptions.NotAuthenticated(),
                authenticator,
                controller,
            )

        user, auth_token = authenticated
        self._set_request_attrs(controller.request, user, auth_token)
        return self

    @staticmethod
    def _set_request_attrs(
        request: HttpRequest,
        user: Any,
        auth_token: Any,
    ) -> None:
        """Populate both DRF-compatible and Django async request attributes."""
        request.user = user
        request.auth = auth_token  # type: ignore[attr-defined]
        request._auth = auth_token  # type: ignore[attr-defined]

        async def auser() -> Any:
            return user

        request.auser = auser

    @classmethod
    def _clear_request_attrs(cls, request: HttpRequest) -> None:
        """Remove possibly stale credentials before emitting a terminal error."""
        cls._set_request_attrs(request, AnonymousUser(), None)

    @staticmethod
    def _raise_api_error(
        exc: exceptions.APIException,
        authenticator: KnoxTokenAuthentication,
        controller: Controller[BaseSerializer],
    ) -> None:
        """Translate a DRF API exception to DMR without leaking a 500."""
        if isinstance(
            exc, (exceptions.AuthenticationFailed, exceptions.NotAuthenticated)
        ):
            authentication_exc: Any = exc
            authentication_exc.auth_header = authenticator.authenticate_header(
                controller.request
            )

        response = exception_handler(exc, context={})
        status_code = HTTPStatus(
            response.status_code if response is not None else exc.status_code
        )
        headers = {}
        if response is not None:
            headers = {
                name: response.headers[name]
                for name in ("WWW-Authenticate", "Retry-After")
                if name in response.headers
            }
        if status_code == HTTPStatus.UNAUTHORIZED:
            headers.setdefault(
                "WWW-Authenticate",
                authenticator.authenticate_header(controller.request),
            )

        raise APIError(
            controller.format_error(
                str(exc.detail),
                error_type=ErrorType.security,
            ),
            status_code=status_code,
            headers=headers or None,
        ) from exc


class KnoxRedisSyncAuth(_BaseKnoxSyncAuth):
    """DMR SyncAuth backed by Redis-accelerated Knox authentication."""

    __slots__ = ()

    authentication_class = RedisTokenAuthentication


class KnoxDatabaseSyncAuth(_BaseKnoxSyncAuth):
    """DMR SyncAuth backed by Knox's database authenticator."""

    __slots__ = ()

    authentication_class = KnoxTokenAuthentication
