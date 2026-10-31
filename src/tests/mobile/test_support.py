"""
Report a bug and Feedback in the app (v4.4.0, src/mobile/views_support.py):
the form choices, a submission saved through the website's own functions
(same checks, same activity log and email), the optional multipart
screenshot with the v3.51.6 checks, and the pledge page restrictions.

The routes aren't in src/mobile/urls.py yet, so this module is its own
urlconf: the site's, plus these views under /api/mobile/v1/.

    python manage.py test src.tests.mobile.test_support
"""
import io
import json
import shutil
import tempfile

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from PIL import Image

import src.urls
from src.mobile import views_support
from src.models import ActivityLog, BugReport, FeedbackRequest
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

_routes = [
    path('support/bug-report/', views_support.BugReportView.as_view(), name='mobile_bug_report'),
    path('support/feedback/', views_support.FeedbackView.as_view(), name='mobile_feedback'),
]
urlpatterns = [path('api/mobile/v1/', include((_routes, 'mobile_support_test')))] + src.urls.urlpatterns

API = '/api/mobile/v1/'
_MEDIA = tempfile.mkdtemp(prefix='mobile-support-test-')


def _png(name='shot.png'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(ROOT_URLCONF='src.tests.mobile.test_support', ALLOWED_HOSTS=['testserver', 'localhost'],
                   MEDIA_ROOT=_MEDIA, BUG_REPORT_EMAIL='admin@example.com', EMAIL_HOST_USER='test@example.com',
                   REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SupportTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.user = make_user('sup-1')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def post_json(self, path_, body, access=None):
        return self.api.post(API + path_, json.dumps(body), content_type='application/json',
                             **bearer(access or self.access))

    # ── bug report ──

    def test_form_choices_are_the_models(self):
        body = self.api.get(API + 'support/bug-report/', **bearer(self.access)).json()
        self.assertEqual([c['value'] for c in body['issue_types']], [v for v, _ in BugReport.ISSUE_TYPES])
        self.assertEqual([c['value'] for c in body['pages']], [v for v, _ in BugReport.PAGE_CHOICES])
        self.assertEqual(body['default_priority'], 'medium')
        self.assertEqual(body['screenshot']['max_bytes'], 10 * 1024 * 1024)

    def test_a_report_is_saved_logged_and_emailed_like_the_website(self):
        r = self.post_json('support/bug-report/', {
            'description': '  The vote button does nothing  ', 'issue_type': 'functionality',
            'page': 'voting', 'priority': 'high', 'feature': 'Vote button',
            'steps_to_reproduce': '1. Open a vote', 'browser_info': 'Parliament app 4.4.0 (12) · iPhone 16 · iOS 26.1',
        })
        self.assertEqual(r.status_code, 201, r.content)
        report = BugReport.objects.get(pk=r.json()['id'])
        self.assertEqual((report.description, report.issue_type, report.page, report.priority, report.submitted_by),
                         ('The vote button does nothing', 'functionality', 'voting', 'high', self.user))
        self.assertEqual(report.browser_info, 'Parliament app 4.4.0 (12) · iPhone 16 · iOS 26.1')
        self.assertIsNone(r.json()['screenshot_problem'])
        self.assertTrue(ActivityLog.objects.filter(action_type='bug_report_submitted', object_id=report.id).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['admin@example.com'])

    def test_the_website_checks_apply(self):
        self.assertEqual(self.post_json('support/bug-report/', {'description': '  '}).json()['error'],
                         'missing_description')
        r = self.post_json('support/bug-report/', {'description': 'x', 'issue_type': 'nope', 'page': 'nope',
                                                   'priority': 'nope', 'page_url': 'a' * 900, 'feature': 7})
        self.assertEqual(r.status_code, 201)
        report = BugReport.objects.get(pk=r.json()['id'])
        self.assertEqual((report.issue_type, report.page, report.priority, len(report.page_url), report.feature),
                         ('other', '', 'medium', 500, '7'))

    def test_a_screenshot_can_be_attached_as_multipart(self):
        r = self.api.post(API + 'support/bug-report/', {'description': 'Look', 'screenshot': _png()},
                          **bearer(self.access))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(BugReport.objects.get(pk=r.json()['id']).screenshot)

    def test_a_bad_screenshot_keeps_the_report(self):
        fake = SimpleUploadedFile('shot.png', b'not an image', content_type='image/png')
        r = self.api.post(API + 'support/bug-report/', {'description': 'Look', 'screenshot': fake},
                          **bearer(self.access))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['screenshot_problem'], "That file isn't a readable image.")
        self.assertFalse(BugReport.objects.get(pk=r.json()['id']).screenshot)

    def test_signed_in_only(self):
        self.assertEqual(self.api.get(API + 'support/bug-report/').status_code, 401)
        self.assertEqual(self.api.post(API + 'support/feedback/', {}).status_code, 401)

    def test_pledge_restrictions_follow_the_web_pages(self):
        from src.models.education import PledgePageRestriction
        pledge = make_user('sup-pl', member_type='Pledge')
        _, access, _ = make_device(pledge)
        PledgePageRestriction.objects.create(url_name='bug_report', allowed_phases=[])
        r = self.api.get(API + 'support/bug-report/', **bearer(access))
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        self.assertEqual(self.api.get(API + 'support/feedback/', **bearer(access)).status_code, 200)

    # ── feedback ──

    def test_feedback_choices(self):
        body = self.api.get(API + 'support/feedback/', **bearer(self.access)).json()
        self.assertEqual([c['value'] for c in body['request_types']], ['feature_idea', 'support_ticket'])
        self.assertEqual([c['value'] for c in body['priorities']], [v for v, _ in FeedbackRequest.PRIORITY_CHOICES])

    def test_an_idea_is_posted_without_email(self):
        r = self.post_json('support/feedback/', {'request_type': 'feature_idea', 'title': 'Dark mode',
                                                 'description': 'For late chapters', 'page': 'home'})
        self.assertEqual(r.status_code, 201, r.content)
        fb = FeedbackRequest.objects.get(pk=r.json()['id'])
        self.assertEqual((fb.request_type, fb.title, fb.page, fb.submitted_by), ('feature_idea', 'Dark mode', 'home',
                                                                               self.user))
        self.assertEqual(len(mail.outbox), 0)
        log = ActivityLog.objects.get(action_type='feedback_submitted', object_id=fb.id)
        self.assertNotIn('Dark mode', log.description)   # v3.29.10: no free text in the log

    def test_a_ticket_emails_the_admin(self):
        r = self.post_json('support/feedback/', {'request_type': 'support_ticket', 'title': 'Locked out',
                                                 'description': 'Help', 'priority': 'urgent'})
        self.assertEqual(r.json()['request_type'], 'support_ticket')
        self.assertEqual(len(mail.outbox), 1)

    def test_feedback_needs_a_title_and_details_and_is_checked(self):
        r = self.post_json('support/feedback/', {'title': 'Only a title'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'missing_fields'))
        r = self.post_json('support/feedback/', {'request_type': 'bogus', 'title': 't' * 300, 'description': 'd',
                                                 'priority': 'bogus', 'page': 'bogus'})
        fb = FeedbackRequest.objects.get(pk=r.json()['id'])
        self.assertEqual((fb.request_type, len(fb.title), fb.priority, fb.page), ('support_ticket', 200, 'medium', ''))
