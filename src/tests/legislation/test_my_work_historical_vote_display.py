"""
v3.29.18 — Mason reported: on My Work, "Cover Page and Foreword" showed
"Passed" in the top status badge but "Failed" in the percentage vote-result
bar directly below it, for the same bill, with 17 yes / 0 no / 0 abstain
against a 51% threshold (100% yes — obviously a pass).

Root cause: `view_legislation_history` (the view behind My Work) used to
recompute `leg.passed` on every GET via `leg.set_passed(counts=counts)`,
where `counts` is the raw aggregate of real `Vote` rows — captured BEFORE
the historical-vote overlay (`historical_yes_votes`/`historical_no_votes`/
`historical_abstain_votes`) is applied to the display numbers a few lines
above. For legislation closed via the officer "mark as voted" manual-entry
tool (no real Vote rows at all), `counts` was `{}`, so `set_passed()`
divided by a zero total and forced-and-PERSISTED `passed = False` —
corrupting a correctly-passed bill purely from viewing the page. The top
badge reads `legislation.status` (untouched, still 'passed'); the
vote-result bar reads `item.passed` (the value just corrupted) — hence the
disagreement.

Fix: stop recomputing `passed` in this view's render loop entirely. Every
real vote-closing path (`auto_close_votes` management command, `end_vote`
view) already sets `passed` correctly at the moment voting closes; this
page — like its sibling `passed_legislation.py` — just trusts the stored
value.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from src.models import Legislation

ParliamentUser = get_user_model()


class MyWorkTrustsStoredPassedTests(TestCase):
    """
    Reproduces Mason's exact numbers (17 yes / 0 no / 0 abstain, 51%
    threshold, manually-recorded outcome — no real Vote rows) and asserts
    both widgets on the page agree, and that the stored `passed` value
    survives a GET rather than being silently flipped and saved.
    """

    def setUp(self):
        self.author = ParliamentUser.objects.create_user(
            user_id='hist1', name='Historical Author', username='hist1',
            member_type='Officer')
        self.author.set_password('testpass')
        self.author.save()
        self.client = Client()
        self.client.force_login(self.author)

        # Mirrors what edit_legislation.py's "mark as voted" action writes:
        # status + passed set directly and consistently, plus historical_*
        # counts, and crucially NO real Vote rows (a manually-recorded
        # historical outcome has none).
        self.leg = Legislation.objects.create(
            title='Cover Page and Foreword',
            description='Add a cover page and foreword.',
            posted_by=self.author,
            available_at=timezone.now() - timedelta(days=30),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf',
            voting_closed=True,
            voting_ended_at=timezone.now() - timedelta(days=1),
            status='passed',
            passed=True,
            historical_yes_votes=17,
            historical_no_votes=0,
            historical_abstain_votes=0,
        )

    def test_my_work_shows_passed_not_failed(self):
        resp = self.client.get(reverse('view_legislation_history'))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertIn('Cover Page and Foreword', body)
        # `item.passed` is the single value both the status badge AND the
        # percentage vote-result bar key off of (see legislation_history.html
        # lines ~143 and ~299-301) — if this is True, both widgets render
        # "Passed" for this row. (Not asserting on a bare `assertNotIn
        # ('Failed', body)` here: the page's own status-filter tabs render
        # the literal word "Failed" as a tab label regardless of this row.)
        item = resp.context['legislation_history'][0]
        self.assertTrue(item['passed'])
        self.assertEqual(item['yes'], 17)
        self.assertEqual(item['no'], 0)
        self.assertEqual(item['abstain'], 0)
        self.assertEqual(item['yes_pct_num'], 100.0)

    def test_get_does_not_flip_or_persist_passed(self):
        """The actual bug: viewing the page used to call set_passed() with
        an empty counts dict and SAVE the resulting False back to the DB."""
        self.client.get(reverse('view_legislation_history'))
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.passed)
        self.assertEqual(self.leg.status, 'passed')

    def test_get_twice_is_still_consistent(self):
        """The corruption compounded on repeated views in the original bug
        (each GET re-ran the faulty recompute) — confirm idempotence."""
        self.client.get(reverse('view_legislation_history'))
        self.client.get(reverse('view_legislation_history'))
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.passed)

    def test_status_badge_and_vote_bar_agree(self):
        resp = self.client.get(reverse('view_legislation_history'))
        self.assertIn('Passed', resp.content.decode('utf-8'))
        item = resp.context['legislation_history'][0]
        # Both the badge (`item.legislation.status == 'passed' or
        # item.passed`) and the vote bar (`item.passed`) are driven off
        # values that must now agree: status stayed 'passed' and passed
        # was never recomputed away from it.
        self.assertEqual(item['legislation'].status, 'passed')
        self.assertTrue(item['passed'])


class HomePageShowsHistoricalVoteTotalTests(TestCase):
    """
    v3.29.19 — same bill, same bug family, a different page: the home
    page's "Recently Passed Legislation" card showed "100%" (correct) next
    to "0 votes" (wrong — should be 17) for a bill with no real `Vote`
    rows and `historical_yes_votes=17`.

    Root cause: `src/view/home.py`'s `recently_passed_legislation` query
    annotates `total_votes=Count('vote')` (real Vote rows only) and the
    render loop used that annotation directly for the card's "N votes"
    caption, while the `yes`/`no` values two lines above it already had
    the historical-counts fallback applied. Fixed by summing the
    historical fields for the total when they're set, same gate the
    existing `yes` fallback already uses (`historical_yes_votes is not
    None`).
    """

    def setUp(self):
        self.viewer = ParliamentUser.objects.create_user(
            user_id='homeview1', name='Home Viewer', username='homeview1',
            member_type='Member')
        self.viewer.set_password('testpass')
        self.viewer.save()
        self.author = ParliamentUser.objects.create_user(
            user_id='homeauth1', name='Historical Author 2', username='homeauth1',
            member_type='Officer')
        self.author.set_password('testpass')
        self.author.save()
        self.client = Client()
        self.client.force_login(self.viewer)

        self.leg = Legislation.objects.create(
            title='Cover Page and Foreword',
            description='Add a cover page and foreword.',
            posted_by=self.author,
            available_at=timezone.now() - timedelta(days=30),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf',
            voting_closed=True,
            voting_ended_at=timezone.now() - timedelta(days=1),
            status='passed',
            passed=True,
            historical_yes_votes=17,
            historical_no_votes=0,
            historical_abstain_votes=0,
        )

    def test_home_page_shows_seventeen_votes_not_zero(self):
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)
        previews = [
            p for p in resp.context['legislation_previews']
            if p['title'] == 'Cover Page and Foreword'
        ]
        self.assertEqual(len(previews), 1)
        item = previews[0]
        self.assertEqual(item['total_votes'], 17)
        self.assertEqual(item['yes_percentage'], '100%')

    def test_home_page_body_shows_seventeen_votes(self):
        resp = self.client.get(reverse('home'))
        self.assertContains(resp, '17 vote')
        self.assertNotContains(resp, '0 vote')
