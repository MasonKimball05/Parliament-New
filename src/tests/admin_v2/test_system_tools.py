"""v4.4.4 admin-v2 system tools: gate, POST-only, confirm, audit rows, safe cache
scopes, job queueing (Celery mocked) and redaction of what the page shows."""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from src.middleware import performance
from src.models import ActivityLog
from src.models_feature_flags import FeatureFlag
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view import admin_v2_system
from src.view.admin_v2_system import SCOPES, read_errors, redact, redact_path

URLS = ('admin_v2_system', 'admin_v2_system_clear_cache', 'admin_v2_system_run_job')


def _path(*parts):
    return '/'.join(('',) + parts + ('',))


class GateTests(AdminV2TestCase):

    def test_page_renders_for_admin_v2(self):
        r = self.client.get(reverse('admin_v2_system'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'System tools')
        self.assertContains(r, 'Not offered here')

    def test_every_url_needs_the_admin_v2_step_up(self):
        client = Client()
        client.force_login(self.admin)  # signed in, allowlisted, no admin-v2 session
        for name in URLS:
            r = client.post(reverse(name), {'scope': 'content', 'confirm': 'yes'})
            self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False, msg_prefix=name)

    def test_off_the_allowlist_is_refused(self):
        other = make_user('st-other', is_admin=True)
        client = Client()
        client.force_login(other)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
        for name in URLS:
            r = client.post(reverse(name), {'scope': 'content', 'confirm': 'yes'})
            self.assertRedirects(r, reverse('home'), fetch_redirect_response=False, msg_prefix=name)
        self.assertFalse(ActivityLog.objects.filter(action_type='settings_changed').exists())

    def test_actions_are_post_only(self):
        for name in ('admin_v2_system_clear_cache', 'admin_v2_system_run_job'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)


class ClearCacheTests(AdminV2TestCase):

    def test_confirm_required_and_get_only_shows_the_panel(self):
        cache.set('landing_page_content', 'x')
        r = self.client.get(reverse('admin_v2_system') + '?confirm=cache&scope=content')
        self.assertContains(r, 'Clear "Page content"?')
        self.assertEqual(cache.get('landing_page_content'), 'x')
        r = self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': 'content'})
        self.assertRedirects(r, reverse('admin_v2_system') + '?confirm=cache&scope=content#cache',
                             fetch_redirect_response=False)
        self.assertEqual(cache.get('landing_page_content'), 'x')
        self.assertFalse(ActivityLog.objects.filter(action_type='settings_changed').exists())

    def test_unknown_scope_does_nothing(self):
        cache.set('some_key', 1)
        self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': 'all', 'confirm': 'yes'})
        self.assertEqual(cache.get('some_key'), 1)
        self.assertFalse(ActivityLog.objects.filter(action_type='settings_changed').exists())

    def test_there_is_no_full_clear(self):
        self.assertNotIn('all', SCOPES)
        self.assertNotIn('everything', SCOPES)

    def test_every_scope_keeps_sessions_rate_limits_and_lockouts(self):
        protected = {
            'login_attempts_ip_203.0.113.9': 4, 'login_lockout_user_bob': True,
            f'admin_v2_attempts_{self.admin.pk}': 2, f'2fa_failures_{self.admin.pk}': 3,
            'ip_blacklisted_203.0.113.9': True, 'honeypot_ban_203.0.113.9': True,
            'signup_ann_sent_5': True, 'django.contrib.sessions.cache_fake': {'a': 1},
        }
        cache.set_many(protected)
        for scope in SCOPES:
            r = self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': scope, 'confirm': 'yes'})
            self.assertEqual(r.status_code, 302, scope)
        self.assertEqual(cache.get_many(protected.keys()), protected)
        self.assertEqual(ActivityLog.objects.filter(action_type='settings_changed', object_type='Cache').count(),
                         len(SCOPES))
        # The admin is still signed in (sessions are DB-backed in tests, but the
        # point is that nothing here logs anyone out).
        self.assertEqual(self.client.get(reverse('admin_v2_system')).status_code, 200)

    def test_settings_scope_drops_flag_and_context_keys(self):
        flag = FeatureFlag.objects.create(name='st_flag', display_name='ST flag', is_enabled=True)
        cache.set(FeatureFlag._cache_key(flag.name), {'result': False, 'source': 'stale'})
        cache.set('context_feature_flags', {'stale': True})
        cache.set('system_lockdown_instance', '__singleton_row_absent__')
        self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': 'settings', 'confirm': 'yes'})
        self.assertIsNone(cache.get(FeatureFlag._cache_key(flag.name)))
        self.assertIsNone(cache.get('context_feature_flags'))
        self.assertIsNone(cache.get('system_lockdown_instance'))
        log = ActivityLog.objects.get(action_type='settings_changed', object_type='Cache')
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.object_id, 'settings')
        self.assertIn('cleared the cache', log.description)

    def test_members_scope_drops_per_member_badges(self):
        m = make_user('st-member')
        for prefix in admin_v2_system.MEMBER_KEY_PREFIXES:
            cache.set(f'{prefix}{m.pk}', 'stale')
        self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': 'members', 'confirm': 'yes'})
        for prefix in admin_v2_system.MEMBER_KEY_PREFIXES:
            self.assertIsNone(cache.get(f'{prefix}{m.pk}'))

    def test_content_reports_and_performance_scopes(self):
        cache.set_many({'changelog_index_v1': 1, 'changelog_detail_v1:v4.4.4': 1, 'admin_v2_health:v1:30': 1,
                        performance.CACHE_KEY: [1], performance.COUNT_KEY: 5})
        for scope in ('content', 'reports', 'performance'):
            self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': scope, 'confirm': 'yes'})
        for key in ('changelog_index_v1', 'changelog_detail_v1:v4.4.4', 'admin_v2_health:v1:30'):
            self.assertIsNone(cache.get(key), key)
        # The middleware starts counting again on the very next request, so
        # check the planted values are gone rather than that the keys are.
        self.assertNotIn(1, cache.get(performance.CACHE_KEY) or [])
        self.assertNotEqual(cache.get(performance.COUNT_KEY), 5)

    def test_activity_appears_in_admin_activity(self):
        self.client.post(reverse('admin_v2_system_clear_cache'), {'scope': 'content', 'confirm': 'yes'})
        self.assertContains(self.client.get(reverse('admin_v2_admin_activity')), 'cleared the cache: Page content')


class RunJobTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        every = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.HOURS)
        self.job = PeriodicTask.objects.create(name='Prune things', task='tasks.prune_expired_login_lockouts',
                                               interval=every, kwargs='{"days": 3}')
        self.purge = PeriodicTask.objects.create(name='Erase data', task='tasks.purge_deleted_accounts',
                                                 interval=every)
        self.off = PeriodicTask.objects.create(name='Off', task='tasks.send_daily_digest', interval=every,
                                               enabled=False)
        patcher = mock.patch('celery.current_app.send_task')
        self.send_task = patcher.start()
        self.send_task.return_value.id = 'celery-id-1'
        self.addCleanup(patcher.stop)

    def _run(self, task, **extra):
        return self.client.post(reverse('admin_v2_system_run_job'), {'task': task.pk, **extra})

    def test_lists_jobs_and_offers_only_runnable_ones(self):
        r = self.client.get(reverse('admin_v2_system'))
        self.assertContains(r, f'?confirm=job&amp;task={self.job.pk}')
        self.assertNotContains(r, f'?confirm=job&amp;task={self.purge.pk}')
        self.assertNotContains(r, f'?confirm=job&amp;task={self.off.pk}')
        self.assertContains(r, 'Erases data permanently')
        # A confirm link for a refused job shows no panel.
        r = self.client.get(reverse('admin_v2_system') + f'?confirm=job&task={self.purge.pk}')
        self.assertNotContains(r, 'Run it now')

    def test_confirm_required(self):
        r = self._run(self.job)
        self.assertRedirects(r, reverse('admin_v2_system') + f'?confirm=job&task={self.job.pk}#jobs',
                             fetch_redirect_response=False)
        self.send_task.assert_not_called()
        self.assertContains(self.client.get(r.url), 'Run "Prune things" now?')

    def test_queues_with_the_rows_arguments_and_logs(self):
        self._run(self.job, confirm='yes')
        self.send_task.assert_called_once_with('tasks.prune_expired_login_lockouts', args=[], kwargs={'days': 3})
        log = ActivityLog.objects.get(action_type='settings_changed', object_type='PeriodicTask')
        self.assertEqual(log.object_id, str(self.job.pk))
        self.assertEqual(log.user, self.admin)
        self.assertIn('Prune things', log.description)

    def test_queue_option_passed(self):
        PeriodicTask.objects.filter(pk=self.job.pk).update(queue='slow')
        self._run(self.job, confirm='yes')
        self.assertEqual(self.send_task.call_args.kwargs['queue'], 'slow')

    def test_destructive_and_disabled_jobs_refused(self):
        for task in (self.purge, self.off):
            self._run(task, confirm='yes')
        self.send_task.assert_not_called()
        self.assertFalse(ActivityLog.objects.filter(action_type='settings_changed').exists())

    def test_destructive_words_refused(self):
        task = PeriodicTask.objects.create(name='Wipe', task='tasks.wipe_everything',
                                           interval=self.job.interval)
        self._run(task, confirm='yes')
        self.send_task.assert_not_called()

    def test_double_submit_queues_once(self):
        self._run(self.job, confirm='yes')
        self._run(self.job, confirm='yes')
        self.assertEqual(self.send_task.call_count, 1)

    def test_broker_down_is_a_message_not_a_500(self):
        self.send_task.side_effect = OSError('broker down')
        r = self._run(self.job, confirm='yes')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(ActivityLog.objects.filter(action_type='settings_changed').exists())
        self.send_task.side_effect = None
        self._run(self.job, confirm='yes')  # the cooldown was released
        self.assertEqual(self.send_task.call_count, 2)

    def test_unknown_task(self):
        r = self.client.post(reverse('admin_v2_system_run_job'), {'task': '999999', 'confirm': 'yes'})
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse('admin_v2_system_run_job'), {'task': 'x', 'confirm': 'yes'})
        self.assertEqual(r.status_code, 302)
        self.send_task.assert_not_called()


class RedactionTests(AdminV2TestCase):

    def test_redact_secrets(self):
        token = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'
        cases = [
            'password=hunter2xyz', "{'password': 'hunter2xyz'}", 'access_token=abcd.efgh',
            'Authorization: Bearer abcdefghijklmnop', 'Cookie: sessionid=abc; csrftoken=def',
            'redis://default:s3cretpw@localhost:6379/0', f'reset link {token}', 'api_key: zzzzyyyy',
            'callback?code=123456&state=ok', 'X-Site-Monitor-Token=monitorvalue',
        ]
        for raw in cases:
            out = redact(raw)
            for secret in ('hunter2xyz', 'abcd.efgh', 'abcdefghijklmnop', 'sessionid=abc', 'def',
                           's3cretpw', token, 'zzzzyyyy', '123456', 'monitorvalue'):
                if secret in raw:
                    self.assertNotIn(secret, out, raw)
            self.assertIn('[redacted]', out, raw)

    def test_redact_keeps_ordinary_text(self):
        line = 'Internal Server Error: admin_v2_member_revoke_device took 2500ms (status 500)'
        self.assertEqual(redact(line), line)

    def test_redact_path(self):
        token = 'Zx9kQ2mP7vL4nR8tY3wB6cJ1hF5dG0sA-_uE'
        self.assertEqual(redact_path(_path('calendar', 'feed', token)), _path('calendar', 'feed', '[redacted]'))
        self.assertEqual(redact_path(_path('members', 'av2-admin') + '?t=secret'), _path('members', 'av2-admin'))

    def _log(self, text):
        d = tempfile.mkdtemp(prefix='st-logs-')
        path = os.path.join(d, 'django_actions.log')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path

    def test_read_errors_filters_levels_keeps_traces_and_redacts(self):
        path = self._log(
            '2026-11-04 10:00:00,000 [INFO] django.server: GET ok\n'
            '2026-11-04 10:00:01,000 [ERROR] django.request: Internal Server Error: boom password=hunter2xyz\n'
            'Traceback (most recent call last):\n'
            '  File "src/view/x.py", line 3, in view\n'
            'ValueError: token=abcdef123456\n'
            '2026-11-04 10:00:02,000 [WARNING] src: slow\n'
            '  not part of an error\n'
            '2026-11-04 10:00:03,000 [CRITICAL] src: second\n')
        entries, problem = read_errors(path)
        self.assertIsNone(problem)
        self.assertEqual([e['level'] for e in entries], ['CRITICAL', 'ERROR'])
        err = entries[1]
        self.assertNotIn('hunter2xyz', err['message'])
        self.assertEqual(len(err['trace']), 3)
        self.assertNotIn('abcdef123456', '\n'.join(err['trace']))
        self.assertNotIn('not part of an error', '\n'.join(err['trace']))

    def test_missing_log_file(self):
        entries, problem = read_errors(os.path.join(tempfile.mkdtemp(), 'nope.log'))
        self.assertEqual(entries, [])
        self.assertIn('does not exist', problem)

    def test_page_shows_redacted_errors_and_slow_pages(self):
        path = self._log('2026-11-04 10:00:01,000 [ERROR] django.request: boom Cookie: sessionid=topsecretvalue\n')
        token = 'Zx9kQ2mP7vL4nR8tY3wB6cJ1hF5dG0sA-_uE'
        cache.set(performance.CACHE_KEY, [
            (timezone.now() - timedelta(minutes=3), 2400.0, _path('calendar', 'feed', token), 42, 900.0),
            (timezone.now(), 50.0, _path('quick'), 1, 1.0),
        ])
        with mock.patch.object(admin_v2_system, 'log_file_path', return_value=path):
            r = self.client.get(reverse('admin_v2_system'))
        self.assertContains(r, 'boom')
        self.assertNotContains(r, 'topsecretvalue')
        self.assertContains(r, '2400 ms')
        self.assertNotContains(r, token)
        self.assertNotContains(r, _path('quick'))
