"""
Site admin in the app, part three: src/mobile/views_site_admin_config.py.

Notification schedules and site settings, through the website pages' own
helpers (src/view/notification_admin.py, src/view/admin_v2.py), behind the Site
admin gate. Plus the website views those helpers were pulled out of, which must
behave as before.

    python manage.py test src.tests.mobile.test_site_admin_config
"""
import datetime
import json
import os
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import ActivityLog, Committee, NotificationLog, NotificationSchedule
from src.models_feature_flags import SiteSetting
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view import admin_v2

API = '/api/mobile/v1/'
ADMIN = 'sac-admin'
SECRET = 'the-admin-secret-0453'
PW = 'pw-' + ADMIN

READS = ('admin/notification-schedules/', 'admin/site-settings/')
WRITES = ('admin/notification-schedules/create/', 'admin/notification-schedules/{id}/update/',
          'admin/notification-schedules/{id}/toggle/', 'admin/notification-schedules/{id}/delete/',
          'admin/site-settings/{id}/')


def schedule_body(**over):
    body = {
        'name': 'Chapter reminder', 'notification_type': 'event_reminder', 'description': 'The night before',
        'hours_before': 24, 'send_at_time': None, 'send_email': True, 'send_in_app': False,
        'target_audience': 'all_active', 'target_committee_id': None,
        'message_template': '{event_name} is {event_date} at {event_time}',
        'email_subject_template': 'Reminder: {event_name}', 'is_active': True,
    }
    body.update(over)
    return body


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        # Not is_admin: neither website page checks it, so the app doesn't either.
        self.admin = make_user(ADMIN, is_admin=False, member_type='Officer', email='sac@parliament.test')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'ADMIN_V2_SECRET_KEY': SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.device, self.token, _ = make_device(self.admin)
        self.api = Client()

    def get(self, path, token=None):
        return self.api.get(API + path, **bearer(token or self.token))

    def post(self, path, data=None, token=None):
        return self.api.post(API + path, data if data is not None else {}, content_type='application/json',
                             **bearer(token or self.token))

    def code(self, r):
        return r.json().get('error')


class GateTests(_Base):

    def test_not_on_the_allowlist_is_404(self):
        member = make_user('sac-member', is_admin=True)
        _, token, _ = make_device(member)
        for path in READS:
            self.assertEqual(self.get(path, token=token).status_code, 404, path)
        for path in WRITES:
            self.assertEqual(self.post(path.format(id=1), token=token).status_code, 404, path)

    def test_locked_is_403_admin_locked(self):
        s = NotificationSchedule.objects.create(name='S', notification_type='custom', message_template='x')
        for path in READS:
            r = self.get(path)
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'), path)
        for path in WRITES:
            r = self.post(path.format(id=s.pk), schedule_body())
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'), path)
        s.refresh_from_db()
        self.assertEqual(NotificationSchedule.objects.count(), 1)
        self.assertTrue(s.is_active)

    def test_writes_refuse_get(self):
        self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        for path in WRITES:
            self.assertEqual(self.get(path.format(id=1)).status_code, 405, path)

    def test_expired_unlock_is_locked_again(self):
        self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        self.device.refresh_from_db()
        self.device.admin_unlocked_until = timezone.now() - datetime.timedelta(minutes=1)
        self.device.save(update_fields=['admin_unlocked_until'])
        r = self.post('admin/site-settings/1/', {'value': 'x'})
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'))


class _Unlocked(_Base):
    def setUp(self):
        super().setUp()
        r = self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        self.assertEqual(r.status_code, 200, r.content)
        ActivityLog.objects.all().delete()


# ── Notification schedules ───────────────────────────────────────────────────

class ScheduleTests(_Unlocked):

    def test_list_carries_choices_committees_and_recent_sends(self):
        committee = Committee.objects.create(name='Finance', code='SACF', is_active=True)
        Committee.objects.create(name='Gone', code='SACG', is_active=False)
        s = NotificationSchedule.objects.create(
            name='Vote nudge', notification_type='vote_reminder', message_template='Vote on {legislation_title}',
            target_audience='committee', target_committee=committee, send_at_time=datetime.time(9, 30))
        NotificationLog.objects.create(schedule=s, notification_type='vote_reminder', title='Vote nudge',
                                       message='m', status='sent', recipient_count=4, successful_count=4)
        NotificationLog.objects.create(notification_type='event_reminder', title='Not from a schedule',
                                       message='m', status='sent')
        r = self.get('admin/notification-schedules/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        row = body['schedules'][0]
        self.assertEqual((row['id'], row['type_label'], row['send_at_time'], row['target_committee']),
                         (s.pk, 'Vote Reminder', '09:30', {'id': committee.pk, 'name': 'Finance'}))
        self.assertIsNotNone(row['last_sent_at'])
        types = {t['value']: t for t in body['types']}
        self.assertTrue(types['event_reminder']['sends'])
        self.assertFalse(types['dues_reminder']['sends'])
        self.assertFalse(types['custom']['sends'])
        self.assertIn('legislation_title', types['vote_reminder']['placeholders'])
        self.assertEqual(types['event_reminder']['placeholders'],
                         ['event_name', 'event_date', 'event_time', 'event_location'])
        audiences = {a['value']: a['sends'] for a in body['audiences']}
        self.assertEqual((audiences['committee'], audiences['custom']), (True, False))
        self.assertEqual(body['committees'], [{'id': committee.pk, 'name': 'Finance'}])
        self.assertEqual([x['title'] for x in body['recent_sends']], ['Vote nudge'])
        self.assertEqual(body['recent_sends'][0]['recipient_count'], 4)

    def test_create(self):
        r = self.post('admin/notification-schedules/create/', schedule_body(send_at_time='18:45'))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Schedule "Chapter reminder" created successfully.')
        s = NotificationSchedule.objects.get()
        self.assertEqual((s.name, s.notification_type, s.hours_before, s.send_at_time, s.send_email,
                          s.send_in_app, s.created_by, s.email_subject_template),
                         ('Chapter reminder', 'event_reminder', 24, datetime.time(18, 45), True, False,
                          self.admin, 'Reminder: {event_name}'))
        self.assertEqual(r.json()['schedule']['id'], s.pk)

    def test_create_with_committee(self):
        committee = Committee.objects.create(name='Social', code='SACS')
        r = self.post('admin/notification-schedules/create/',
                      schedule_body(target_audience='committee', target_committee_id=committee.pk))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(NotificationSchedule.objects.get().target_committee, committee)

    def test_create_refusals(self):
        cases = [
            ({'name': '  '}, 'Name is required.'),
            ({'message_template': ''}, 'Message is required.'),
            ({'notification_type': 'birthday'}, 'Choose a type.'),
            ({'target_audience': 'everyone'}, 'Choose who it goes to.'),
            ({'hours_before': 0}, 'Hours before must be from 1 to 8760.'),
            ({'hours_before': '24'}, 'Hours before must be a whole number.'),
            ({'hours_before': True}, 'Hours before must be a whole number.'),
            ({'send_at_time': '25:00'}, 'The time of day must look like 09:00.'),
            ({'send_email': 'yes'}, 'Say whether to send an email.'),
            ({'name': 'x' * 101}, 'Name can be at most 100 characters.'),
            ({'target_audience': 'committee'}, 'Choose the committee it goes to.'),
            ({'target_audience': 'committee', 'target_committee_id': 99999}, 'Choose the committee it goes to.'),
        ]
        for over, message in cases:
            r = self.post('admin/notification-schedules/create/', schedule_body(**over))
            self.assertEqual((r.status_code, self.code(r), r.json()['detail']), (400, 'invalid_input', message), over)
        r = self.post('admin/notification-schedules/create/', [1, 2])
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        self.assertFalse(NotificationSchedule.objects.exists())

    def test_update(self):
        committee = Committee.objects.create(name='Social', code='SACU')
        s = NotificationSchedule.objects.create(name='Old', notification_type='event_reminder',
                                                message_template='x', target_audience='committee',
                                                target_committee=committee, send_at_time=datetime.time(8, 0))
        r = self.post(f'admin/notification-schedules/{s.pk}/update/',
                      schedule_body(name='New', notification_type='attendance_reminder', hours_before=2))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Schedule "New" updated successfully.')
        s.refresh_from_db()
        self.assertEqual((s.name, s.notification_type, s.hours_before, s.send_at_time, s.target_committee),
                         ('New', 'attendance_reminder', 2, None, None))

    def test_partial_update_keeps_the_rest_and_checks_the_committee(self):
        committee = Committee.objects.create(name='Social', code='SACP')
        s = NotificationSchedule.objects.create(name='Keep', notification_type='event_reminder',
                                                message_template='x', target_audience='committee',
                                                target_committee=committee, hours_before=6)
        r = self.post(f'admin/notification-schedules/{s.pk}/update/', {'description': 'Changed'})
        self.assertEqual(r.status_code, 200, r.content)
        s.refresh_from_db()
        self.assertEqual((s.description, s.name, s.hours_before, s.target_committee),
                         ('Changed', 'Keep', 6, committee))
        r = self.post(f'admin/notification-schedules/{s.pk}/update/', {'target_committee_id': None})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Choose the committee it goes to.'))

    def test_update_missing_is_404(self):
        r = self.post('admin/notification-schedules/99999/update/', schedule_body())
        self.assertEqual(r.status_code, 404)

    def test_toggle(self):
        s = NotificationSchedule.objects.create(name='T', notification_type='event_reminder', message_template='x')
        r = self.post(f'admin/notification-schedules/{s.pk}/toggle/', {'is_active': False})
        self.assertEqual((r.json()['changed'], r.json()['message']), (True, 'Schedule "T" deactivated.'))
        s.refresh_from_db()
        self.assertFalse(s.is_active)
        # Already off: a stale screen asking for off again changes nothing.
        r = self.post(f'admin/notification-schedules/{s.pk}/toggle/', {'is_active': False})
        self.assertEqual(r.json()['changed'], False)
        s.refresh_from_db()
        self.assertFalse(s.is_active)
        # No state named: it flips, like the page's button.
        r = self.post(f'admin/notification-schedules/{s.pk}/toggle/')
        self.assertEqual((r.json()['schedule']['is_active'], r.json()['message']), (True, 'Schedule "T" activated.'))
        r = self.post(f'admin/notification-schedules/{s.pk}/toggle/', {'is_active': 'no'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))

    def test_delete_keeps_its_sends(self):
        s = NotificationSchedule.objects.create(name='D', notification_type='event_reminder', message_template='x')
        log = NotificationLog.objects.create(schedule=s, notification_type='event_reminder', title='D', message='m')
        r = self.post(f'admin/notification-schedules/{s.pk}/delete/')
        self.assertEqual(r.json(), {'ok': True, 'message': 'Schedule "D" deleted successfully.'})
        self.assertFalse(NotificationSchedule.objects.exists())
        log.refresh_from_db()
        self.assertIsNone(log.schedule_id)
        self.assertEqual(self.post(f'admin/notification-schedules/{s.pk}/delete/').status_code, 404)


# ── Site settings ────────────────────────────────────────────────────────────

class SiteSettingTests(_Unlocked):

    def setUp(self):
        super().setUp()
        self.policy = SiteSetting.objects.create(
            key='2fa_policy_mode', display_name='2FA Policy Mode', category='security', setting_type='string',
            value='none', default_value='none')
        self.blob = SiteSetting.objects.create(
            key='sac_blob', display_name='Blob', category='display', setting_type='json',
            value='{}', default_value='{"a": 1}')
        SiteSetting.objects.create(key='chat_active_poll_interval', display_name='Legacy', category='chat',
                                   setting_type='integer', value='3000', default_value='3000')

    def row(self, key):
        return SiteSetting.objects.get(key=key)

    def test_list_seeds_groups_and_types(self):
        r = self.get('admin/site-settings/')
        self.assertEqual(r.status_code, 200, r.content)
        rows = {s['key']: s for s in r.json()['settings']}
        self.assertNotIn('chat_active_poll_interval', rows)   # legacy, as on the Maintenance page
        self.assertIn('event_reminders_enabled', rows)        # seeded, as on the Maintenance page
        poll = rows['chat_active_users_poll_interval']
        self.assertEqual((poll['unit'], poll['min'], poll['category_label']), ('ms', 1000, 'Chat Settings'))
        self.assertEqual(rows['event_reminder_default_hours']['unit'], 'hours')
        self.assertEqual([c['value'] for c in rows['2fa_policy_mode']['choices']],
                         ['none', 'admins_only', 'officers_and_admins', 'all_members', 'custom'])
        self.assertIsNone(rows['sac_blob']['choices'])
        self.assertEqual([s['category'] for s in r.json()['settings']],
                         sorted(s['category'] for s in r.json()['settings']))

    def _seeded(self, key):
        self.get('admin/site-settings/')
        return self.row(key)

    def test_boolean(self):
        s = self._seeded('event_reminders_enabled')
        self.assertTrue(SiteSetting.get_setting('event_reminders_enabled'))   # now cached
        r = self.post(f'admin/site-settings/{s.pk}/', {'value': False})
        self.assertEqual((r.status_code, r.json()['changed'], r.json()['setting']['value']), (200, True, 'false'))
        self.assertIs(SiteSetting.get_setting('event_reminders_enabled'), False)   # cache dropped
        s.refresh_from_db()
        self.assertEqual(s.last_modified_by, self.admin.get_display_name())
        log = ActivityLog.objects.get(action_type='setting_change')
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.description, f'{self.admin.get_display_name()} changed Event Reminder Notifications '
                                          f'from true to false')
        for bad in ('maybe', 1, None):
            r = self.post(f'admin/site-settings/{s.pk}/', {'value': bad})
            self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'), bad)

    def test_integer(self):
        s = self._seeded('chat_active_users_poll_interval')
        r = self.post(f'admin/site-settings/{s.pk}/', {'value': 8000})
        self.assertEqual(r.json()['setting']['value'], '8000')
        self.assertEqual(SiteSetting.get_setting('chat_active_users_poll_interval'), 8000)
        r = self.post(f'admin/site-settings/{s.pk}/', {'value': ' 9000 '})
        self.assertEqual(r.json()['setting']['value'], '9000')
        for bad, message in ((999, 'The smallest value is 1000.'), ('fast', 'Enter a whole number.'),
                             (True, 'Enter a whole number.'), (2.5, 'Enter a whole number.')):
            r = self.post(f'admin/site-settings/{s.pk}/', {'value': bad})
            self.assertEqual((r.status_code, r.json()['detail']), (400, message), bad)
        self.assertEqual(self.row('chat_active_users_poll_interval').value, '9000')
        hours = self._seeded('event_reminder_default_hours')
        r = self.post(f'admin/site-settings/{hours.pk}/', {'value': -1})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'The smallest value is 0.'))

    def test_json(self):
        r = self.post(f'admin/site-settings/{self.blob.pk}/', {'value': '{"a": [1, 2]}'})
        self.assertEqual((r.status_code, r.json()['setting']['value']), (200, '{"a": [1, 2]}'))
        self.assertEqual(SiteSetting.get_setting('sac_blob'), {'a': [1, 2]})
        r = self.post(f'admin/site-settings/{self.blob.pk}/', {'value': '{"a": '})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        self.assertIn('isn’t valid JSON', r.json()['detail'])
        r = self.post(f'admin/site-settings/{self.blob.pk}/', {'value': {'a': 1}})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Enter the value as text.'))

    def test_choice_setting(self):
        SiteSetting.get_setting('2fa_policy_mode')
        r = self.post(f'admin/site-settings/{self.policy.pk}/', {'value': 'admins_only'})
        self.assertEqual(r.json()['setting']['value'], 'admins_only')
        self.assertEqual(SiteSetting.get_setting('2fa_policy_mode', 'none'), 'admins_only')
        r = self.post(f'admin/site-settings/{self.policy.pk}/', {'value': 'everyone'})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Choose one of the listed options.'))
        self.assertEqual(self.row('2fa_policy_mode').value, 'admins_only')
        self.assertEqual(ActivityLog.objects.filter(action_type='setting_change').count(), 1)

    def test_reset_and_unchanged(self):
        r = self.post(f'admin/site-settings/{self.blob.pk}/', {'reset': True})
        self.assertEqual((r.json()['changed'], r.json()['setting']['value']), (True, '{"a": 1}'))
        r = self.post(f'admin/site-settings/{self.blob.pk}/', {'value': '{"a": 1}'})
        self.assertEqual(r.json()['changed'], False)
        self.assertEqual(ActivityLog.objects.filter(action_type='setting_change').count(), 1)
        r = self.post(f'admin/site-settings/{self.blob.pk}/', {})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))

    def test_legacy_and_missing_are_404(self):
        legacy = self.row('chat_active_poll_interval')
        self.assertEqual(self.post(f'admin/site-settings/{legacy.pk}/', {'value': 1}).status_code, 404)
        self.assertEqual(self.post('admin/site-settings/99999/', {'value': 1}).status_code, 404)


# ── The website views (unchanged behaviour) ──────────────────────────────────

class WebViewsTests(TestCase):

    def setUp(self):
        cache.clear()
        self.admin = make_user('sac-web', is_admin=True, member_type='Officer')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {'sac-web'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    def post_json(self, name, body, *args):
        return self.client.post(reverse(name, args=args), json.dumps(body), content_type='application/json')

    def test_schedule_page_and_actions(self):
        self.assertEqual(self.client.get(reverse('admin_v2_notification_schedules')).status_code, 200)
        r = self.post_json('admin_v2_create_notification_schedule', {'name': 'W', 'notification_type': 'custom'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'message_template is required'))
        # The page's form: no description/committee/time; defaults for what it leaves out.
        r = self.post_json('admin_v2_create_notification_schedule', {
            'name': 'W', 'notification_type': 'event_reminder', 'message_template': 'm', 'hours_before': 3,
            'target_audience': 'officers', 'send_email': False, 'send_in_app': True, 'description': ''})
        self.assertTrue(r.json()['success'])
        s = NotificationSchedule.objects.get(pk=r.json()['schedule_id'])
        self.assertEqual((s.hours_before, s.target_audience, s.send_email, s.is_active, s.created_by,
                          s.send_at_time, s.email_subject_template),
                         (3, 'officers', False, True, self.admin, None, ''))
        r = self.post_json('admin_v2_update_notification_schedule', {'name': 'W2', 'target_committee_id': 0}, s.pk)
        self.assertEqual(r.json()['message'], 'Schedule "W2" updated successfully.')
        s.refresh_from_db()
        self.assertEqual((s.name, s.target_committee_id, s.hours_before), ('W2', None, 3))
        r = self.client.post(reverse('admin_v2_toggle_notification_schedule', args=[s.pk]))
        self.assertEqual(r.json()['is_active'], False)
        self.assertEqual(self.client.get(reverse('admin_v2_toggle_notification_schedule', args=[s.pk])).status_code,
                         405)
        r = self.client.post(reverse('admin_v2_delete_notification_schedule', args=[s.pk]))
        self.assertEqual(r.json()['message'], 'Schedule "W2" deleted successfully.')
        self.assertFalse(NotificationSchedule.objects.exists())

    def test_site_setting_update(self):
        s = SiteSetting.objects.create(key='sac_web_int', display_name='Web int', category='general',
                                       setting_type='integer', value='5', default_value='5')
        flag = SiteSetting.objects.create(key='sac_web_bool', display_name='Web bool', category='general',
                                          setting_type='boolean', value='false', default_value='false')
        url = reverse('update_site_setting', args=[s.pk])
        r = self.client.post(url, {'value': 'abc', 'next': 'maintenance'})
        self.assertRedirects(r, reverse('admin_v2_maintenance'), fetch_redirect_response=False)
        s.refresh_from_db()
        self.assertEqual(s.value, '5')
        self.assertFalse(ActivityLog.objects.filter(action_type='setting_change').exists())
        SiteSetting.get_setting('sac_web_int')
        r = self.client.post(url, {'value': ' 7 '})
        self.assertRedirects(r, reverse('admin_v2_dashboard'), fetch_redirect_response=False)
        self.assertEqual(SiteSetting.get_setting('sac_web_int'), 7)
        s.refresh_from_db()
        self.assertEqual(s.last_modified_by, self.admin.get_display_name())
        self.assertTrue(ActivityLog.objects.filter(action_type='setting_change',
                                                   description__endswith='changed Web int from 5 to 7').exists())
        self.client.post(reverse('update_site_setting', args=[flag.pk]), {'value': 'on'})
        flag.refresh_from_db()
        self.assertEqual(flag.value, 'true')
        # GET changes nothing.
        self.client.get(reverse('update_site_setting', args=[s.pk]))
        self.assertEqual(ActivityLog.objects.filter(action_type='setting_change').count(), 2)

    def test_two_factor_policy_still_validates(self):
        r = self.post_json('admin_v2_two_factor_update_policy', {'policy': 'nobody'})
        self.assertEqual(r.status_code, 400)
        r = self.post_json('admin_v2_two_factor_update_policy', {'policy': 'officers_and_admins'})
        self.assertEqual(r.json(), {'success': True, 'policy': 'officers_and_admins'})
