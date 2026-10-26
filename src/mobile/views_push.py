"""
``/api/mobile/v1/devices/push-token/`` (mobile plan, Phase 1).

POST {push_token} stores the Expo push token on the calling device; DELETE
clears it. A token is per app install, so if another live device row holds
the same token (the app was reinstalled, or another member signed in on the
same phone) it is cleared there first: one phone, one recipient.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.push import is_valid_push_token
from src.models.mobile import MobileDevice


class PushTokenView(MobileAPIView):

    def _device(self, request):
        return getattr(request, 'mobile_device', None) or request.auth

    def post(self, request):
        device = self._device(request)
        token = request.data.get('push_token') if hasattr(request.data, 'get') else None
        if not is_valid_push_token(token):
            return Response(
                {'error': 'invalid_push_token',
                 'detail': 'Expected an ExponentPushToken[...] of at most 255 characters.'},
                status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            (MobileDevice.objects
             .filter(push_token=token, revoked_at__isnull=True)
             .exclude(pk=device.pk)
             .update(push_token=''))
            MobileDevice.objects.filter(pk=device.pk).update(push_token=token)
        device.push_token = token
        return Response({'push_token': token})

    def delete(self, request):
        device = self._device(request)
        MobileDevice.objects.filter(pk=device.pk).update(push_token='')
        device.push_token = ''
        return Response(status=status.HTTP_204_NO_CONTENT)
