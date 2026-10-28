"""
Location check-in for the app (v3.60.0; rules in ``src/location_checkin.py``).

Members:
* ``GET events/checkin-areas/`` — the areas the app should watch (next 7 days,
  soonest first), with each window and whether the member is already in.
* ``POST devices/checkin-key/`` — a new check-in key for this phone (replacing
  any old one). Shown once; only its hash is stored.
* ``POST events/<id>/checkin/location/`` with ``{latitude, longitude,
  accuracy}`` and the ``X-Checkin-Key`` header — the check-in itself.

Officers and chairs (the ``officer_required`` rule of the attendance pages):
* ``PUT events/<id>/checkin-area/`` with ``{latitude, longitude, radius_m}``
  or ``{place_id[, radius_m]}``, optionally ``save_as`` to keep it as a place;
  ``DELETE`` to turn location check-in off for the event.
* ``GET/POST checkin-places/`` and ``DELETE checkin-places/<id>/``.
"""
import math

from django.db import IntegrityError
from rest_framework import exceptions
from rest_framework.response import Response

from src import location_checkin as lc
from src.mobile.api import CheckinKeyAPIView, MobileAPIView
from src.models import ActivityLog, CheckinPlace, Event
from src.models.mobile import MobileDevice, hash_secret, new_secret
from src.permissions import user_is_officer_or_chair


def _need_feature():
    if not lc.enabled():
        raise exceptions.PermissionDenied('Location check-in is off.', code='feature_disabled')


def _need_officer(user):
    if not user_is_officer_or_chair(user):
        raise exceptions.PermissionDenied('Officers and chairs set check-in areas.', code='forbidden')


def _place_json(p):
    return {'id': p.id, 'name': p.name, 'latitude': float(p.latitude),
            'longitude': float(p.longitude), 'radius_m': p.radius_m}


class CheckinAreaListView(MobileAPIView):
    web_page = 'calendar'

    def get(self, request):
        on = lc.enabled()
        return Response({'enabled': on, 'events': lc.upcoming_areas(request.user) if on else []})


class CheckinKeyView(MobileAPIView):

    def post(self, request):
        _need_feature()
        raw = new_secret()
        MobileDevice.objects.filter(pk=request.mobile_device.pk).update(checkin_key_hash=hash_secret(raw))
        return Response({'key': raw})


class LocationCheckinView(CheckinKeyAPIView):
    web_page = 'event_qr_checkin'   # the QR scan target: same pledge rules
    web_page_toggle = False

    def post(self, request, event_id):
        _need_feature()
        try:
            lat = float(request.data['latitude'])
            lng = float(request.data['longitude'])
            accuracy = float(request.data['accuracy']) if request.data.get('accuracy') is not None else None
        except (KeyError, TypeError, ValueError):
            raise exceptions.ValidationError('latitude, longitude and accuracy are numbers.',
                                             code='invalid_request')
        # float('nan') / float('inf') parse; NaN slipped past every comparison (10-28-26).
        if not (math.isfinite(lat) and math.isfinite(lng)
                and -90 <= lat <= 90 and -180 <= lng <= 180):
            raise exceptions.ValidationError('latitude, longitude and accuracy are numbers.',
                                             code='invalid_request')
        # Same events the member is shown in checkin-areas/ (visible to them, not
        # archived) — otherwise a hidden event could be found, located from the
        # too_far distances, and checked in to (10-28-26).
        event = (Event.objects
                 .filter(pk=event_id, requires_attendance=True, is_active=True, archived=False)
                 .filter(lc.visible_to_q(request.user.member_type))
                 .first())
        if event is None:
            raise exceptions.NotFound()
        result, created, distance = lc.location_check_in(request, request.user, event, lat, lng, accuracy)
        if result != 'success':
            status, detail = lc.REFUSALS[result]
            # Prefixed: a bare code like `expired` means "signed out" to the app.
            return Response({'error': f'checkin_{result}', 'detail': detail, 'distance_m': distance},
                            status=status)
        return Response({'ok': True, 'already_checked_in': not created, 'distance_m': distance,
                         'event': {'id': event.id, 'title': event.title}})


class EventCheckinAreaView(MobileAPIView):
    web_page = 'edit_event'
    web_page_toggle = False

    def _event(self, request, event_id):
        _need_feature()
        _need_officer(request.user)
        event = Event.objects.filter(pk=event_id).first()
        if event is None:
            raise exceptions.NotFound()
        return event

    def put(self, request, event_id):
        event = self._event(request, event_id)
        data = request.data
        try:
            if data.get('place_id'):
                place = CheckinPlace.objects.filter(pk=data['place_id']).first()
                if place is None:
                    raise exceptions.NotFound()
                lat, lng, radius = lc.clean_point(place.latitude, place.longitude,
                                                  data.get('radius_m') or place.radius_m)
            else:
                lat, lng, radius = lc.clean_point(data.get('latitude'), data.get('longitude'),
                                                  data.get('radius_m'))
        except ValueError as e:
            raise exceptions.ValidationError(str(e), code='invalid_request')
        saved = None
        name = (data.get('save_as') or '').strip()[:80]
        if name:
            try:
                saved, _ = CheckinPlace.objects.update_or_create(
                    name=name, defaults={'latitude': lat, 'longitude': lng, 'radius_m': radius,
                                         'created_by': request.user})
            except IntegrityError:
                raise exceptions.ValidationError('A place with that name already exists.', code='invalid_request')
        Event.objects.filter(pk=event.pk).update(checkin_latitude=lat, checkin_longitude=lng,
                                                 checkin_radius_m=radius)
        event.refresh_from_db()
        ActivityLog.log_activity(action_type='event_edited', user=request.user,
                                 description=f'{request.user.name} set the check-in area for {event.title} '
                                             f'({radius} m)',
                                 request=request, object_type='Event', object_id=event.id,
                                 object_repr=str(event))
        return Response({'checkin_area': lc.area_json(event), 'place': _place_json(saved) if saved else None})

    def delete(self, request, event_id):
        event = self._event(request, event_id)
        Event.objects.filter(pk=event.pk).update(checkin_latitude=None, checkin_longitude=None,
                                                 checkin_radius_m=None)
        return Response({'checkin_area': None})


class CheckinPlaceListView(MobileAPIView):
    web_page = 'edit_event'
    web_page_toggle = False

    def get(self, request):
        _need_feature()
        _need_officer(request.user)
        return Response({'places': [_place_json(p) for p in CheckinPlace.objects.all()]})

    def post(self, request):
        _need_feature()
        _need_officer(request.user)
        name = (request.data.get('name') or '').strip()[:80]
        if not name:
            raise exceptions.ValidationError('Give the place a name.', code='invalid_request')
        try:
            lat, lng, radius = lc.clean_point(request.data.get('latitude'), request.data.get('longitude'),
                                              request.data.get('radius_m', 100))
            place = CheckinPlace.objects.create(name=name, latitude=lat, longitude=lng, radius_m=radius,
                                                created_by=request.user)
        except ValueError as e:
            raise exceptions.ValidationError(str(e), code='invalid_request')
        except IntegrityError:
            raise exceptions.ValidationError('A place with that name already exists.', code='invalid_request')
        return Response({'place': _place_json(place)}, status=201)


class CheckinPlaceView(MobileAPIView):
    web_page = 'edit_event'
    web_page_toggle = False

    def delete(self, request, place_id):
        _need_feature()
        _need_officer(request.user)
        deleted, _ = CheckinPlace.objects.filter(pk=place_id).delete()
        if not deleted:
            raise exceptions.NotFound()
        return Response({'ok': True})
