"""
The check-in and poll QR codes in the app (v4.4.3): an officer running a
meeting shows the code from their phone instead of the website. Each is the
website page's own rules and code path (the helpers in
``src/view/officer/event_attendance.py`` and ``announcement_polls.py``):

* ``GET officer/events/<id>/qr/`` — ``manage_qr_checkin``: the open window
  (opened/expires, seconds left), its QR, the live check-in count and the
  embed link. ``POST .../qr/open/`` starts a new 15-minute window
  (``open_checkin_window``; refused once attendance is finalized), ``POST
  .../qr/close/`` ends it early (``close_checkin_window``). ``POST
  .../qr/embed/`` gets (or reuses) the slide-deck embed link and ``DELETE``
  revokes it. Officers and chairs (``officer_required``), with the
  ``attendance_tracking``, ``event_attendance`` and ``qr_attendance_checkin``
  flags on (``QR_CHECKIN_FLAGS``), on an event that requires attendance.
* ``GET officer/announcements/<id>/poll/qr/`` — ``manage_poll_qr``: the
  poll's QR (it never changes: the poll's own open/close governs it), whether
  it's taking responses, and the embed link; ``POST``/``DELETE
  .../poll/qr/embed/`` as above. Officers and chairs.

The QR is a PNG data URI (``render_qr_png_data_uri``) so the app draws it with
React Native's Image, no QR library or SVG module. The check-in QR encodes a
live token, so it is only ever in a response while its window is open, and
the encoded URL itself is not sent separately (a link is easier to forward
than a picture).
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import Announcement, AnnouncementPoll, AnnouncementPollEmbed, Attendance, Event, EventCheckinEmbed
from src.models import EventCheckinWindow
from src.permissions import user_is_officer_or_chair
from src.utils.qr_svg import render_qr_png_data_uri
from src.view.officer import announcement_polls as web_polls
from src.view.officer import event_attendance as web_checkin

#: The note self_check_in (src/view/event_checkin.py) puts on a QR check-in.
QR_NOTE_PREFIX = 'Self-checked in via QR'


class _Officers(MobileAPIView):
    web_page_toggle = False   # the officer pages have no page toggles

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_is_officer_or_chair(request.user):
            raise exceptions.PermissionDenied('Officers and chairs only.', code='forbidden')


# ── Event check-in ───────────────────────────────────────────────────────────

class _CheckinView(_Officers):
    web_page = 'manage_qr_checkin'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not all(check_feature_enabled(f) for f in web_checkin.QR_CHECKIN_FLAGS):
            raise exceptions.PermissionDenied('QR check-in is off.', code='feature_disabled')

    def _event(self, event_id):
        return get_object_or_404(Event, pk=event_id, requires_attendance=True)

    def _response(self, event):
        window = EventCheckinWindow.get_open_window(event)
        now = timezone.now()
        present = Attendance.objects.filter(event=event, attendance_type='event', status='present')
        return Response({
            'event': {'id': event.id, 'title': event.title, 'date_time': event.date_time.isoformat(),
                      'finalized': event.attendance_finalized},
            'window_minutes': EventCheckinWindow.WINDOW_MINUTES,
            'window': ({
                'opened_at': window.opened_at.isoformat(),
                'expires_at': window.expires_at.isoformat(),
                # Counted on the server's clock, so a phone whose clock is off still counts down right.
                'seconds_left': max(0, int((window.expires_at - now).total_seconds())),
                'opened_by': window.opened_by.get_display_name() if window.opened_by_id else None,
                'qr': render_qr_png_data_uri(web_checkin.checkin_url(self.request, event, window)),
            } if window else None),
            'checked_in': {
                'qr': present.filter(notes__startswith=QR_NOTE_PREFIX).count(),
                'present': present.count(),
            },
            'embed_url': web_checkin.checkin_embed_url(self.request, event),
        })


class CheckinQRView(_CheckinView):
    def get(self, request, event_id):
        return self._response(self._event(event_id))


class CheckinQROpenView(_CheckinView):
    web_page = 'open_qr_checkin'

    def post(self, request, event_id):
        event = self._event(event_id)
        try:
            web_checkin.open_checkin_window(event, request.user, request._request)
        except web_checkin.CheckinFinalized as e:
            raise BadRequest(str(e), code='finalized')
        return self._response(event)


class CheckinQRCloseView(_CheckinView):
    web_page = 'close_qr_checkin'

    def post(self, request, event_id):
        event = self._event(event_id)
        web_checkin.close_checkin_window(event, request.user)
        return self._response(event)


class CheckinQREmbedView(_CheckinView):
    web_page = 'generate_qr_embed_link'

    def post(self, request, event_id):
        event = self._event(event_id)
        EventCheckinEmbed.get_or_create_for(event, created_by=request.user)
        return Response({'embed_url': web_checkin.checkin_embed_url(request, event)})

    def delete(self, request, event_id):
        web_checkin.revoke_checkin_embed(self._event(event_id))
        return Response({'embed_url': None})


# ── Poll ─────────────────────────────────────────────────────────────────────

class _PollQRView(_Officers):
    web_page = 'manage_poll_qr'

    def _poll(self, announcement_id):
        announcement = get_object_or_404(Announcement, pk=announcement_id)
        return announcement, get_object_or_404(AnnouncementPoll, announcement=announcement)


class PollQRView(_PollQRView):
    def get(self, request, announcement_id):
        a, poll = self._poll(announcement_id)
        return Response({
            'announcement': {'id': a.id, 'title': a.title, 'published': a.is_published()},
            'poll': {'title': poll.title, 'accepting': poll.is_accepting_responses(),
                     'closes_at': poll.closes_at.isoformat() if poll.closes_at else None},
            'qr': render_qr_png_data_uri(web_polls.poll_take_url(request, a)),
            'embed_url': web_polls.poll_embed_url(request, a, poll),
        })


class PollQREmbedView(_PollQRView):
    web_page = 'generate_poll_qr_embed_link'

    def post(self, request, announcement_id):
        a, poll = self._poll(announcement_id)
        AnnouncementPollEmbed.get_or_create_for(poll, created_by=request.user)
        return Response({'embed_url': web_polls.poll_embed_url(request, a, poll)})

    def delete(self, request, announcement_id):
        _, poll = self._poll(announcement_id)
        web_polls.revoke_poll_embed(poll)
        return Response({'embed_url': None})
