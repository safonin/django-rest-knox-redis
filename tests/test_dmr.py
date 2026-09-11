"""Regression tests for the optional django-modern-rest integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("dmr", reason="install the dmr extra to run DMR tests")
pytest.importorskip("msgspec", reason="DMR controller tests use MsgspecSerializer")

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone
from dmr import APIError, ResponseSpec
from dmr.openapi import build_schema
from knox.auth import TokenAuthentication as KnoxTokenAuthentication
from knox.models import AuthToken

from knox_redis.cache import TokenCache
from knox_redis.dmr import KnoxDatabaseSyncAuth, KnoxRedisSyncAuth
from knox_redis.exceptions import CacheInvalidationError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _dmr_urlconf(settings):
    """Use the isolated DMR URLConf for one test at a time."""
    settings.ROOT_URLCONF = "tests.dmr_urls"


def _authorization(raw_token: str) -> str:
    return f"Token {raw_token}"


def test_redis_adapter_authenticates_a_cached_token_with_full_request_state(
    client,
    auth_token,
    cache_enabled,
    mock_redis_client,
    mocker,
) -> None:
    instance, raw_token = auth_token
    assert TokenCache.set_token(instance)
    database_miss = mocker.patch.object(
        KnoxTokenAuthentication,
        "authenticate_credentials",
        side_effect=AssertionError("cache hit unexpectedly delegated to Knox"),
    )

    response = client.get(
        "/dmr/redis-required/",
        HTTP_AUTHORIZATION=_authorization(raw_token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": instance.user.get_username(),
        "token_key": instance.token_key,
        "auth_matches_private": True,
        "provider": "KnoxRedisSyncAuth",
    }
    database_miss.assert_not_called()


def test_database_adapter_returns_a_real_knox_token_without_redis_lookup(
    client,
    auth_token,
    mocker,
) -> None:
    instance, raw_token = auth_token
    cache_lookup = mocker.patch.object(TokenCache, "get_token")

    response = client.get(
        "/dmr/database-required/",
        HTTP_AUTHORIZATION=_authorization(raw_token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": instance.user.get_username(),
        "token_key": instance.token_key,
        "auth_matches_private": True,
        "provider": "KnoxDatabaseSyncAuth",
    }
    cache_lookup.assert_not_called()


def test_success_sets_auser_to_the_authenticated_user(
    auth_token,
    cache_enabled,
    mock_redis_client,
) -> None:
    instance, raw_token = auth_token
    assert TokenCache.set_token(instance)
    request = RequestFactory().get(
        "/dmr/direct/",
        HTTP_AUTHORIZATION=_authorization(raw_token),
    )

    result = KnoxRedisSyncAuth(required=True)(
        Mock(),
        SimpleNamespace(request=request),
    )

    assert isinstance(result, KnoxRedisSyncAuth)
    assert request.auth is request._auth
    assert request.auth.token_key == instance.token_key
    assert asyncio.run(request.auser()) == request.user


def test_recognized_error_clears_stale_request_authentication_state() -> None:
    request = RequestFactory().get(
        "/dmr/direct/",
        HTTP_AUTHORIZATION="Token",
    )
    request.user = object()
    request.auth = object()
    request._auth = object()
    controller = SimpleNamespace(
        request=request,
        format_error=lambda error, **kwargs: {"error": str(error), **kwargs},
    )

    with pytest.raises(APIError):
        KnoxDatabaseSyncAuth()(Mock(), controller)

    assert isinstance(request.user, AnonymousUser)
    assert request.auth is None
    assert request._auth is None
    assert asyncio.run(request.auser()) is request.user


@pytest.mark.parametrize("authorization", [None, "Bearer other-credentials"])
def test_optional_missing_or_foreign_credentials_fall_through_untouched(
    client,
    authorization,
) -> None:
    headers = {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}

    response = client.get("/dmr/optional/", **headers)

    assert response.status_code == 200
    assert response.json() == {
        "username": "",
        "token_key": None,
        "auth_matches_private": True,
        "provider": "AnonymousSyncAuth",
    }
    assert "WWW-Authenticate" not in response


@pytest.mark.parametrize(
    "authorization",
    [
        "Token",
        "Token credential with-spaces",
        "Token invalid-token",
        "Token ÿ",
    ],
)
def test_recognized_malformed_or_invalid_credentials_stop_optional_chain(
    client,
    authorization,
) -> None:
    response = client.get(
        "/dmr/optional/",
        HTTP_AUTHORIZATION=authorization,
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Token"
    assert response.status_code != 500


@pytest.mark.parametrize("authorization", [None, "Bearer other-credentials"])
def test_required_mode_challenges_missing_or_foreign_credentials(
    client,
    authorization,
) -> None:
    headers = {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}

    response = client.get("/dmr/redis-required/", **headers)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Token"


def test_database_adapter_maps_non_utf8_token_to_401(client) -> None:
    response = client.get(
        "/dmr/database-required/",
        HTTP_AUTHORIZATION="Token ÿ",
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Token"
    assert response.status_code != 500


def test_expired_token_is_a_non_500_dmr_error(
    client,
    auth_token,
    cache_enabled,
    mock_redis_client,
) -> None:
    instance, raw_token = auth_token
    instance.expiry = timezone.now() - timedelta(seconds=1)
    instance.save(update_fields=("expiry",))

    response = client.get(
        "/dmr/redis-required/",
        HTTP_AUTHORIZATION=_authorization(raw_token),
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Token"
    assert not AuthToken.objects.filter(pk=instance.pk).exists()


def test_inactive_user_is_a_non_500_dmr_error(client, inactive_user) -> None:
    _instance, raw_token = AuthToken.objects.create(user=inactive_user)

    response = client.get(
        "/dmr/database-required/",
        HTTP_AUTHORIZATION=_authorization(raw_token),
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Token"


def test_redis_invalidation_error_preserves_service_unavailable(
    client,
    mocker,
) -> None:
    mocker.patch(
        "knox_redis.auth.TokenAuthentication.authenticate",
        side_effect=CacheInvalidationError(),
    )

    response = client.get(
        "/dmr/redis-required/",
        HTTP_AUTHORIZATION="Token recognized-credential",
    )

    assert response.status_code == 503
    assert response.status_code != 500


def test_database_adapter_preserves_expiry_invalidation_service_unavailable(
    client,
    mocker,
) -> None:
    mocker.patch(
        "knox.auth.TokenAuthentication.authenticate",
        side_effect=CacheInvalidationError(),
    )

    response = client.get(
        "/dmr/database-required/",
        HTTP_AUTHORIZATION="Token recognized-credential",
    )

    assert response.status_code == 503
    assert response.status_code != 500


def test_response_specs_do_not_replace_existing_controller_responses() -> None:
    controller_cls = SimpleNamespace(error_model=dict)
    unauthorized = ResponseSpec(dict, status_code=HTTPStatus.UNAUTHORIZED)
    unavailable = ResponseSpec(dict, status_code=HTTPStatus.SERVICE_UNAVAILABLE)

    assert (
        KnoxDatabaseSyncAuth().provide_response_specs(
            Mock(),
            controller_cls,
            {
                HTTPStatus.UNAUTHORIZED: unauthorized,
                HTTPStatus.SERVICE_UNAVAILABLE: unavailable,
            },
        )
        == []
    )
    assert (
        KnoxRedisSyncAuth().provide_response_specs(
            Mock(),
            controller_cls,
            {
                HTTPStatus.UNAUTHORIZED: unauthorized,
                HTTPStatus.SERVICE_UNAVAILABLE: unavailable,
            },
        )
        == []
    )


def test_an_earlier_successful_alternative_short_circuits_required_knox(client) -> None:
    response = client.get(
        "/dmr/ordered-required/",
        HTTP_X_EARLIER_AUTH="allow",
        HTTP_AUTHORIZATION="Token malformed credentials",
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "EarlierSyncAuth"


def test_openapi_uses_authorization_api_key_and_preserves_alternative_order() -> None:
    from tests.dmr_urls import router

    schema = build_schema(router).convert(skip_validation=True)
    security_scheme = schema["components"]["securitySchemes"]["knox_token"]

    assert security_scheme["type"] == "apiKey"
    assert security_scheme["name"] == "Authorization"
    assert security_scheme["in"] == "header"
    assert "scheme" not in security_scheme
    assert "bearerFormat" not in security_scheme
    assert schema["paths"]["/dmr/redis-required/"]["get"]["security"] == [
        {"knox_token": []}
    ]
    assert schema["paths"]["/dmr/optional/"]["get"]["security"] == [
        {"knox_token": []},
        {},
    ]
    assert schema["paths"]["/dmr/ordered-required/"]["get"]["security"] == [
        {"earlier_auth": []},
        {"knox_token": []},
    ]
    unauthorized = schema["paths"]["/dmr/redis-required/"]["get"]["responses"]["401"]
    assert "WWW-Authenticate" in unauthorized["headers"]
    assert "503" in schema["paths"]["/dmr/redis-required/"]["get"]["responses"]
    assert "503" in schema["paths"]["/dmr/database-required/"]["get"]["responses"]
