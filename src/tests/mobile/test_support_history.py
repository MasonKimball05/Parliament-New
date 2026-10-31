"""
My bug reports, My submissions and the Ideas board in the app (v4.4.0,
src/mobile/views_support_history.py): each member sees only their own reports
and submissions, the board lists ideas only (never support tickets) in the
page's order with its counts and filters, staff fields stay out, and the web
pages still render the same rows through the shared helpers.

The routes aren't in src/mobile/urls.py yet, so this module is its own
urlconf: the site's, plus these views under /api/mobile/v1/.

    python manage.py test src.tests.mobile.test_support_history
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path

import src.urls
from src.mobile import views_support_history as v
from src.models import BugReport, FeedbackRequest
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

_routes = [
    path('support/bug-reports/', v.MyBugReportsView.as_view(), name='mobile_my_bug_reports'),
    path('support/feedback/mine/', v.MyFeedbackView.as_view(), name='mobile_my_feedback'),
    path('support/feedback/ideas/', v.FeedbackIdeasView.as_view(), name='mobile_feedback_ideas'),
]
urlpatterns = [path('api/mobile/v1/', include((_routes, 'mobile_support_history_test')))] + src.urls.urlpatterns

API = '/api/mobile/v1/'


def _fb(user, title, request_type='feature_idea', **extra):
    return FeedbackRequest.objects.create(submitted_by=user, title=title, description=f'About {title}',
                                          request_type=request_type, **extra)


@override_settings(ROOT_URLCONF='src.tests.mobile.test_support_history', ALLOWED_HOSTS=['testserver', 'localhost'])
class SupportHistoryTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('sh-1')
        self.other = make_user('sh-2')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, path_, access=None):
        r = self.api.get(API + path_, **bearer(access or self.access))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    # ── my bug reports ──

    def test_bug_reports_are_only_mine_newest_first_without_staff_fields(self):
        old = BugReport.objects.create(submitted_by=self.user, description='Old one', issue_type='ui', page='voting',
                                       priority='high', admin_notes='internal', browser_info='Safari')
        new = BugReport.objects.create(submitted_by=self.user, description='New one', status='in_progress')
        BugReport.objects.create(submitted_by=self.other, description='Not mine')
        body = self.get('support/bug-reports/')
        self.assertEqual([r['id'] for r in body['reports']], [new.id, old.id])
        row = body['reports'][1]
        self.assertEqual((row['issue_type_label'], row['page_label'], row['priority_label'], row['status_label']),
                         ('UI/Visual Issue', old.get_page_display(), old.get_priority_display(), 'New'))
        self.assertEqual(body['reports'][0]['status_label'], 'In Progress')
        self.assertNotIn('admin_notes', row)
        self.assertNotIn('internal', str(body))
        self.assertNotIn('Safari', str(body))
        self.assertEqual(body['links']['page'], '/bug-report/my-reports/')
        self.assertEqual((row['links']['detail'], row['has_screenshot']), (f'/bug-tracker/{old.id}/', False))

    def test_no_reports_is_an_empty_list(self):
        self.assertEqual(self.get('support/bug-reports/')['reports'], [])

    # ── my feedback ──

    def test_submissions_are_mine_ideas_and_tickets_together(self):
        idea = _fb(self.user, 'Dark mode', page='home')
        ticket = _fb(self.user, 'Locked out', request_type='support_ticket', admin_notes='called him')
        _fb(self.other, 'Their ticket', request_type='support_ticket')
        body = self.get('support/feedback/mine/')
        self.assertEqual([s['id'] for s in body['submissions']], [ticket.id, idea.id])
        self.assertEqual([s['request_type_label'] for s in body['submissions']], ['Support Ticket / Contact', 'Feature Idea'])
        self.assertEqual(body['submissions'][0]['links']['detail'], f'/feedback/ideas/{ticket.id}/')
        self.assertNotIn('called him', str(body))
        self.assertNotIn('Their ticket', str(body))

    # ── ideas board ──

    def test_board_lists_ideas_only_in_the_pages_order_with_counts(self):
        new = _fb(self.other, 'New idea')
        done = _fb(self.user, 'Done idea', status='resolved')
        doing = _fb(self.other, 'Doing idea', status='in_progress')
        ack = _fb(self.other, 'Ack idea', status='acknowledged')
        _fb(self.other, 'Secret ticket', request_type='support_ticket')
        body = self.get('support/feedback/ideas/')
        self.assertEqual([i['id'] for i in body['ideas']], [doing.id, ack.id, new.id, done.id])
        self.assertNotIn('Secret ticket', str(body))
        self.assertEqual(body['counts'], {'total': 4, 'new': 1, 'in_progress': 2, 'resolved': 1})
        mine = next(i for i in body['ideas'] if i['id'] == done.id)
        self.assertEqual((mine['submitted_by'], mine['is_mine']), (self.user.name, True))
        self.assertFalse(body['can_manage'])
        self.assertIsNone(body['links']['admin'])
        self.assertEqual([c['value'] for c in body['status_choices']], [s for s, _ in FeedbackRequest.STATUS_CHOICES])

    def test_board_filters_and_ignores_unknown_values(self):
        _fb(self.other, 'Low', priority='low')
        high = _fb(self.other, 'High', priority='high', status='acknowledged')
        body = self.get('support/feedback/ideas/?priority=high')
        self.assertEqual(([i['id'] for i in body['ideas']], body['priority']), ([high.id], 'high'))
        self.assertEqual([i['id'] for i in self.get('support/feedback/ideas/?status=acknowledged')['ideas']],
                         [high.id])
        body = self.get('support/feedback/ideas/?status=bogus&priority=nope')
        self.assertEqual((len(body['ideas']), body['status'], body['priority']), (2, '', ''))
        # The counts are the board's totals, whatever the filter.
        self.assertEqual(body['counts']['total'], 2)

    def test_a_deleted_submitter_shows_as_anonymous(self):
        _fb(None, 'Orphan')
        self.assertEqual(self.get('support/feedback/ideas/')['ideas'][0]['submitted_by'], 'Anonymous')

    def test_the_feedback_admin_gets_the_admin_link(self):
        admin = make_user('73')
        _, access, _ = make_device(admin)
        body = self.get('support/feedback/ideas/', access)
        self.assertTrue(body['can_manage'])
        self.assertEqual(body['links']['admin'], '/feedback/admin/')

    # ── access ──

    def test_signed_in_only(self):
        for p in ('support/bug-reports/', 'support/feedback/mine/', 'support/feedback/ideas/'):
            self.assertEqual(self.api.get(API + p).status_code, 401)

    def test_pledge_restrictions_follow_the_web_pages(self):
        from src.models.education import PledgePageRestriction
        pledge = make_user('sh-pl', member_type='Pledge')
        _, access, _ = make_device(pledge)
        PledgePageRestriction.objects.create(url_name='feedback_tracker', allowed_phases=[])
        r = self.api.get(API + 'support/feedback/ideas/', **bearer(access))
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        self.assertEqual(self.api.get(API + 'support/feedback/mine/', **bearer(access)).status_code, 200)

    # ── the web pages, through the shared helpers ──

    def test_web_pages_render_the_same_rows(self):
        _fb(self.user, 'My web idea')
        _fb(self.other, 'Their ticket', request_type='support_ticket')
        BugReport.objects.create(submitted_by=self.user, description='My web bug')
        web = Client()
        web.force_login(self.user)
        r = web.get('/feedback/ideas/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([f.title for f in r.context['feedback_requests']], ['My web idea'])
        self.assertEqual(r.context['total_count'], 1)
        r = web.get('/feedback/my-submissions/')
        self.assertEqual([f.title for f in r.context['feedback_requests']], ['My web idea'])
        r = web.get('/bug-report/my-reports/')
        self.assertEqual([b.description for b in r.context['bug_reports']], ['My web bug'])
