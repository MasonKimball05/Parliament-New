"""
Mobile plan, Phase 1 — native push.

* POST/DELETE /api/mobile/v1/devices/push-token/ (format check, reinstall
  hand-over, bearer auth only);
* src/mobile/push.py talking to Expo directly (batches of 100, headers,
  access token, DeviceNotRegistered clears the token, tickets stored);
* tasks.check_expo_push_receipts;
* send_push_notification fanning out to web AND native, each retried on its
  own so a retry never re-sends what already went out; web push 404 = gone;
* the paths that used to never push (create_notification, chat mentions,
  security alerts, scheduled reminders) now do, respecting prefs['push'];
* event reminders link members to the calendar, not an officer page;
* system check src.W020.
"""
import json
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

import requests
from django.core.checks import run_checks
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.mobile import push as expo
from src.mobile.tokens import create_device
from src.models import (
    ChatChannel, ChatReadReceipt, Committee, Event, Notification,
    NotificationSchedule, ParliamentUser, PushSubscription,
)
from src.models.mobile import MobileDevice, MobilePushTicket

TOKEN_A = 'ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]'
TOKEN_B = 'ExpoPushToken[bbbbbbbbbbbbbbbbbbbbbb]'
VAPID = dict(VAPID_PRIVATE_KEY='test-key', VAPID_CLAIMS={'sub': 'mailto:x@example.com'})


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid.replace('-', '_'),
                  member_type='Member', member_status='Active')
    fields.update(extra)
    return ParliamentUser.objects.create(**fields)


def make_device(user, token=''):
    device, access, _ = create_device(user, 'password_totp', 'ios')
    if token:
        MobileDevice.objects.filter(pk=device.pk).update(push_token=token)
        device.push_token = token
    return device, access


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


def ok_tickets(payload):
    return FakeResponse(200, {'data': [{'status': 'ok', 'id': f'tk-{i}-{m["to"]}'}
                                       for i, m in enumerate(payload)]})


def expo_ok(url, json=None, headers=None, timeout=None):
    return ok_tickets(json)


@contextmanager
def eager_retries():
    """
    Let eager tasks retry in-process. Tests run with task_eager_propagates on,
    under which Celery re-raises ``Retry`` instead of re-applying the task;
    a worker retries for real, so this only restores production behaviour.
    """
    from Parliament.celery import app
    # The app reads Django settings under the CELERY_ namespace, so the
    # namespaced key is the one that wins.
    old = app.conf.task_eager_propagates
    app.conf.CELERY_TASK_EAGER_PROPAGATES = False
    try:
        assert app.conf.task_eager_propagates is False
        yield
    finally:
        app.conf.CELERY_TASK_EAGER_PROPAGATES = old


def set_push_pref(user, key, value):
    prefs = user.preferences
    prefs.prefs.setdefault('push', {})[key] = value
    prefs.save()


class PushTokenEndpointTests(TestCase):
    url = '/api/mobile/v1/devices/push-token/'

    def setUp(self):
        self.user = make_user('pt-user')
        self.device, self.access = make_device(self.user)
        self.client = Client()

    def _auth(self, access=None):
        return {'HTTP_AUTHORIZATION': f'Bearer {access or self.access}'}

    def test_reverse_name(self):
        self.assertEqual(reverse('mobile:mobile_push_token'), self.url)

    def test_registers_valid_token(self):
        for token in (TOKEN_A, TOKEN_B):
            r = self.client.post(self.url, {'push_token': token},
                                 content_type='application/json', **self._auth())
            self.assertEqual(r.status_code, 200, r.content)
            self.device.refresh_from_db()
            self.assertEqual(self.device.push_token, token)

    def test_rejects_bad_tokens(self):
        for bad in ('', 'abc', 'ExponentPushToken[]', 'ExponentPushToken[a b]',
                    'ExponentPushToken[' + 'a' * 300 + ']', None, 42):
            r = self.client.post(self.url, json.dumps({'push_token': bad}),
                                 content_type='application/json', **self._auth())
            self.assertEqual(r.status_code, 400, bad)
        self.device.refresh_from_db()
        self.assertEqual(self.device.push_token, '')

    def test_requires_bearer_auth(self):
        other = make_user('pt-session')
        self.client.force_login(other)
        r = self.client.post(self.url, {'push_token': TOKEN_A}, content_type='application/json')
        self.assertIn(r.status_code, (401, 403))
        self.assertFalse(MobileDevice.objects.filter(push_token=TOKEN_A).exists())

    def test_reinstall_clears_token_on_other_live_device(self):
        other_user = make_user('pt-other')
        old, _ = make_device(other_user, TOKEN_A)
        r = self.client.post(self.url, {'push_token': TOKEN_A},
                             content_type='application/json', **self._auth())
        self.assertEqual(r.status_code, 200)
        old.refresh_from_db()
        self.device.refresh_from_db()
        self.assertEqual(old.push_token, '')
        self.assertEqual(self.device.push_token, TOKEN_A)
        self.assertEqual(MobileDevice.objects.filter(push_token=TOKEN_A).count(), 1)

    def test_delete_clears(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(push_token=TOKEN_A)
        r = self.client.delete(self.url, **self._auth())
        self.assertEqual(r.status_code, 204)
        self.device.refresh_from_db()
        self.assertEqual(self.device.push_token, '')


class ExpoSenderTests(TestCase):
    def setUp(self):
        self.user = make_user('ex-user')

    def test_batches_of_100(self):
        devices = [make_device(self.user, f'ExponentPushToken[t{i:03d}]')[0] for i in range(101)]
        with mock.patch('src.mobile.push.requests.post', side_effect=expo_ok) as post:
            ok = expo.send_to_devices(devices, 'T', 'B', data={'url': '/vote/'}, badge=3)
        self.assertEqual(post.call_count, 2)
        sizes = [len(c.kwargs['json']) for c in post.call_args_list]
        self.assertEqual(sizes, [100, 1])
        self.assertEqual(ok, 101)
        self.assertEqual(MobilePushTicket.objects.count(), 101)
        first = post.call_args_list[0]
        self.assertEqual(first.args[0], 'https://exp.host/--/api/v2/push/send')
        self.assertEqual(first.kwargs['timeout'], 10)
        msg = first.kwargs['json'][0]
        self.assertEqual(msg['title'], 'T')
        self.assertEqual(msg['body'], 'B')
        self.assertEqual(msg['sound'], 'default')
        self.assertEqual(msg['badge'], 3)
        self.assertEqual(msg['channelId'], 'default')
        self.assertEqual(msg['data'], {'url': '/vote/'})

    def test_headers_without_and_with_access_token(self):
        device, _ = make_device(self.user, TOKEN_A)
        with override_settings(EXPO_ACCESS_TOKEN=''), \
                mock.patch('src.mobile.push.requests.post', side_effect=expo_ok) as post:
            expo.send_to_devices([device], 'T', 'B')
        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers['Accept'], 'application/json')
        self.assertIn('gzip', headers['Accept-Encoding'])
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertNotIn('Authorization', headers)
        with override_settings(EXPO_ACCESS_TOKEN='sekrit'), \
                mock.patch('src.mobile.push.requests.post', side_effect=expo_ok) as post:
            expo.send_to_devices([device], 'T', 'B')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer sekrit')

    def test_device_not_registered_clears_token(self):
        gone, _ = make_device(self.user, TOKEN_A)
        fine, _ = make_device(self.user, TOKEN_B)
        body = {'data': [
            {'status': 'error', 'message': 'not registered',
             'details': {'error': 'DeviceNotRegistered'}},
            {'status': 'ok', 'id': 'tk-fine'},
        ]}
        with mock.patch('src.mobile.push.requests.post', return_value=FakeResponse(200, body)):
            ok = expo.send_to_devices([gone, fine], 'T', 'B')
        self.assertEqual(ok, 1)
        gone.refresh_from_db()
        fine.refresh_from_db()
        self.assertEqual(gone.push_token, '')
        self.assertEqual(fine.push_token, TOKEN_B)
        self.assertEqual(list(MobilePushTicket.objects.values_list('ticket_id', 'device_id')),
                         [('tk-fine', fine.pk)])

    def test_transient_errors_raise(self):
        device, _ = make_device(self.user, TOKEN_A)
        for effect in (requests.ConnectionError('down'), requests.Timeout('slow'),
                       FakeResponse(503, {}), FakeResponse(429, {})):
            kwargs = {'side_effect': effect} if isinstance(effect, Exception) else {'return_value': effect}
            with mock.patch('src.mobile.push.requests.post', **kwargs):
                with self.assertRaises(expo.ExpoTransientError):
                    expo.send_to_devices([device], 'T', 'B')

    def test_client_error_is_not_retried(self):
        device, _ = make_device(self.user, TOKEN_A)
        with mock.patch('src.mobile.push.requests.post',
                        return_value=FakeResponse(401, {'errors': [{'code': 'UNAUTHORIZED'}]})):
            with self.assertLogs('src.mobile.push', 'ERROR'):
                self.assertEqual(expo.send_to_devices([device], 'T', 'B'), 0)


class ReceiptTaskTests(TestCase):
    def setUp(self):
        self.user = make_user('rc-user')

    def _ticket(self, device, ticket_id, age, token=None):
        t = MobilePushTicket.objects.create(
            ticket_id=ticket_id, device=device, push_token=token or device.push_token)
        MobilePushTicket.objects.filter(pk=t.pk).update(created_at=timezone.now() - age)
        return t

    def test_receipts(self):
        from src.tasks.mobile_push import check_expo_push_receipts
        gone, _ = make_device(self.user, TOKEN_A)
        fine, _ = make_device(self.user, TOKEN_B)
        renewed, _ = make_device(self.user, 'ExponentPushToken[new]')
        self._ticket(gone, 'r-gone', timedelta(minutes=20))
        self._ticket(fine, 'r-fine', timedelta(minutes=20))
        self._ticket(fine, 'r-pending', timedelta(minutes=20))
        self._ticket(renewed, 'r-oldtoken', timedelta(minutes=20), token='ExponentPushToken[old]')
        self._ticket(fine, 'r-young', timedelta(minutes=5))
        self._ticket(fine, 'r-stale', timedelta(hours=25))
        body = {'data': {
            'r-gone': {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}},
            'r-fine': {'status': 'ok'},
            'r-oldtoken': {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}},
        }}
        with mock.patch('src.mobile.push.requests.post',
                        return_value=FakeResponse(200, body)) as post:
            result = check_expo_push_receipts()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 'https://exp.host/--/api/v2/push/getReceipts')
        self.assertEqual(sorted(post.call_args.kwargs['json']['ids']),
                         ['r-fine', 'r-gone', 'r-oldtoken', 'r-pending'])
        self.assertEqual(result, {'processed': 3, 'cleared': 1, 'stale': 1})
        gone.refresh_from_db()
        renewed.refresh_from_db()
        self.assertEqual(gone.push_token, '')
        self.assertEqual(renewed.push_token, 'ExponentPushToken[new]')  # stale receipt, new token
        self.assertEqual(sorted(MobilePushTicket.objects.values_list('ticket_id', flat=True)),
                         ['r-pending', 'r-young'])

    def test_receipt_requests_hold_at_most_1000_ids(self):
        from src.tasks.mobile_push import check_expo_push_receipts
        device, _ = make_device(self.user, TOKEN_A)
        MobilePushTicket.objects.bulk_create([
            MobilePushTicket(ticket_id=f'b-{i}', device=device, push_token=TOKEN_A)
            for i in range(1001)])
        MobilePushTicket.objects.update(created_at=timezone.now() - timedelta(minutes=30))
        with mock.patch('src.mobile.push.requests.post',
                        return_value=FakeResponse(200, {'data': {}})) as post:
            check_expo_push_receipts()
        self.assertEqual([len(c.kwargs['json']['ids']) for c in post.call_args_list], [1000, 1])

    def test_transient_failure_keeps_tickets(self):
        from src.tasks.mobile_push import check_expo_push_receipts
        device, _ = make_device(self.user, TOKEN_A)
        self._ticket(device, 'r-1', timedelta(minutes=20))
        with mock.patch('src.mobile.push.requests.post', side_effect=requests.Timeout()):
            with self.assertLogs('src.tasks.mobile_push', 'WARNING'):
                check_expo_push_receipts()
        self.assertTrue(MobilePushTicket.objects.filter(ticket_id='r-1').exists())

    def test_task_is_scheduled(self):
        from src.management.commands.setup_celery_schedules import SCHEDULES
        spec = next(s for s in SCHEDULES if s['task'] == 'tasks.check_expo_push_receipts')
        self.assertEqual(spec['interval']['every'], 15)
        from Parliament.celery import app
        self.assertIn('tasks.check_expo_push_receipts', app.tasks)


@override_settings(**VAPID)
class FanOutTests(TestCase):
    def setUp(self):
        self.user = make_user('fo-user')
        self.sub = PushSubscription.objects.create(
            user=self.user, endpoint='https://push.example/a', p256dh='k', auth='a')
        self.device, _ = make_device(self.user, TOKEN_A)
        Notification.objects.create(recipient=self.user, notification_type='announcement', title='x')

    def test_sends_to_web_and_native(self):
        from src.tasks import send_push_notification
        with mock.patch('pywebpush.webpush') as webpush, \
                mock.patch('src.mobile.push.requests.post', side_effect=expo_ok) as post:
            send_push_notification.delay(self.user.pk, 'Vote open', 'Go vote', '/legislation/detail/7/')
        self.assertEqual(webpush.call_count, 1)
        self.assertEqual(json.loads(webpush.call_args.kwargs['data'])['url'], '/legislation/detail/7/')
        self.assertEqual(post.call_count, 1)
        msg = post.call_args.kwargs['json'][0]
        self.assertEqual(msg['to'], TOKEN_A)
        self.assertEqual(msg['data'], {'url': '/legislation/detail/7/', 'screen': 'vote', 'id': 7})
        self.assertEqual(msg['badge'], 1)

    def test_revoked_and_tokenless_devices_skipped(self):
        from src.tasks import send_native_push
        self.device.revoke('test')
        make_device(self.user)  # no token
        with mock.patch('src.mobile.push.requests.post', side_effect=expo_ok) as post:
            send_native_push.delay(self.user.pk, 'T', 'B')
        post.assert_not_called()

    def test_native_retry_does_not_resend_web_push(self):
        from src.tasks import send_push_notification
        responses = [FakeResponse(503, {}), None]

        def flaky(url, json=None, headers=None, timeout=None):
            r = responses.pop(0)
            return r or ok_tickets(json)

        with eager_retries(), mock.patch('pywebpush.webpush') as webpush, \
                mock.patch('src.mobile.push.requests.post', side_effect=flaky) as post:
            send_push_notification.delay(self.user.pk, 'T', 'B')
        self.assertEqual(post.call_count, 2)       # retried
        self.assertEqual(webpush.call_count, 1)    # web push sent once
        self.assertEqual(MobilePushTicket.objects.count(), 1)

    def test_native_retry_skips_devices_already_sent(self):
        from src.tasks import send_native_push
        devices = [self.device] + [make_device(self.user, f'ExponentPushToken[m{i:03d}]')[0]
                                   for i in range(100)]   # 101 devices → 2 batches
        calls = []

        def second_batch_fails_once(url, json=None, headers=None, timeout=None):
            calls.append([m['to'] for m in json])
            if len(calls) == 2:
                return FakeResponse(502, {})
            return ok_tickets(json)

        with eager_retries(), \
                mock.patch('src.mobile.push.requests.post', side_effect=second_batch_fails_once):
            send_native_push.delay(self.user.pk, 'T', 'B')
        self.assertEqual([len(c) for c in calls], [100, 1, 1])
        self.assertEqual(calls[1], calls[2])
        sent = [t for c in calls for t in c]
        self.assertEqual(len(set(sent)), len(devices))

    def test_web_retry_only_resends_failed_subscriptions(self):
        from pywebpush import WebPushException
        from src.tasks import send_web_push
        other = PushSubscription.objects.create(
            user=self.user, endpoint='https://push.example/b', p256dh='k', auth='a')
        attempts = []

        def flaky(subscription_info, **kw):
            attempts.append(subscription_info['endpoint'])
            if subscription_info['endpoint'] == other.endpoint and attempts.count(other.endpoint) == 1:
                raise WebPushException('boom', response=FakeResponse(500))

        with eager_retries(), mock.patch('pywebpush.webpush', side_effect=flaky):
            send_web_push.delay(self.user.pk, 'T', 'B')
        self.assertEqual(attempts.count(self.sub.endpoint), 1)
        self.assertEqual(attempts.count(other.endpoint), 2)

    def test_web_404_and_410_delete_subscription(self):
        from pywebpush import WebPushException
        from src.tasks import send_web_push
        other = PushSubscription.objects.create(
            user=self.user, endpoint='https://push.example/b', p256dh='k', auth='a')

        def gone(subscription_info, **kw):
            status = 404 if subscription_info['endpoint'] == self.sub.endpoint else 410
            raise WebPushException('gone', response=FakeResponse(status))

        with mock.patch('pywebpush.webpush', side_effect=gone):
            send_web_push.delay(self.user.pk, 'T', 'B')
        self.assertFalse(PushSubscription.objects.filter(pk__in=[self.sub.pk, other.pk]).exists())

    def test_gives_up_after_max_retries(self):
        from src.tasks import send_native_push
        with eager_retries(), mock.patch('src.mobile.push.requests.post',
                                         return_value=FakeResponse(500, {})) as post:
            with self.assertLogs('src.tasks.notifications', 'WARNING'):
                send_native_push.delay(self.user.pk, 'T', 'B')
        self.assertEqual(post.call_count, 3)   # first try + max_retries=2


class RoutedPathsPushTests(TestCase):
    """Paths that created a Notification row but never pushed."""

    def setUp(self):
        self.user = make_user('rp-user')

    def _pushes(self, fn):
        with mock.patch('src.tasks.send_push_notification') as task:
            fn()
        return [c.kwargs for c in task.delay.call_args_list]

    def test_create_notification_pushes(self):
        from src.notification_service import create_notification
        calls = self._pushes(lambda: create_notification(
            self.user, 'role_kb_available', 'Notes', 'msg', link='/vote/'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['user_id'], self.user.pk)
        self.assertEqual(calls[0]['url'], '/vote/')

    def test_excuse_push_respects_pref(self):
        from src.notification_service import create_notification
        call = lambda: create_notification(self.user, 'excuse_reviewed', 'Approved', link='/excuses/')
        self.assertEqual(len(self._pushes(call)), 1)
        set_push_pref(self.user, 'excuses', False)
        self.user = ParliamentUser.objects.get(pk=self.user.pk)
        self.assertEqual(self._pushes(call), [])
        # the in-app row is still created
        self.assertEqual(Notification.objects.filter(
            recipient=self.user, notification_type='excuse_reviewed').count(), 2)

    def test_security_alert_pushes_and_respects_pref(self):
        from src.security_notifications import notify_user_security_event
        calls = self._pushes(lambda: notify_user_security_event(self.user, 'New sign-in', 'Body'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['title'], 'New sign-in')
        set_push_pref(self.user, 'security', False)
        user = ParliamentUser.objects.get(pk=self.user.pk)
        self.assertEqual(self._pushes(lambda: notify_user_security_event(user, 'X', 'Y')), [])
        self.assertEqual(Notification.objects.filter(
            recipient=self.user, notification_type='security').count(), 2)

    def test_new_pref_defaults_on(self):
        p = self.user.preferences
        for attr in ('push_excuses', 'push_security', 'push_reminders', 'push_mentions'):
            self.assertTrue(getattr(p, attr))
        p.prefs = {}
        for attr in ('push_excuses', 'push_security', 'push_reminders', 'push_mentions'):
            self.assertTrue(getattr(p, attr))

    def test_scheduled_reminder_pushes_respecting_pref(self):
        from src.tasks.notifications import fire_scheduled_notifications
        opted_out = make_user('rp-optout')
        set_push_pref(opted_out, 'reminders', False)
        Event.objects.create(title='Chapter', description='',
                             date_time=timezone.now() + timedelta(hours=2), created_by=self.user)
        NotificationSchedule.objects.create(
            name='Event soon', notification_type='event_reminder', hours_before=24,
            send_email=False, send_in_app=True, target_audience='all_active',
            message_template='{event_name} soon')
        calls = self._pushes(fire_scheduled_notifications)
        self.assertEqual([c['user_id'] for c in calls], [self.user.pk])
        self.assertEqual(Notification.objects.filter(recipient=opted_out).count(), 1)


class ChatMentionPushTests(TestCase):
    def setUp(self):
        from src.models_feature_flags import FeatureFlag
        FeatureFlag.objects.update_or_create(name='chats', defaults={'is_enabled': True})
        self.sender = make_user('cm-sender')
        self.member = make_user('cm-member')
        committee = Committee.objects.create(name='Kai', code='CMK')
        committee.members.add(self.sender, self.member)
        self.channel = ChatChannel.objects.create(
            name='Kai', channel_type='committee', access_type='committee', committee=committee)
        self.client = Client()
        self.client.force_login(self.sender)

    def _send(self, text):
        with mock.patch('src.tasks.send_push_notification') as task:
            r = self.client.post(reverse('send_channel_message', args=[self.channel.id]),
                                 {'message': text})
        self.assertEqual(r.status_code, 200)
        return [c.kwargs for c in task.delay.call_args_list]

    def test_mention_pushes_without_private_text(self):
        calls = self._send('@cm_member secret plans')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['user_id'], self.member.pk)
        self.assertNotIn('secret', json.dumps(calls[0]))
        self.assertNotIn('Kai', json.dumps(calls[0]))
        self.assertTrue(Notification.objects.filter(
            recipient=self.member, notification_type='chat_mention').exists())

    def test_no_double_push_when_chat_fanout_already_pushed(self):
        ChatReadReceipt.objects.create(user=self.member, channel=self.channel)
        ChatReadReceipt.objects.filter(user=self.member).update(
            last_read_at=timezone.now() - timedelta(minutes=5))
        calls = self._send('@cm_member hi')
        self.assertEqual([c['user_id'] for c in calls], [self.member.pk])

    def test_mention_respects_pref(self):
        set_push_pref(self.member, 'mentions', False)
        self.assertEqual(self._send('@cm_member hi'), [])
        self.assertTrue(Notification.objects.filter(
            recipient=self.member, notification_type='chat_mention').exists())


class EventReminderLinkTests(TestCase):
    def test_members_get_the_calendar_not_the_officer_page(self):
        from src.tasks.notifications import send_event_reminder_pushes
        creator = make_user('er-officer', member_type='Officer')
        member = make_user('er-member')
        make_device(member, TOKEN_A)   # app only, no browser subscription
        when = timezone.now() + timedelta(hours=2)
        Event.objects.create(title='Chapter', description='', date_time=when,
                             created_by=creator, reminder_1_enabled=True,
                             reminder_1_hours_before=24)
        with mock.patch('src.tasks.notifications.send_push_notification') as task:
            send_event_reminder_pushes()
        calls = task.delay.call_args_list
        self.assertEqual([c.args[0] for c in calls], [member.pk])
        url = calls[0].args[3]
        local = timezone.localtime(when)
        self.assertEqual(url, f'/calendar/?year={local.year}&month={local.month}')
        self.assertNotIn('officer', url)


class ExpoAccessTokenCheckTests(TestCase):
    def _ids(self):
        return [m.id for m in run_checks() if m.id == 'src.W020']

    @override_settings(EXPO_ACCESS_TOKEN='', DEBUG=True)
    def test_quiet_in_dev_without_push_devices(self):
        self.assertEqual(self._ids(), [])

    @override_settings(EXPO_ACCESS_TOKEN='', DEBUG=False, CELERY_TASK_ALWAYS_EAGER=True)
    def test_quiet_in_test_runs_without_push_devices(self):
        self.assertEqual(self._ids(), [])

    @override_settings(EXPO_ACCESS_TOKEN='', DEBUG=True)
    def test_warns_when_push_devices_exist(self):
        make_device(make_user('ck-user'), TOKEN_A)
        self.assertEqual(self._ids(), ['src.W020'])

    @override_settings(EXPO_ACCESS_TOKEN='', DEBUG=False, CELERY_TASK_ALWAYS_EAGER=False)
    def test_warns_in_production(self):
        self.assertEqual(self._ids(), ['src.W020'])

    @override_settings(EXPO_ACCESS_TOKEN='tok', DEBUG=False)
    def test_quiet_when_set(self):
        self.assertEqual(self._ids(), [])
