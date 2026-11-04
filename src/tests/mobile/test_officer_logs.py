"""
The officer log pages in the app (v4.4.3): Activity Logs and System Logs,
each behind its website page's own gate and through the web views' own helpers
(`filtered_activity_logs` + `redact_kai_logs`, `read_log_entries`).

    python manage.py test src.tests.mobile.test_officer_logs
"""
import importlib
import json
import os
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import ActivityLog, Committee
from src.models.kai import KaiMemberPermission, KaiReport
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'
SUBMITTER = 'Zebediah Quillfeather'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.officer = make_user('lg-off', member_type='Officer')
        self.chair = make_user('lg-chair', member_type='Chair')
        self.advisor = make_user('lg-adv', member_type='Advisor')
        self.member = make_user('lg-mem')
        self.tokens = {u.user_id: make_device(u)[1]
                       for u in (self.officer, self.chair, self.advisor, self.member)}

    def get(self, user, path):
        return self.api.get(API + path, **bearer(self.tokens[user.user_id]))


# ── Activity Logs ────────────────────────────────────────────────────────────

class ActivityLogsTests(_Base):

    def setUp(self):
        super().setUp()
        flag('activity_logs', True)
        self.login = ActivityLog.log_activity(action_type='login', user=self.member,
                                              description='Member logged in', ip_address='10.0.0.5')
        self.vote = ActivityLog.log_activity(action_type='vote_cast', user=self.officer,
                                             description='Officer voted on Bill A', object_repr='Bill A')
        self.old = ActivityLog.log_activity(action_type='login', user=self.officer, description='Old login')
        ActivityLog.objects.filter(pk=self.old.pk).update(timestamp=timezone.now() - timedelta(days=20))

    def ids(self, r):
        self.assertEqual(r.status_code, 200, r.content)
        return {row['id'] for row in r.json()['logs']}

    def test_officers_and_chairs_only_like_the_web(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.get(user, 'officer/activity-logs/').status_code, code, user.member_type)

    def test_feature_flag(self):
        flag('activity_logs', False)
        r = self.get(self.officer, 'officer/activity-logs/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'feature_disabled')

    def test_default_is_last_seven_days_with_page_one_extras(self):
        r = self.get(self.officer, 'officer/activity-logs/')
        d = r.json()
        self.assertEqual(self.ids(r), {self.login.pk, self.vote.pk})
        self.assertEqual(d['filters']['date_range'], '7')
        self.assertEqual(d['total'], 2)
        self.assertEqual(d['unique_users'], 2)
        self.assertEqual({c['code']: c['count'] for c in d['category_counts']}, {'auth': 1, 'vote': 1})
        self.assertIn(self.member.user_id, {u['user_id'] for u in d['users']})
        self.assertTrue(d['categories'] and d['types'])
        row = next(x for x in d['logs'] if x['id'] == self.login.pk)
        self.assertEqual(row['actor'], self.member.get_display_name())
        self.assertEqual(row['ip'], '10.0.0.5')
        self.assertEqual(row['category'], 'auth')

    def test_filters(self):
        self.assertEqual(self.ids(self.get(self.officer, 'officer/activity-logs/?date_range=all')),
                         {self.login.pk, self.vote.pk, self.old.pk})
        self.assertEqual(self.ids(self.get(self.officer, 'officer/activity-logs/?date_range=30&category=auth')),
                         {self.login.pk, self.old.pk})
        self.assertEqual(self.ids(self.get(self.officer, 'officer/activity-logs/?type=vote_cast')), {self.vote.pk})
        self.assertEqual(self.ids(self.get(self.officer, f'officer/activity-logs/?user={self.member.user_id}')),
                         {self.login.pk})
        self.assertEqual(self.ids(self.get(self.officer, 'officer/activity-logs/?q=Bill%20A')), {self.vote.pk})

    def test_paging_fifty_a_page(self):
        ActivityLog.objects.bulk_create([ActivityLog(user=self.officer, action_category='auth', action_type='login',
                                                     description=f'row {i}') for i in range(60)])
        first = self.get(self.officer, 'officer/activity-logs/').json()
        self.assertEqual((first['page'], first['pages'], first['total'], len(first['logs'])), (1, 2, 62, 50))
        self.assertTrue(first['has_next'])
        second = self.get(self.officer, 'officer/activity-logs/?page=2').json()
        self.assertEqual((second['page'], len(second['logs']), second['has_next']), (2, 12, False))
        self.assertNotIn('users', second)
        self.assertFalse({r['id'] for r in first['logs']} & {r['id'] for r in second['logs']})


class ActivityLogsExportTests(_Base):
    """The page's Export CSV, through the app: same gate, file, redaction and log entry."""

    def setUp(self):
        super().setUp()
        flag('activity_logs', True)
        ActivityLog.log_activity(action_type='vote_cast', user=self.officer,
                                 description='Officer voted on Bill A', object_repr='Bill A')
        self.committee = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.submitter = make_user('lg-xsub', name=SUBMITTER)
        report = KaiReport.objects.create(title='Case', description='x', submitted_by=self.submitter,
                                          targeted_to=self.member)
        ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter, ip_address='10.9.9.9',
            description=f'{SUBMITTER} submitted Kai case {report.display_number}',
            object_type='KaiReport', object_id=report.id, object_repr=report.display_number,
            metadata={'action': 'submitted'})

    def test_officers_and_chairs_only_like_the_web(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.get(user, 'officer/activity-logs/export/').status_code, code, user.member_type)
        flag('activity_logs', False)
        self.assertEqual(self.get(self.officer, 'officer/activity-logs/export/').json()['error'], 'feature_disabled')

    def test_the_websites_file_redacted_and_logged(self):
        r = self.get(self.officer, 'officer/activity-logs/export/?q=Bill')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])
        self.assertIn('attachment', r['Content-Disposition'])
        body = b''.join(r.streaming_content if r.streaming else [r.content]).decode()
        self.assertIn('Officer voted on Bill A', body)
        self.assertNotIn('submitted Kai case', body)    # the q filter applied
        r = self.get(self.officer, 'officer/activity-logs/export/?category=kai')
        body = b''.join(r.streaming_content if r.streaming else [r.content]).decode()
        self.assertNotIn(SUBMITTER, body)
        self.assertNotIn('10.9.9.9', body)
        self.assertTrue(ActivityLog.objects.filter(user=self.officer,
                                                   description__contains='exported').exists())


class ActivityLogsKaiTests(_Base):
    """The page's Kai rules, through the app: redaction and the author-filter exclusion."""

    def setUp(self):
        super().setUp()
        flag('activity_logs', True)
        self.committee = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.submitter = make_user('lg-sub', name=SUBMITTER)
        report = KaiReport.objects.create(title='Case', description='x', submitted_by=self.submitter,
                                          targeted_to=self.member)
        self.kai = ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter, ip_address='10.9.9.9',
            description=f'{SUBMITTER} submitted Kai case {report.display_number}',
            object_type='KaiReport', object_id=report.id, object_repr=report.display_number,
            metadata={'action': 'submitted'})

    def test_officer_sees_the_row_without_the_reporter_or_ip(self):
        r = self.get(self.officer, 'officer/activity-logs/?category=kai')
        self.assertEqual(r.status_code, 200)
        # The rows, not the member dropdown (which, as on the web, lists every
        # Active member by name).
        body = json.dumps(r.json()['logs'])
        row = next(x for x in r.json()['logs'] if x['id'] == self.kai.pk)
        self.assertNotEqual(row['actor'], self.submitter.get_display_name())
        self.assertIsNone(row['actor_id'])
        self.assertIsNone(row['ip'])
        self.assertNotIn(SUBMITTER, body)
        self.assertNotIn('10.9.9.9', body)
        self.assertNotIn(self.submitter.user_id, body)

    def test_author_filter_excludes_the_kai_row(self):
        r = self.get(self.officer, f'officer/activity-logs/?user={self.submitter.user_id}')
        self.assertNotIn(self.kai.pk, {x['id'] for x in r.json()['logs']})

    def test_search_does_not_match_the_redacted_name(self):
        r = self.get(self.officer, 'officer/activity-logs/?q=Zebediah')
        self.assertNotIn(self.kai.pk, {x['id'] for x in r.json()['logs']})

    def test_a_full_reviewer_sees_it_as_on_the_web(self):
        self.committee.members.add(self.officer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.officer, can_view_report_list=True,
            can_view_report_details=True, can_view_submitter_identity=True, can_view_accused_identity=True)
        r = self.get(self.officer, f'officer/activity-logs/?user={self.submitter.user_id}')
        self.assertIn(self.kai.pk, {x['id'] for x in r.json()['logs']})


# ── System Logs ──────────────────────────────────────────────────────────────

class SystemLogsTests(_Base):

    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('src.view.officer.view_logs')
        self.real_path = self.module.LOG_FILE_PATH
        fd, self.path = tempfile.mkstemp(suffix='.log')
        os.close(fd)
        self.module.LOG_FILE_PATH = self.path
        self.reporter = make_user('lg-rep', username='reporter_guy')

    def tearDown(self):
        self.module.LOG_FILE_PATH = self.real_path
        try:
            os.remove(self.path)
        except OSError:  # pragma: no cover
            pass

    def write(self, lines):
        with open(self.path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')

    def test_officers_and_chairs_only_like_the_web(self):
        self.write(['2026-11-03 10:00:00,000 [INFO] django.server: hello'])
        for user, code in ((self.officer, 200), (self.chair, 200), (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.get(user, 'officer/system-logs/').status_code, code, user.member_type)

    def test_entries_newest_first_parsed(self):
        self.write([
            '2026-11-03 10:00:00,000 [INFO] django.server: first',
            '2026-11-03 10:00:01,000 [ERROR] src.views: second',
            'Traceback (most recent call last):',
        ])
        d = self.get(self.officer, 'officer/system-logs/').json()
        self.assertEqual(d['state'], 'ok')
        self.assertEqual([e['message'] for e in d['entries']],
                         ['Traceback (most recent call last):', 'second', 'first'])
        self.assertEqual(d['entries'][1]['level'], 'ERROR')
        self.assertEqual(d['entries'][1]['logger'], 'src.views')
        self.assertIsNone(d['entries'][0]['level'])

    def test_bounded_to_the_pages_tail(self):
        self.write([f'2026-11-03 10:00:00,000 [INFO] x: line {i}' for i in range(500)])
        d = self.get(self.officer, 'officer/system-logs/').json()
        self.assertEqual(len(d['entries']), d['limit'])
        self.assertEqual(d['entries'][0]['message'], 'line 499')

    def test_kai_reporter_is_redacted(self):
        self.write(["2026-11-03 10:00:00,000 [INFO] function_calls: User reporter_guy called submit_kai_report {}"])
        body = self.get(self.officer, 'officer/system-logs/').content.decode()
        self.assertIn('submit_kai_report', body)
        self.assertNotIn('reporter_guy', body)

    def test_missing_file(self):
        os.remove(self.path)
        d = self.get(self.officer, 'officer/system-logs/').json()
        self.assertEqual((d['state'], d['entries']), ('missing', []))


class HubFlagTests(_Base):

    def test_hub_flag_follows_the_feature_flag(self):
        flag('activity_logs', True)
        hub = self.get(self.officer, 'officer/').json()
        self.assertTrue(hub['can_view_activity_logs'])
        self.assertTrue(hub['can_view_system_logs'])
        flag('activity_logs', False)
        cache.clear()
        self.assertFalse(self.get(self.officer, 'officer/').json()['can_view_activity_logs'])
