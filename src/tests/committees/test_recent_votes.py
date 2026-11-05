"""
11-05-26: a committee home's Recent Votes listed BALLOTS (CommitteeVote: one
row per member per choice, with no title), so it showed blank rows and the
Votes tile counted ballots. It now lists the votes themselves
(CommitteeLegislation), newest first, each Open / Passed / Did not pass and
linking to the vote or its result; the tiles count votes and open votes.

    python manage.py test src.tests.committees.test_recent_votes
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Committee, CommitteeLegislation, CommitteeVote
from src.models_feature_flags import FeatureFlag
from src.tests.landing.test_web_refresh_pages_c import make_user


class RecentVotesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.chair = make_user('rv-1', name='Avery Chairwell')
        self.members = [make_user(f'rv-{i}') for i in range(2, 6)]
        self.committee = Committee.objects.create(name='Votes Finance', code='RVF', is_active=True)
        self.committee.members.add(self.chair, *self.members)
        self.committee.chairs.add(self.chair)
        now = timezone.now()
        mk = lambda title, **kw: CommitteeLegislation.objects.create(
            committee=self.committee, title=title, description='d', posted_by=self.chair, **kw)
        self.open = mk('Raise the social budget', available_at=now - timedelta(hours=1))
        self.passed = mk('Approve the fall audit', available_at=now - timedelta(days=3), voting_closed=True,
                         passed=True, status='passed')
        self.failed = mk('Buy a boat', available_at=now - timedelta(days=5), voting_closed=True)
        mk('Removed one', available_at=now - timedelta(days=1), status='removed')
        mk('Not yet visible', available_at=now + timedelta(days=1))
        # Many ballots: they must not show up as "votes".
        for m in self.members:
            CommitteeVote.objects.create(user=m, legislation=self.passed, vote_choice='yes')
            CommitteeVote.objects.create(user=m, legislation=self.open, vote_choice='no')
        self.client = Client()
        self.client.force_login(self.chair)

    def home(self):
        r = self.client.get(reverse('committee_home', args=[self.committee.code]))
        self.assertEqual(r.status_code, 200)
        return r

    def test_lists_votes_not_ballots(self):
        r = self.home()
        recent = r.context['recent_votes']
        self.assertEqual([v.title for v in recent],
                         ['Raise the social budget', 'Approve the fall audit', 'Buy a boat'])
        self.assertEqual([v.is_open for v in recent], [True, False, False])

    def test_tiles_count_votes(self):
        stats = self.home().context['stats']
        self.assertEqual((stats['total_votes'], stats['active_votes']), (4, 1))   # removed one left out

    def test_each_row_says_where_it_stands_and_links(self):
        for refresh in (False, True):
            FeatureFlag.objects.update_or_create(name='web_refresh', defaults={
                'display_name': 'Website Refresh', 'description': 't', 'category': 'ui', 'is_enabled': refresh})
            cache.clear()
            html = self.home().content.decode()
            self.assertIn('Raise the social budget', html)
            self.assertIn('Did not pass', html)
            self.assertIn(reverse('committee_vote_result', args=[self.committee.code, self.passed.id]), html)
