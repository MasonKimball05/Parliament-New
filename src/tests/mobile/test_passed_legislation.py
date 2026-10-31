"""
v4.4.0 — the Legislation Tracker (Passed Legislation page) in the app.

The app must list what ``passed_legislation`` lists and nothing else: the same
tabs with the same counts, rows and order, the same tallies (historical counts
win), the member's own ballots and receipts on the Personal tab, and the
detail page's results, present members and roll call. The page toggle applies
to the list (the web page has one); only the pledge restriction applies to the
detail page (it has none). Officer-only parts follow the page's rule.

The routes are mounted by this module's own urlconf (the project's urlconf
plus these two paths) so the tests stand on their own.

    python manage.py test src.tests.mobile.test_passed_legislation
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from django.utils import timezone

from src.mobile import views_passed_legislation as v
from src.models import Attendance, Legislation, Vote
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'

_routes = [
    path('legislation/passed/', v.PassedLegislationView.as_view(), name='mobile_passed_legislation'),
    path('legislation/passed/<int:legislation_id>/', v.PassedLegislationDetailView.as_view(),
         name='mobile_passed_legislation_detail'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_pl')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('pl-1')
        self.other = make_user('pl-2')
        _, self.access, _ = make_device(self.user)
        self.api = Client()
        now = timezone.now()
        self.passed = self.bill('Passed bill', voting_closed=True, status='passed', passed=True,
                                voting_ended_at=now - timedelta(days=1),
                                historical_yes_votes=17, historical_no_votes=3, historical_abstain_votes=1)
        self.failed = self.bill('Failed bill', voting_closed=True, status='failed',
                                voting_ended_at=now - timedelta(days=2))
        self.tabled = self.bill('Tabled bill', status='tabled', by=self.user)
        self.open = self.bill('Open bill', voting_starts_at=now - timedelta(hours=1))
        self.bill('Removed bill', status='removed', voting_closed=True)
        self.bill('Inactive bill', is_active=False, voting_closed=True, passed=True)
        self.bill('Someone else scheduled', available_at=now + timedelta(days=1))
        self.passed.co_authors.add(self.other)
        Vote.objects.create(user=self.user, legislation=self.failed, vote_choice='yes')
        Vote.objects.create(user=self.other, legislation=self.failed, vote_choice='no')
        Vote.objects.create(user=make_user('pl-3'), legislation=self.failed, vote_choice='no')

    def bill(self, title, by=None, **kw):
        fields = dict(title=title, description='A bill about things.', posted_by=by or self.other,
                      available_at=timezone.now() - timedelta(days=3))
        fields.update(kw)
        return Legislation.objects.create(**fields)

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))


class ListTests(_Base):

    def test_all_closed_tab_counts_and_tallies(self):
        r = self.get('legislation/passed/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['status'], 'all')
        self.assertEqual([b['title'] for b in body['bills']], ['Passed bill', 'Failed bill'])
        self.assertEqual(body['counts'], {'all': 2, 'pending': 0, 'active': 1, 'passed': 1,
                                          'failed': 1, 'tabled': 1, 'personal': 1})
        self.assertEqual((body['page'], body['pages'], body['total'], body['has_next']), (1, 1, 2, False))
        self.assertFalse(body['can_manage'])
        self.assertIsNone(body['ballots'])
        p, f = body['bills']
        self.assertEqual((p['state'], p['vote_passed'], p['posted_by'], p['co_authors']),
                         ('passed', True, 'User pl-2', ['User pl-2']))
        self.assertEqual((p['tally']['yes'], p['tally']['no'], p['tally']['abstain']), (17, 3, 1))
        self.assertEqual((p['tally']['yes_pct'], p['tally']['no_pct'], p['tally']['required_pct']), (85.0, 15.0, 51))
        self.assertIsNone(p['tally']['breakdown'])
        self.assertEqual(p['links']['detail'], f'/legislation/detail/{self.passed.id}/')
        self.assertIsNone(p['links']['edit'])
        self.assertIsNone(p['present_members'])          # a member, not an officer
        self.assertFalse(p['on_ballot'])
        self.assertEqual((f['state'], f['vote_passed'], f['tally']['yes'], f['tally']['no']),
                         ('failed', False, 1, 2))

    def test_tabs_match_the_page(self):
        for status, titles in [
            ('active', ['Open bill']),
            ('passed', ['Passed bill']),
            ('failed', ['Failed bill']),
            ('tabled', ['Tabled bill']),
            ('pending', []),
            ('bogus', ['Passed bill', 'Failed bill']),
        ]:
            body = self.get(f'legislation/passed/?status={status}').json()
            self.assertEqual([b['title'] for b in body['bills']], titles, status)
            self.assertEqual(body['status'], 'all' if status == 'bogus' else status)
        rows = {b['title']: b for b in self.get('legislation/passed/?status=active').json()['bills']}
        self.assertTrue(rows['Open bill']['on_ballot'])
        self.assertEqual(rows['Open bill']['state'], 'active')
        tabled = self.get('legislation/passed/?status=tabled').json()['bills'][0]
        self.assertEqual(tabled['links']['edit'], f'/legislation/{self.tabled.id}/edit/')  # its author

    def test_scheduled_pending_only_shown_to_its_author(self):
        self.assertEqual(self.get('legislation/passed/?status=pending').json()['bills'], [])
        _, other_access = make_device(self.other)[:2]
        body = self.get('legislation/passed/?status=pending', other_access).json()
        self.assertEqual([b['title'] for b in body['bills']], ['Someone else scheduled'])

    def test_search_narrows_rows_not_counts(self):
        body = self.get('legislation/passed/?q=FAIL').json()
        self.assertEqual([b['title'] for b in body['bills']], ['Failed bill'])
        self.assertEqual((body['total'], body['counts']['all']), (1, 2))

    def test_pages_of_twenty(self):
        for i in range(20):
            self.bill(f'Extra {i}', voting_closed=True, passed=True,
                      voting_ended_at=timezone.now() - timedelta(days=10 + i))
        first = self.get('legislation/passed/').json()
        self.assertEqual((len(first['bills']), first['pages'], first['has_next']), (20, 2, True))
        second = self.get('legislation/passed/?page=2').json()
        self.assertEqual((len(second['bills']), second['page'], second['has_next']), (2, 2, False))

    def test_plurality_breakdown(self):
        plural = self.bill('Pick a colour', vote_mode='plurality', plurality_options=['Red', 'Blue'],
                           voting_closed=True, passed=True, voting_ended_at=timezone.now())
        Vote.objects.create(user=self.user, legislation=plural, vote_choice='Blue')
        row = self.get('legislation/passed/').json()['bills'][0]
        self.assertEqual(row['title'], 'Pick a colour')
        self.assertEqual(row['tally']['breakdown'], [{'option': 'Red', 'count': 0}, {'option': 'Blue', 'count': 1}])
        self.assertEqual(row['tally']['winner'], 'Blue')

    def test_personal_tab_ballots_and_receipts(self):
        Vote.objects.create(user=self.user, legislation=self.open, vote_choice='no')
        old = self.bill('Old bill', voting_closed=True, passed=True)
        old_vote = Vote.objects.create(user=self.user, legislation=old, vote_choice='abstain')
        Vote.objects.filter(pk=old_vote.pk).update(cast_at=timezone.now() - timedelta(days=200))
        body = self.get('legislation/passed/?status=personal').json()
        self.assertEqual(body['bills'], [])
        self.assertEqual(body['counts']['personal'], 3)
        ballots = {b['legislation']['title']: b for b in body['ballots']}
        self.assertEqual(set(ballots), {'Failed bill', 'Open bill', 'Old bill'})
        self.assertEqual((ballots['Failed bill']['choices'], ballots['Failed bill']['result']), (['yes'], 'failed'))
        self.assertEqual(ballots['Open bill']['result'], 'open')
        self.assertTrue(ballots['Failed bill']['receipt'])
        self.assertTrue(ballots['Failed bill']['links']['verify'].startswith('/vote/verify-receipt/?receipt='))
        self.assertTrue(ballots['Old bill']['receipt_expired'])
        self.assertIsNone(ballots['Old bill']['receipt'])
        self.assertIsNone(ballots['Old bill']['links']['verify'])
        self.assertEqual(body['ballots'][-1]['legislation']['title'], 'Old bill')   # newest first

    def test_officer_sees_present_members_and_manage_links(self):
        officer = make_user('pl-off', member_type='Officer')
        _, access, _ = make_device(officer)
        att = Attendance.objects.create(user=self.other, status='present')
        Attendance.objects.filter(pk=att.pk).update(created_at=self.failed.voting_ended_at - timedelta(hours=1))
        body = self.get('legislation/passed/', access).json()
        self.assertTrue(body['can_manage'])
        rows = {b['title']: b for b in body['bills']}
        self.assertEqual(rows['Failed bill']['present_members'], ['User pl-2'])
        tabled = self.get('legislation/passed/?status=tabled', access).json()['bills'][0]
        self.assertEqual(tabled['links']['edit'], f'/legislation/{self.tabled.id}/edit/')
        # Admins manage, but the roster is the page's Officer-only check.
        admin = make_user('pl-adm', is_admin=True)
        _, admin_access, _ = make_device(admin)
        body = self.get('legislation/passed/', admin_access).json()
        self.assertTrue(body['can_manage'])
        self.assertIsNone(body['bills'][0]['present_members'])

    def test_toggle_and_pledge_restriction(self):
        toggle('passed_legislation', False)
        r = self.get('legislation/passed/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'page_disabled'))
        toggle('passed_legislation', True)
        pledge = make_user('pl-pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('passed_legislation', [])
        r = self.get('legislation/passed/', access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'legislation/passed/').status_code, 401)


class DetailTests(_Base):

    def test_percentage_result_present_members_and_roll_call(self):
        att = Attendance.objects.create(user=self.other, status='late')
        Attendance.objects.filter(pk=att.pk).update(created_at=self.failed.voting_ended_at - timedelta(hours=2))
        r = self.get(f'legislation/passed/{self.failed.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d['title'], d['state'], d['posted_by']), ('Failed bill', 'failed', 'User pl-2'))
        res = d['result']
        self.assertEqual((res['mode'], res['yes'], res['no'], res['abstain'], res['total']), ('percentage', 1, 2, 0, 3))
        self.assertEqual((res['yes_pct'], res['required_pct'], res['passed']), (33.3, 51, False))
        self.assertEqual(d['present_members'], ['User pl-2'])     # every member, as on the page
        self.assertEqual(sorted((x['name'], x['choice']) for x in d['roll_call']),
                         [('User pl-1', 'yes'), ('User pl-2', 'no'), ('User pl-3', 'no')])
        self.assertEqual(d['links']['page'], f'/legislation/detail/{self.failed.id}/')

    def test_anonymous_has_no_roll_call_and_open_has_no_result(self):
        self.failed.anonymous_vote = True
        self.failed.save(update_fields=['anonymous_vote'])
        self.assertEqual(self.get(f'legislation/passed/{self.failed.id}/').json()['roll_call'], [])
        d = self.get(f'legislation/passed/{self.open.id}/').json()
        self.assertIsNone(d['result'])
        self.assertTrue(d['on_ballot'])

    def test_historical_counts_and_missing(self):
        d = self.get(f'legislation/passed/{self.passed.id}/').json()
        self.assertEqual((d['state'], d['result']['yes'], d['result']['total']), ('passed', 17, 21))
        self.assertEqual(self.get('legislation/passed/999999/').status_code, 404)

    def test_no_toggle_but_pledge_restriction(self):
        toggle('passed_legislation_detail', False)
        self.assertEqual(self.get(f'legislation/passed/{self.passed.id}/').status_code, 200)
        pledge = make_user('pl-pledge2', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('passed_legislation_detail', [])
        r = self.get(f'legislation/passed/{self.passed.id}/', access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))


class WebPageUnchangedTests(_Base):
    """The pages render from the same helpers: same rows, same counts, same results."""

    def test_web_and_app_agree(self):
        web = Client()
        web.force_login(self.user)
        for status in ('all', 'active', 'passed', 'failed', 'tabled', 'pending'):
            r = web.get(f'/passed-legislation/?status={status}')
            self.assertEqual(r.status_code, 200)
            app = self.get(f'legislation/passed/?status={status}').json()
            self.assertEqual(r.context['status_counts'], app['counts'])
            self.assertEqual([i['legislation'].id for i in r.context['passed_legislation']],
                             [b['id'] for b in app['bills']], status)
        r = web.get('/passed-legislation/?status=personal')
        app = self.get('legislation/passed/?status=personal').json()
        self.assertEqual([b['receipt'] for b in r.context['personal_ballots']],
                         [b['receipt'] for b in app['ballots']])

        r = web.get(f'/legislation/detail/{self.failed.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['vote_result']['yes'], 1)
        self.assertEqual(len(r.context['individual_votes']), 3)
