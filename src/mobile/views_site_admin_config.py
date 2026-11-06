"""
Site admin in the app, part three: Admin v2's notification schedules and site
settings.

Same gate as the rest of Site admin (``src/mobile/site_admin.py``: allowlist →
404, per-device 12-hour unlock → 403 ``admin_locked``). Neither website page
also checks ``user.is_admin``, so neither does the app.

* Notification schedules — the Notification Schedules page
  (``src/view/notification_admin.py``). Create, update, on/off and delete run
  the page's own helpers (``create_notification_schedule`` /
  ``update_notification_schedule`` / ``toggle_notification_schedule`` /
  ``delete_notification_schedule``); the app's form is checked first by
  ``clean_schedule_input`` (the page's form rules, enforced on the server).
  Refusals are 400 ``invalid_input`` with the message (``invalid`` is a
  signed-out reason the app reads as one). The list also
  carries what ``fire_scheduled_notifications`` does with each type and
  audience, its placeholders, and the newest sends (NotificationLog).
* Site settings — the Maintenance page's settings list (the same rows: legacy
  chat polling keys left out, the dashboard defaults seeded). A change goes
  through the page's ``clean_site_setting_value`` and
  ``apply_site_setting_change`` (the same ``setting_change`` ActivityLog row;
  the post_save receiver drops the cached value), after the app's stricter
  check (``strict_site_setting_value``): booleans and whole numbers by type,
  JSON that parses, and '2fa_policy_mode' only one of the 2FA page's policies.
  Setting a value it already has changes and logs nothing.
"""
import json

from django.conf import settings
from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from src.mobile.site_admin import SiteAdminAPIView
from src.mobile.views_site_admin import BadRequest, _http, _iso, _person

RECENT_SENDS = 20

#: What fire_scheduled_notifications (src/tasks/notifications.py) does with each type / audience.
TYPE_SENDS = {'event_reminder', 'attendance_reminder', 'vote_reminder'}
AUDIENCE_SENDS = {'all_active', 'all_members', 'officers', 'pledges', 'committee'}


# ── Notification schedules ───────────────────────────────────────────────────

def _time(t):
    return t.strftime('%H:%M') if t else None


def _schedule(s, last_sent=None):
    return {
        'id': s.pk,
        'name': s.name,
        'notification_type': s.notification_type,
        'type_label': s.get_notification_type_display(),
        'description': s.description,
        'hours_before': s.hours_before,
        'send_at_time': _time(s.send_at_time),
        'send_email': s.send_email,
        'send_in_app': s.send_in_app,
        'target_audience': s.target_audience,
        'audience_label': s.get_target_audience_display(),
        'target_committee': {'id': s.target_committee.pk, 'name': s.target_committee.name}
        if s.target_committee_id and s.target_committee else None,
        'message_template': s.message_template,
        'email_subject_template': s.email_subject_template,
        'is_active': s.is_active,
        'created_by': _person(s.created_by),
        'created_at': _iso(s.created_at),
        'updated_at': _iso(s.updated_at),
        'last_sent_at': _iso(last_sent),
    }


def _send(log):
    return {
        'id': log.pk,
        'schedule_id': log.schedule_id,
        'title': log.title,
        'status': log.status,
        'status_label': log.get_status_display(),
        'sent_via_email': log.sent_via_email,
        'sent_via_in_app': log.sent_via_in_app,
        'recipient_count': log.recipient_count,
        'successful_count': log.successful_count,
        'failed_count': log.failed_count,
        'error_message': (log.error_message or '')[:500],
        'related_object_type': log.related_object_type,
        'created_at': _iso(log.created_at),
        'sent_at': _iso(log.sent_at),
    }


def _schedule_input(request, schedule=None):
    from src.view.notification_admin import ScheduleInputError, clean_schedule_input
    try:
        return clean_schedule_input(request.data, schedule)
    except ScheduleInputError as e:
        raise BadRequest(e.message, code='invalid_input')


def _one(schedule):
    from src.models import NotificationSchedule
    s = NotificationSchedule.objects.select_related('target_committee', 'created_by').get(pk=schedule.pk)
    last = s.logs.filter(status='sent').aggregate(at=Max('created_at'))['at']
    return _schedule(s, last)


class NotificationSchedulesView(SiteAdminAPIView):
    """GET: every schedule (the page's order), the form's choices, and the newest sends."""
    web_page = 'admin_v2_notification_schedules'

    def get(self, request):
        from src.models import Committee, NotificationLog, NotificationSchedule
        from src.view.notification_admin import SCHEDULE_HOURS_MAX, SCHEDULE_PLACEHOLDERS

        schedules = list(NotificationSchedule.objects.select_related('target_committee', 'created_by')
                         .order_by('notification_type', 'name'))
        last_sent = dict(NotificationLog.objects.filter(schedule__in=schedules, status='sent')
                         .values('schedule_id').annotate(at=Max('created_at'))
                         .values_list('schedule_id', 'at'))
        recent = NotificationLog.objects.filter(schedule__isnull=False).order_by('-created_at')[:RECENT_SENDS]
        return Response({
            'schedules': [_schedule(s, last_sent.get(s.pk)) for s in schedules],
            'types': [{'value': v, 'label': label, 'sends': v in TYPE_SENDS,
                       'placeholders': list(SCHEDULE_PLACEHOLDERS.get(v, ()))}
                      for v, label in NotificationSchedule.NOTIFICATION_TYPE_CHOICES],
            'audiences': [{'value': v, 'label': label, 'sends': v in AUDIENCE_SENDS}
                          for v, label in NotificationSchedule.TARGET_AUDIENCE_CHOICES],
            'committees': [{'id': c.pk, 'name': c.name}
                           for c in Committee.objects.filter(is_active=True).order_by('name')],
            'hours_max': SCHEDULE_HOURS_MAX,
            'time_zone': settings.TIME_ZONE,
            'recent_sends': [_send(log) for log in recent],
        })


class NotificationScheduleCreateView(SiteAdminAPIView):
    """POST the form: `create_notification_schedule`."""
    web_page = 'admin_v2_create_notification_schedule'

    def post(self, request):
        from src.view.notification_admin import create_notification_schedule
        schedule = create_notification_schedule(request.user, _schedule_input(request))
        return Response({'schedule': _one(schedule),
                         'message': f'Schedule "{schedule.name}" created successfully.'})


class NotificationScheduleUpdateView(SiteAdminAPIView):
    """POST the fields to change: `update_notification_schedule`."""
    web_page = 'admin_v2_update_notification_schedule'

    def post(self, request, schedule_id):
        from src.models import NotificationSchedule
        from src.view.notification_admin import update_notification_schedule
        schedule = get_object_or_404(NotificationSchedule, pk=schedule_id)
        update_notification_schedule(schedule, _schedule_input(request, schedule))
        return Response({'schedule': _one(schedule),
                         'message': f'Schedule "{schedule.name}" updated successfully.'})


class NotificationScheduleToggleView(SiteAdminAPIView):
    """
    POST ``{is_active}``: turn it on or off (`toggle_notification_schedule`).
    Already that way → nothing changes (a stale screen can't flip it back).
    Without ``is_active`` it flips, as the page's button does.
    """
    web_page = 'admin_v2_toggle_notification_schedule'

    def post(self, request, schedule_id):
        from src.models import NotificationSchedule
        from src.view.notification_admin import toggle_notification_schedule
        schedule = get_object_or_404(NotificationSchedule, pk=schedule_id)
        wanted = request.data.get('is_active') if hasattr(request.data, 'get') else None
        if wanted is not None and not isinstance(wanted, bool):
            raise BadRequest('Say whether it should be on or off.', code='invalid_input')
        changed = wanted is None or wanted != schedule.is_active
        if changed:
            toggle_notification_schedule(schedule)
        state = 'activated' if schedule.is_active else 'deactivated'
        return Response({'schedule': _one(schedule), 'changed': changed,
                         'message': f'Schedule "{schedule.name}" {state}.'})


class NotificationScheduleDeleteView(SiteAdminAPIView):
    """POST: `delete_notification_schedule` (its sends stay in the log)."""
    web_page = 'admin_v2_delete_notification_schedule'

    def post(self, request, schedule_id):
        from src.models import NotificationSchedule
        from src.view.notification_admin import delete_notification_schedule
        name = delete_notification_schedule(get_object_or_404(NotificationSchedule, pk=schedule_id))
        return Response({'ok': True, 'message': f'Schedule "{name}" deleted successfully.'})


# ── Site settings ────────────────────────────────────────────────────────────

#: The smallest whole number each integer setting takes from the app (0 otherwise).
SETTING_MIN = {'chat_active_users_poll_interval': 1000}
SETTING_TEXT_MAX = 5000


def setting_choices(setting):
    """The values a string setting may hold, if the code defines them: [(value, label)] or None."""
    if setting.key == '2fa_policy_mode':
        from src.view.admin_two_factor import TWO_FACTOR_POLICY_OPTIONS
        return list(TWO_FACTOR_POLICY_OPTIONS)
    return None


def setting_unit(setting):
    if setting.setting_type != 'integer':
        return None
    if setting.key.endswith('_poll_interval'):
        return 'ms'
    if setting.key.endswith('_hours'):
        return 'hours'
    return None


def strict_site_setting_value(setting, raw):
    """
    The app's check of a new value, by the setting's type, as the text the row
    stores. Raises BadRequest ``invalid_input``.
    """
    kind = setting.setting_type
    if kind == 'boolean':
        if isinstance(raw, bool):
            return 'true' if raw else 'false'
        if isinstance(raw, str) and raw.strip().lower() in ('true', 'false'):
            return raw.strip().lower()
        raise BadRequest('Turn it on or off.', code='invalid_input')
    if kind == 'integer':
        if isinstance(raw, bool):
            raise BadRequest('Enter a whole number.', code='invalid_input')
        if isinstance(raw, str):
            try:
                raw = int(raw.strip())
            except ValueError:
                raise BadRequest('Enter a whole number.', code='invalid_input')
        if not isinstance(raw, int):
            raise BadRequest('Enter a whole number.', code='invalid_input')
        low = SETTING_MIN.get(setting.key, 0)
        if raw < low:
            raise BadRequest(f'The smallest value is {low}.', code='invalid_input')
        return str(raw)
    if not isinstance(raw, str):
        raise BadRequest('Enter the value as text.', code='invalid_input')
    value = raw.strip()
    if len(value) > SETTING_TEXT_MAX:
        raise BadRequest(f'At most {SETTING_TEXT_MAX} characters.', code='invalid_input')
    if kind == 'json':
        try:
            json.loads(value)
        except ValueError as e:
            raise BadRequest(f'That isn’t valid JSON ({e.msg}, line {e.lineno}).', code='invalid_input')
        return value
    choices = setting_choices(setting)
    if choices is not None and value not in {v for v, _ in choices}:
        raise BadRequest('Choose one of the listed options.', code='invalid_input')
    return value


def _setting(s):
    choices = setting_choices(s)
    return {
        'id': s.pk,
        'key': s.key,
        'display_name': s.display_name,
        'description': s.description,
        'category': s.category,
        'category_label': s.get_category_display(),
        'setting_type': s.setting_type,
        'value': s.value,
        'default_value': s.default_value,
        'choices': [{'value': v, 'label': label} for v, label in choices] if choices is not None else None,
        'unit': setting_unit(s),
        'min': SETTING_MIN.get(s.key, 0) if s.setting_type == 'integer' else None,
        'last_modified_by': s.last_modified_by or None,
        'updated_at': _iso(s.updated_at),
    }


class SiteSettingsView(SiteAdminAPIView):
    """GET: the Maintenance page's settings, by category."""
    web_page = 'admin_v2_maintenance'

    def get(self, request):
        from src.models_feature_flags import SiteSetting
        from src.view.admin_v2 import seed_dashboard_defaults
        from src.view.admin_v2_ops import LEGACY_SETTINGS
        seed_dashboard_defaults()  # the same rows the Maintenance page creates
        rows = SiteSetting.objects.exclude(key__in=LEGACY_SETTINGS).order_by('category', 'display_name')
        return Response({'settings': [_setting(s) for s in rows]})


class SiteSettingUpdateView(SiteAdminAPIView):
    """
    POST ``{value}`` (typed: a bool for a switch, a number for a whole number,
    text otherwise) or ``{reset: true}`` for the default.
    """
    web_page = 'update_site_setting'

    def post(self, request, setting_id):
        from src.models_feature_flags import SiteSetting
        from src.view.admin_v2 import apply_site_setting_change, clean_site_setting_value
        from src.view.admin_v2_ops import LEGACY_SETTINGS
        setting = get_object_or_404(SiteSetting.objects.exclude(key__in=LEGACY_SETTINGS), pk=setting_id)
        data = request.data if hasattr(request.data, 'get') else {}
        if data.get('reset') is True:
            value = setting.default_value
        elif 'value' in data:
            value, error = clean_site_setting_value(setting, strict_site_setting_value(setting, data['value']))
            if error:  # can't happen after the strict check; kept so the page's rule always applies
                raise BadRequest(error, code='invalid_input')
        else:
            raise BadRequest('Send the new value.', code='invalid_input')
        changed = value != setting.value
        if changed:
            apply_site_setting_change(_http(request), request.user, setting, value)
        return Response({'setting': _setting(setting), 'changed': changed})
