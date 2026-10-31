"""
My Kai reports in the app (src/mobile/views_kai.py): the member Kai dashboard
(/kai/) and a party's view of one case (/kai/my-report/<id>/), read-only.

The confidentiality rules are the point: the accused never learns who filed a
case, never sees one before it's addressed, and the activity feed is action +
time only.

Until its routes are in src/mobile/urls.py, this module is its own urlconf.

    python manage.py test src.tests.mobile.test_kai
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from src.mobile import urls as mobile_urls
from src.mobile import views_kai
from src.models import (
    Committee, KaiAppeal, KaiClosureRequest, KaiFormField, KaiMemberPermission,
    KaiRecusal, KaiReport, KaiReportActivity, KaiReportFieldResponse,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'

urlpatterns = [
    path('api/mobile/v1/', include(([
        path('kai/', views_kai.KaiDashboardView.as_view(), name='mobile_kai'),
        path('kai/reports/<int:report_id>/', views_kai.KaiReportView.as_view(), name='mobile_kai_report'),
        *mobile_urls.urlpatterns,
    ], 'mobile'))),
    path('', include('src.urls')),
]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.reporter = make_user('kai-reporter')
        self.accused = make_user('kai-accused')
        self.other = make_user('kai-other')
        self.api = Client()
        self.tokens = {}

    def access(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def get(self, user, p):
        return self.api.get(API + p, **bearer(self.access(user)))

    def report(self, title, by=None, to=None, **kw):
        return KaiReport.objects.create(
            title=title, description=f'{title} body', submitted_by=by or self.reporter,
            targeted_to=to, **kw)


class DashboardTests(_Base):

    def setUp(self):
        super().setUp()
        self.mine = self.report('Filed by me', to=self.accused, category='social',
                                deliberation_outcome='heard', status='reviewed')
        self.pending = self.report('Pending one', to=self.accused)
        self.unnamed = self.report('No one named')
        self.report('Someone else', by=self.other, to=self.other)
        KaiClosureRequest.objects.create(report=self.mine, requested_by=self.reporter, reason='Done')

    def test_submitter_sees_their_cases_with_the_accused(self):
        r = self.get(self.reporter, 'kai/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual({s['title'] for s in body['submitted']},
                         {'Filed by me', 'Pending one', 'No one named'})
        rows = {s['title']: s for s in body['submitted']}
        self.assertEqual(rows['Filed by me']['regarding'], 'User kai-accused')
        self.assertEqual(rows['No one named']['regarding'], '')
        self.assertEqual((rows['Filed by me']['category_label'], rows['Filed by me']['status_label'],
                          rows['Filed by me']['outcome_label']),
                         ('Social Conduct', 'Reviewed', 'Case Heard'))
        self.assertEqual(body['named'], [])
        self.assertEqual(body['pending_requests'], 1)
        self.assertFalse(body['can_view_kai_reports'])
        self.assertIsNone(body['links']['review'])
        self.assertEqual(body['links']['submit'], reverse('submit_kai_report'))
        self.assertEqual(body['links']['page'], '/kai/')

    def test_accused_sees_only_addressed_cases_and_never_the_submitter(self):
        r = self.get(self.accused, 'kai/')
        body = r.json()
        self.assertEqual([n['title'] for n in body['named']], ['Filed by me'])
        self.assertNotIn('regarding', body['named'][0])
        self.assertNotIn('kai-reporter', r.content.decode())
        self.assertEqual(body['submitted'], [])

    def test_self_report_is_not_listed_as_named(self):
        self.report('Myself', by=self.accused, to=self.accused, deliberation_outcome='heard')
        body = self.get(self.accused, 'kai/').json()
        self.assertEqual([n['title'] for n in body['named']], ['Filed by me'])
        self.assertEqual([s['title'] for s in body['submitted']], ['Myself'])

    def test_review_link_follows_the_grant(self):
        kai = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        kai.members.add(self.other)
        KaiMemberPermission.objects.create(committee=kai, user=self.other, can_view_report_list=True)
        body = self.get(self.other, 'kai/').json()
        self.assertTrue(body['can_view_kai_reports'])
        self.assertEqual(body['links']['review'], reverse('view_kai_reports'))

    def test_standin_cases(self):
        standin = make_user('kai-standin')
        KaiRecusal.objects.create(report=self.mine, user=self.other, reason='unavailable',
                                  replacement=standin, appointed_at=timezone.now())
        body = self.get(standin, 'kai/').json()
        self.assertEqual(len(body['standing_in']), 1)
        s = body['standing_in'][0]
        self.assertEqual((s['report_id'], s['title'], s['standing_in_for']),
                         (self.mine.id, 'Filed by me', 'User kai-other'))
        self.assertEqual(s['path'], reverse('manage_kai_report', args=[self.mine.id]))
        self.assertEqual(s['case_number'], self.mine.display_number)

    def test_toggle_is_ignored_and_pledge_restriction_applies(self):
        toggle('user_kai_dashboard', False)
        self.assertEqual(self.get(self.reporter, 'kai/').status_code, 200)
        pledge = make_user('kai-pledge', member_type='Pledge')
        restrict('user_kai_dashboard', [])
        r = self.get(pledge, 'kai/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_signed_out(self):
        self.assertEqual(self.api.get(API + 'kai/').status_code, 401)


class ReportTests(_Base):

    def setUp(self):
        super().setUp()
        self.case = self.report('The case', to=self.accused, deliberation_outcome='heard',
                                status='reviewed', tags=['urgent'],
                                accused_notified_at=timezone.now() - timedelta(days=2))
        KaiReportActivity.objects.create(report=self.case, user=self.reporter, action='created',
                                         details='User kai-reporter filed it')
        KaiReportActivity.objects.create(report=self.case, user=self.other, action='notes_updated',
                                         details='secret chair notes')
        field = KaiFormField.objects.create(field_name='where', label='Where', field_type='text')
        multi = KaiFormField.objects.create(field_name='who', label='Witnesses', field_type='multiselect')
        empty = KaiFormField.objects.create(field_name='when', label='When', field_type='date')
        KaiReportFieldResponse.objects.create(report=self.case, field=field, text_value='The house')
        KaiReportFieldResponse.objects.create(report=self.case, field=multi, json_value=['A', 'B'])
        KaiReportFieldResponse.objects.create(report=self.case, field=empty)
        KaiClosureRequest.objects.create(report=self.case, requested_by=self.reporter,
                                         request_type='drop', reason='Resolved', status='denied',
                                         review_notes='Not yet')

    def path(self, report=None):
        return f'kai/reports/{(report or self.case).id}/'

    def test_submitter_view(self):
        r = self.get(self.reporter, self.path())
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertTrue(b['is_submitter'])
        self.assertFalse(b['is_accused'])
        self.assertEqual(b['description'], 'The case body')
        self.assertEqual(b['directed_to'], 'User kai-accused')
        self.assertEqual(b['tags'], ['Urgent'])
        self.assertIsNone(b['attachment'])
        self.assertEqual([(x['label'], x['value']) for x in b['responses']],
                         [('Where', 'The house'), ('Witnesses', 'A, B'), ('When', 'Not provided')])
        self.assertEqual(b['requests'][0]['type_label'], 'Drop Request')
        self.assertEqual(b['requests'][0]['review_notes'], 'Not yet')
        self.assertIsNone(b['appeal'])
        # No pending request, heard: both web actions offered.
        self.assertEqual(b['actions']['request_closure'], reverse('kai_request_closure', args=[self.case.id]))
        self.assertEqual(b['actions']['drop_case'], reverse('kai_request_drop', args=[self.case.id]))
        self.assertEqual(b['links']['page'], reverse('user_view_kai_report', args=[self.case.id]))

    def test_activity_is_action_and_time_only(self):
        r = self.get(self.accused, self.path())
        b = r.json()
        self.assertEqual([a['action'] for a in b['activity']], ['created'])
        self.assertEqual(set(b['activity'][0]), {'action', 'label', 'timestamp'})
        text = r.content.decode()
        self.assertNotIn('kai-reporter', text)
        self.assertNotIn('secret chair notes', text)

    def test_accused_view_and_appeal_window(self):
        b = self.get(self.accused, self.path()).json()
        self.assertTrue(b['is_accused'])
        self.assertIsNone(b['actions']['drop_case'])
        self.assertIsNotNone(b['actions']['request_closure'])
        a = b['appeal']
        self.assertTrue(a['can_appeal'])
        self.assertEqual(a['days_remaining'], 8)
        self.assertEqual(a['window_days'], 10)
        self.assertEqual(a['path'], b['links']['page'])
        self.assertEqual(a['blocked_reason'], '')

        KaiAppeal.objects.create(report=self.case, filed_by=self.accused, grounds='Unfair')
        b = self.get(self.accused, self.path()).json()
        self.assertFalse(b['appeal']['can_appeal'])
        self.assertIsNone(b['appeal']['path'])
        self.assertIn('already filed', b['appeal']['blocked_reason'])
        self.assertEqual(b['appeals'][0]['level_label'], 'The Chapter')
        self.assertNotIn('filed_by', b['appeals'][0])

    def test_pending_case_is_hidden_from_the_accused(self):
        pending = self.report('Pending', to=self.accused)
        self.assertEqual(self.get(self.accused, self.path(pending)).status_code, 404)
        self.assertEqual(self.get(self.reporter, self.path(pending)).status_code, 200)

    def test_strangers_get_404(self):
        self.assertEqual(self.get(self.other, self.path()).status_code, 404)
        self.assertEqual(self.get(self.other, 'kai/reports/999999/').status_code, 404)

    def test_web_page_still_renders_for_both_parties(self):
        for user in (self.reporter, self.accused):
            c = Client()
            c.force_login(user)
            self.assertEqual(c.get(reverse('user_kai_dashboard')).status_code, 200)
            self.assertEqual(c.get(reverse('user_view_kai_report', args=[self.case.id])).status_code, 200)
