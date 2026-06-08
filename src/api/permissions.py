"""
DRF permission classes for Parliament's REST API.

APIEnabled  — gates the entire API behind the 'rest_api' feature flag.
ScopePermission — checks that the authenticated token holds the scope declared
                  on the viewset via the ``required_scope`` class attribute.
"""
from rest_framework.permissions import BasePermission

from src.models_feature_flags import FeatureFlag


class APIEnabled(BasePermission):
    """Block all API access when the 'rest_api' feature flag is disabled."""
    message = 'The Parliament API is not currently enabled.'

    def has_permission(self, request, view):
        return FeatureFlag.is_feature_enabled('rest_api')


class ScopePermission(BasePermission):
    """
    Check that the authenticated API token carries the scope declared on the
    viewset via the ``required_scope`` class attribute.

    If the viewset does not declare ``required_scope`` the check passes
    (i.e., endpoints without an explicit scope are open to any valid token).
    """
    message = 'Your API token does not have permission for this endpoint.'

    def has_permission(self, request, view):
        scope = getattr(view, 'required_scope', None)
        if scope is None:
            return True
        token = getattr(request, '_api_token', None)
        if token is None:
            return False
        return token.has_scope(scope)
