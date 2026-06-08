"""
Custom DRF authentication backend for Parliament API tokens.

Validates against APIToken instead of DRF's built-in Token model,
enabling scope checking, approval workflow, and expiry enforcement.
"""
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from src.models.api import APIToken


class APITokenAuthentication(BaseAuthentication):
    """
    Authenticate against Parliament's APIToken model.

    Header format: Authorization: Token <64-char-hex-key>

    After successful authentication the token object is attached to the request
    as ``request._api_token`` so the logging mixin and scope permission can
    read it without a second DB query.
    """
    keyword = 'Token'

    def authenticate(self, request):
        auth = request.META.get('HTTP_AUTHORIZATION', '').split()
        if not auth or auth[0].lower() != self.keyword.lower():
            return None
        if len(auth) != 2:
            raise AuthenticationFailed(
                'Invalid token header. Expected format: Authorization: Token <key>'
            )

        key = auth[1]
        try:
            token = APIToken.objects.select_related('user').get(key=key)
        except APIToken.DoesNotExist:
            raise AuthenticationFailed('Invalid token.')

        if token.status == APIToken.STATUS_PENDING:
            raise AuthenticationFailed('Token is pending admin approval.')
        if token.status == APIToken.STATUS_REVOKED:
            raise AuthenticationFailed('Token has been revoked.')
        if token.status == APIToken.STATUS_REJECTED:
            raise AuthenticationFailed('Token request was rejected.')
        if token.expires_at and token.expires_at < timezone.now():
            raise AuthenticationFailed('Token has expired.')

        # Stamp last_used_at without a full model save
        APIToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())

        # Attach token to request for use by APILoggingMixin and ScopePermission
        request._api_token = token
        return (token.user, token)

    def authenticate_header(self, request):
        return self.keyword
