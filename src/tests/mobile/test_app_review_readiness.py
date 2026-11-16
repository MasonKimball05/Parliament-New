"""
App Review readiness (11-16-26): what the first submission needs, judged
against guidelines 1.2 and 2.1.

* Terms of Use accepted once in the app (``me/`` says when; ``terms/accept/``).
* Reporting a member's profile and an idea, and officers acting on reports.
* The demo account's practice ballot: castable with re-auth, never recorded.

    python manage.py test src.tests.mobile.test_app_review_readiness
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src import content_reports, terms
from src.models import Attendance, ContentReport, FeedbackRequest, Legislation, Notification, Vote
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'
REVIEW = override_settings(APP_REVIEW_USERNAMES=frozenset({'appreview'}))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.member = make_user('rr-member', name='Riley Member')
        self.officer = make_user('rr-officer', name='Olive Officer', member_type='Officer')
        self.api = Client()
        _, self.access, _ = make_device(self.member)
        _, self.officer_access, _ = make_device(self.officer)

    def call(self, method, path, body=None, access=None):
        kw = bearer(access or self.access)
        if method == 'get':
            return self.api.get(API + path, **kw)
        return self.api.post(API + path, body or {}, content_type='application/json', **kw)


class TermsTests(_Base):

    def test_accept_once_then_again_when_the_terms_change(self):
        me = self.call('get', 'me/').json()['terms']
        self.assertEqual((me['accepted'], me['version'], me['url']), (False, terms.current_version(), '/terms/'))
        r = self.call('post', 'terms/accept/', {'version': '2020-01-01'})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'terms_changed'))
        self.assertEqual(self.call('post', 'terms/accept/', {'version': me['version']}).status_code, 200)
        self.assertTrue(self.call('get', 'me/').json()['terms']['accepted'])
        with self.settings():
            from src.view import legal
            old = legal.TERMS_EFFECTIVE_DATE
            legal.TERMS_EFFECTIVE_DATE = old + timedelta(days=1)
            try:
                self.assertFalse(self.call('get', 'me/').json()['terms']['accepted'])
            finally:
                legal.TERMS_EFFECTIVE_DATE = old

    def test_terms_page_mentions_profiles_and_ideas(self):
        self.assertContains(self.client.get('/terms/'), 'member profile or idea')


class ProfileReportTests(_Base):

    def setUp(self):
        super().setUp()
        self.other = make_user('rr-other', name='Otto Other', about_me='Something objectionable')

    def test_report_notifies_officers_and_removal_clears_the_profile(self):
        path = f'members/{self.other.pk}/report/'
        self.assertEqual(self.call('post', path, {'reason': 'nope'}).json()['error'], 'invalid_input')
        r = self.call('post', path, {'reason': 'harassment', 'details': 'Rude'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.call('post', path, {'reason': 'harassment'}).json()['error'], 'already_reported')
        self.assertEqual(self.call('post', f'members/{self.member.pk}/report/', {'reason': 'spam'}).json()['error'],
                         'own_profile')
        self.assertTrue(Notification.objects.filter(recipient=self.officer, notification_type='content_report')
                        .exists())
        self.assertFalse(Notification.objects.filter(recipient=self.other).exists())

        # Members can't see the queue; officers can, and act.
        self.assertEqual(self.call('get', 'officer/content-reports/').status_code, 403)
        queue = self.call('get', 'officer/content-reports/', access=self.officer_access).json()
        self.assertEqual(queue['open_count'], 1)
        row = queue['reports'][0]
        self.assertEqual((row['kind'], row['reporter'], row['about_me']),
                         ('profile', 'Riley Member', 'Something objectionable'))
        r = self.call('post', f'officer/content-reports/{row["id"]}/', {'action': 'remove'},
                      access=self.officer_access)
        self.assertEqual(r.json()['report']['status'], 'actioned')
        self.other.refresh_from_db()
        self.assertEqual(self.other.about_me, '')
        # A second review of the same report is refused.
        r = self.call('post', f'officer/content-reports/{row["id"]}/', {'action': 'dismiss'},
                      access=self.officer_access)
        self.assertEqual(r.status_code, 409)


class IdeaReportTests(_Base):

    def setUp(self):
        super().setUp()
        self.author = make_user('rr-author', name='Ada Author')
        self.idea = FeedbackRequest.objects.create(request_type='feature_idea', title='Bad idea',
                                                   description='d', submitted_by=self.author)

    def board(self):
        return [i['id'] for i in self.call('get', 'support/feedback/ideas/').json()['ideas']]

    def test_enough_reports_hide_it_and_dismissing_brings_it_back(self):
        path = f'support/feedback/ideas/{self.idea.pk}/report/'
        _, author_access, _ = make_device(self.author)
        self.assertEqual(self.call('post', path, {'reason': 'spam'}, access=author_access).json()['error'],
                         'own_idea')
        reporters = [self.member] + [make_user(f'rr-r{i}') for i in range(content_reports.AUTO_HIDE_THRESHOLD - 1)]
        self.assertIn(self.idea.pk, self.board())
        for who in reporters:
            _, access, _ = make_device(who)
            self.assertEqual(self.call('post', path, {'reason': 'spam'}, access=access).status_code, 201)
        self.idea.refresh_from_db()
        self.assertIsNotNone(self.idea.hidden_at)
        self.assertNotIn(self.idea.pk, self.board())
        self.assertEqual(self.call('get', f'support/feedback/{self.idea.pk}/').status_code, 404)
        # Its author still sees it.
        self.assertEqual(self.call('get', f'support/feedback/{self.idea.pk}/', access=author_access).status_code,
                         200)

        report = ContentReport.objects.filter(idea=self.idea).first()
        self.call('post', f'officer/content-reports/{report.pk}/', {'action': 'dismiss'}, access=self.officer_access)
        self.idea.refresh_from_db()
        self.assertIsNone(self.idea.hidden_at)
        self.assertFalse(ContentReport.objects.filter(idea=self.idea, status='open').exists())
        self.assertIn(self.idea.pk, self.board())

    def test_officer_hides_it_from_the_website_page(self):
        self.call('post', f'support/feedback/ideas/{self.idea.pk}/report/', {'reason': 'hate'})
        report = ContentReport.objects.get()
        web = Client()
        web.force_login(self.member)
        self.assertEqual(web.get(reverse('content_reports')).status_code, 403)
        web.force_login(self.officer)
        page = web.get(reverse('content_reports'))
        self.assertContains(page, 'Bad idea')
        self.assertContains(page, 'Riley Member')
        web.post(reverse('content_report_review', args=[report.pk]), {'action': 'remove'})
        self.idea.refresh_from_db()
        self.assertIsNotNone(self.idea.hidden_at)


@REVIEW
class PracticeBallotTests(_Base):

    def setUp(self):
        super().setUp()
        self.reviewer = make_user('appreview', username='appreview', name='App Review')
        _, self.review_access, _ = make_device(self.reviewer)
        now = timezone.now()
        Legislation.objects.create(title='Real bill', description='d', posted_by=self.officer,
                                   available_at=now - timedelta(hours=1), voting_starts_at=now - timedelta(hours=1))

    def test_only_the_review_account_sees_it_and_nothing_is_recorded(self):
        Attendance.objects.create(user=self.member, status='present')
        titles = [v['title'] for v in self.call('get', 'votes/').json()['votes']]
        self.assertEqual(titles, ['Real bill'])
        self.assertEqual(self.call('get', 'votes/0/').status_code, 404)

        body = self.call('get', 'votes/', access=self.review_access).json()
        self.assertEqual([v['id'] for v in body['votes']][-1], 0)
        self.assertTrue(body['me']['can_cast_now'])
        detail = self.call('get', 'votes/0/', access=self.review_access).json()['vote']
        self.assertEqual(detail['options'], ['yes', 'no', 'abstain'])

        r = self.call('post', 'votes/0/cast/', {'choices': ['yes'], 'password': 'wrong'},
                      access=self.review_access)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        r = self.call('post', 'votes/0/cast/', {'choices': ['yes'], 'password': 'pw-appreview'},
                      access=self.review_access)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['receipt'].startswith('PRACTICE-'))
        self.assertFalse(Vote.objects.exists())
        voted = [v for v in self.call('get', 'votes/', access=self.review_access).json()['votes'] if v['id'] == 0]
        self.assertTrue(voted[0]['voted'])
        r = self.call('post', 'votes/0/cast/', {'choices': ['no'], 'password': 'pw-appreview'},
                      access=self.review_access)
        self.assertEqual(r.json()['error'], 'invalid_choice')

        # Real ballots are still refused for it.
        real = Legislation.objects.get().pk
        r = self.call('post', f'votes/{real}/cast/', {'choices': ['yes'], 'password': 'pw-appreview'},
                      access=self.review_access)
        self.assertEqual(r.json()['error'], 'not_eligible')
