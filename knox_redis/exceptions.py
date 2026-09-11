"""Public exceptions raised by :mod:`knox_redis`."""

from rest_framework import status
from rest_framework.exceptions import APIException


class CacheInvalidationError(APIException):
    """Redis could not confirm invalidation of a token being revoked."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Token revocation is temporarily unavailable."
    default_code = "token_revocation_unavailable"
