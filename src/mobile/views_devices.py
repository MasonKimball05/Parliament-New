"""
The member's own signed-in app installs (mobile plan, Phase 1).

* ``GET  /api/mobile/v1/devices/`` — live devices (not revoked, refresh window
  open), ``current`` marks the one making the request.
* ``POST /api/mobile/v1/devices/<uuid>/revoke/`` — sign one out. Own devices
  only; anyone else's is a 404 (not 403), so device ids can't be probed.

The website's Active Sessions page has the same list and button
(``src/view/session_viewer.py``).
"""
from django.http import Http404
from django.utils import timezone
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models.mobile import MobileDevice


def live_devices(user):
    return (MobileDevice.objects
            .filter(user=user, revoked_at__isnull=True, refresh_expires_at__gt=timezone.now())
            .only('public_id', 'platform', 'device_name', 'app_version', 'created_at', 'last_seen_at')
            .order_by('-last_seen_at', '-created_at'))


def _serialize(device, current_pk):
    return {
        'public_id': str(device.public_id),
        'platform': device.platform,
        'device_name': device.device_name,
        'app_version': device.app_version,
        'created_at': device.created_at.isoformat(),
        'last_seen_at': device.last_seen_at.isoformat() if device.last_seen_at else None,
        'current': device.pk == current_pk,
    }


class DeviceListView(MobileAPIView):

    def get(self, request):
        current = request.mobile_device.pk
        return Response({'devices': [_serialize(d, current) for d in live_devices(request.user)[:50]]})


class DeviceRevokeView(MobileAPIView):

    def post(self, request, public_id):
        device = (MobileDevice.objects
                  .filter(public_id=public_id, user=request.user, revoked_at__isnull=True).first())
        if device is None:
            raise Http404
        device.revoke('signed out from the app')
        return Response({'ok': True, 'current': device.pk == request.mobile_device.pk})
