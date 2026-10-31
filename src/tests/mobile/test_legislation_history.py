"""
v4.4.0 — My Work (the Legislation History page) in the app.

The app must list what ``view_legislation_history`` lists and nothing else: the
member's own bills (posted or co-authored, once each), under the same status
tabs with the same counts and the same tallies (historical counts win), plus
their own polls and drafts. The page's toggle-free gate and pledge restriction
apply.

The routes are mounted by this module's own urlconf (the project's urlconf
plus these three paths) so the tests stand on their own.

    python manage.py test src.tests.mobile.test_legislation_history
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from django.utils import timezone

from src.mobile import views_legislation_history as v
from src.models import Announcement, AnnouncementPoll, Legislation, LegislationDraft, Vote
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict

API = '/api/mobile/v1/'

_routes = [
    path('legislation/history/', v.LegislationHistoryView.as_view(), name='mobile_legislation_history'),
    path('legislation/history/polls/', v.LegislationHistoryPollsView.as_view(),
         name='mobile_legislation_history_polls'),
    path('legislation/history/drafts/', v.LegislationHistoryDraftsView.as_view(),
         name='mobile_legislation_history_drafts'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_lh')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('lh-1')
        self.other = make_user('lh-2')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def bill(self, title, by=None, **kw):
        fields = dict(title=title, description='A bill about things.', posted_by=by or self.user,
                      available_at=timezone.now() - timedelta(days=1))
        fields.update(kw)
        return Legislation.objects.create(**fields)

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))


class ListTests(_Base):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.active = self.bill('Active bill', voting_starts_at=now - timedelta(hours=1))
        self.passed = self.bill('Passed bill', voting_closed=True, status='passed', passed=True,
                                available_at=now - timedelta(days=5), voting_ended_at=now - timedelta(days=4),
                                historical_yes_votes=17, historical_no_votes=0, historical_abstain_votes=1)
        self.failed = self.bill('Failed bill', voting_closed=True, passed=False,
                                available_at=now - timedelta(days=6))
        self.tabled = self.bill('Tabled bill', status='tabled', available_at=now - timedelta(days=7))
        # Co-authored AND posted: listed once.
        self.passed.co_authors.add(self.user, self.other)
        self.coauthored = self.bill('Their bill I co-wrote', by=self.other, available_at=now - timedelta(days=8))
        self.coauthored.co_authors.add(self.user)
        self.bill('Not mine', by=self.other)
        Vote.objects.create(user=self.other, legislation=self.failed, vote_choice='no')
        Vote.objects.create(user=self.user, legislation=self.failed, vote_choice='yes')
        Vote.objects.create(user=make_user('lh-3'), legislation=self.failed, vote_choice='no')

    def test_lists_own_bills_with_counts_and_tallies(self):
        r = self.get('legislation/history/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([b['title'] for b in body['bills']],
                         ['Active bill', 'Passed bill', 'Failed bill', 'Tabled bill', 'Their bill I co-wrote'])
        self.assertEqual(body['counts'], {'all': 5, 'active': 3, 'passed': 1, 'failed': 1, 'tabled': 1})
        self.assertEqual((body['page'], body['pages'], body['total'], body['has_next']), (1, 1, 5, False))
        bills = {b['title']: b for b in body['bills']}

        p = bills['Passed bill']
        self.assertEqual(p['state'], 'passed')
        self.assertEqual((p['tally']['yes'], p['tally']['no'], p['tally']['abstain']), (17, 0, 1))
        self.assertEqual((p['tally']['yes_pct'], p['tally']['required_pct']), (100.0, 51))
        self.assertTrue(p['is_co_author'])
        self.assertEqual(sorted(p['co_authors']), ['User lh-1', 'User lh-2'])
        self.assertFalse(p['on_ballot'])
        self.assertIsNone(p['links']['edit'])     # passed: "Submit new version", not Edit
        self.assertEqual(p['links']['detail'], f'/legislation/{self.passed.id}/')

        f = bills['Failed bill']
        self.assertEqual((f['state'], f['tally']['yes'], f['tally']['no'], f['tally']['total']), ('failed', 1, 2, 3))
        self.assertTrue(f['can_reopen'])
        self.assertEqual(f['links']['edit'], f'/legislation/{self.failed.id}/edit/')

        a = bills['Active bill']
        self.assertEqual(a['state'], 'active')
        self.assertTrue(a['on_ballot'])
        self.assertEqual(bills['Tabled bill']['state'], 'tabled')
        self.assertFalse(bills['Tabled bill']['on_ballot'])
        self.assertFalse(bills['Active bill']['is_co_author'])
        self.assertTrue(bills['Their bill I co-wrote']['is_co_author'])

    def test_status_filters_match_the_page(self):
        for status, titles in [
            ('active', ['Active bill', 'Tabled bill', 'Their bill I co-wrote']),
            ('passed', ['Passed bill']),
            ('failed', ['Failed bill']),
            ('tabled', ['Tabled bill']),
            ('bogus', None),
        ]:
            body = self.get(f'legislation/history/?status={status}').json()
            got = [b['title'] for b in body['bills']]
            if titles is None:
                self.assertEqual((body['status'], len(got)), ('all', 5))
            else:
                self.assertEqual(got, titles, status)
                self.assertEqual(body['status'], status)
            self.assertEqual(body['counts']['all'], 5)   # the tab counts never narrow

    def test_search_narrows_by_title(self):
        body = self.get('legislation/history/?q=FAIL').json()
        self.assertEqual([b['title'] for b in body['bills']], ['Failed bill'])
        self.assertEqual(body['total'], 1)
        body = self.get('legislation/history/?q=not+mine').json()
        self.assertEqual(body['bills'], [])          # someone else's bill never matches

    def test_pages_of_twenty(self):
        for i in range(20):
            self.bill(f'Extra {i}', available_at=timezone.now() - timedelta(days=30 + i))
        first = self.get('legislation/history/').json()
        self.assertEqual((len(first['bills']), first['pages'], first['has_next']), (20, 2, True))
        second = self.get('legislation/history/?page=2').json()
        self.assertEqual((len(second['bills']), second['page'], second['has_next']), (5, 2, False))
        self.assertFalse({b['id'] for b in first['bills']} & {b['id'] for b in second['bills']})

    def test_plurality_and_appointment_rows(self):
        plural = self.bill('Pick a colour', vote_mode='plurality', plurality_options=['Red', 'Blue'],
                           voting_closed=True, passed=True, anonymous_vote=True)
        Vote.objects.create(user=self.user, legislation=plural, vote_choice='Blue')
        runoff = self.bill('Pick a colour (runoff)', vote_mode='plurality', plurality_options=['Red', 'Blue'],
                           plurality_is_runoff=True, plurality_parent=plural)
        body = self.get('legislation/history/').json()
        rows = {b['title']: b for b in body['bills']}
        p = rows['Pick a colour']
        self.assertTrue(p['anonymous'])
        self.assertEqual(p['tally']['breakdown'], [{'option': 'Red', 'count': 0}, {'option': 'Blue', 'count': 1}])
        self.assertEqual(p['tally']['winner'], 'Blue')
        self.assertEqual(p['runoffs'], [{'id': runoff.id, 'title': runoff.title}])
        self.assertEqual(rows['Pick a colour (runoff)']['runoff_of'], {'id': plural.id, 'title': plural.title})

    def test_empty_and_pledge_restriction(self):
        _, access, _ = make_device(make_user('lh-empty'))
        body = self.get('legislation/history/', access).json()
        self.assertEqual((body['bills'], body['counts']['all'], body['total']), ([], 0, 0))

        pledge = make_user('lh-pledge', member_type='Pledge')
        _, pledge_access, _ = make_device(pledge)
        restrict('view_legislation_history', [])
        r = self.get('legislation/history/', pledge_access)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'legislation/history/').status_code, 401)


class PollsAndDraftsTests(_Base):

    def test_own_polls_only(self):
        mine = Announcement.objects.create(title='Retreat', content='c', posted_by=self.user)
        theirs = Announcement.objects.create(title='Other', content='c', posted_by=self.other)
        poll = AnnouncementPoll.objects.create(announcement=mine, title='Where to?', created_by=self.user)
        AnnouncementPoll.objects.create(announcement=theirs, title='Not mine', created_by=self.other)
        body = self.get('legislation/history/polls/').json()
        self.assertEqual([p['title'] for p in body['polls']], ['Where to?'])
        p = body['polls'][0]
        self.assertEqual((p['announcement']['title'], p['questions'], p['responses'], p['open']),
                         ('Retreat', 0, 0, True))
        self.assertEqual(p['links']['results'], f'/officers/announcements/{mine.id}/poll/results/')
        self.assertEqual(self.get('legislation/history/').json()['polls_count'], 1)
        self.assertEqual(poll.id, p['id'])

    def test_own_drafts_only(self):
        LegislationDraft.objects.create(author=self.user, title='Half done')
        published = self.bill('From a draft')
        LegislationDraft.objects.create(author=self.user, title='Done', description='x' * 30,
                                        published_legislation=published, published_at=timezone.now())
        LegislationDraft.objects.create(author=self.other, title='Secret')
        body = self.get('legislation/history/drafts/').json()
        drafts = {d['title']: d for d in body['drafts']}
        self.assertEqual(set(drafts), {'Half done', 'Done'})
        self.assertEqual(body['unpublished_count'], 1)
        self.assertFalse(drafts['Half done']['ready'])
        self.assertTrue(drafts['Half done']['not_ready_reason'])
        self.assertEqual(drafts['Half done']['links']['edit'],
                         f'/legislation/drafts/{drafts["Half done"]["id"]}/edit/')
        self.assertTrue(drafts['Done']['published'])
        self.assertEqual(drafts['Done']['bill'], {'id': published.id, 'title': 'From a draft'})
        self.assertIsNone(drafts['Done']['links']['edit'])
        self.assertFalse(body['can_publish'])        # a plain member: ask an officer
        self.assertEqual(self.get('legislation/history/').json()['drafts_count'], 1)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class WebPageUnchangedTests(_Base):
    """The page renders from the same helpers: same rows, same counts."""

    def test_web_and_app_agree(self):
        self.bill('One')
        self.bill('Two', voting_closed=True, status='failed')
        web = Client()
        web.force_login(self.user)
        r = web.get('/legislation/history/')
        self.assertEqual(r.status_code, 200)
        app = self.get('legislation/history/').json()
        self.assertEqual(r.context['status_counts'], app['counts'])
        self.assertEqual([i['legislation'].id for i in r.context['legislation_history']],
                         [b['id'] for b in app['bills']])
