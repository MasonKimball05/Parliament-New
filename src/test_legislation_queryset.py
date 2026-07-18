"""
v3.14.1 — LegislationQuerySet.open_for_voting() / .visible().

The open-for-voting invariant used to be hand-copied Q-logic in home.py and
api/views.py (and had rotted once already: pre-v3.13.3 `status='active'`
filters that never matched). These tests pin the queryset semantics and
assert the two call sites actually use the method, so the invariant can't
silently fork again.
"""
import inspect
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from src.models import Legislation, ParliamentUser


class LegislationQuerySetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = ParliamentUser.objects.create_user(
            user_id='qs1', name='QS Author', username='qs1', member_type='Member')
        cls.now = timezone.now()

    def _leg(self, **kwargs):
        defaults = dict(
            title='QS Test', description='D', posted_by=self.author,
            available_at=self.now - timedelta(hours=1),
            vote_mode='percentage', required_percentage='51',
            document='test.pdf',
        )
        defaults.update(kwargs)
        return Legislation.objects.create(**defaults)

    def _open_ids(self):
        return set(Legislation.objects.open_for_voting(self.now)
                   .values_list('id', flat=True))

    # --- open_for_voting ----------------------------------------------------

    def test_open_when_scheduled_start_has_passed(self):
        leg = self._leg(voting_starts_at=self.now - timedelta(minutes=5))
        self.assertIn(leg.id, self._open_ids())

    def test_excluded_when_start_is_in_the_future(self):
        leg = self._leg(voting_starts_at=self.now + timedelta(hours=1))
        self.assertNotIn(leg.id, self._open_ids())

    def test_null_start_auto_opens_with_availability(self):
        leg = self._leg(voting_starts_at=None, voting_manual_open=False)
        self.assertIn(leg.id, self._open_ids())

    def test_manual_open_stays_closed_until_author_opens(self):
        leg = self._leg(voting_starts_at=None, voting_manual_open=True)
        self.assertNotIn(leg.id, self._open_ids())
        # Author hits "Open Voting Now" → voting_starts_at gets set
        leg.voting_starts_at = self.now - timedelta(seconds=1)
        leg.save(update_fields=['voting_starts_at'])
        self.assertIn(leg.id, self._open_ids())

    def test_closed_vote_excluded(self):
        leg = self._leg(voting_closed=True)
        self.assertNotIn(leg.id, self._open_ids())

    def test_not_yet_available_excluded(self):
        leg = self._leg(available_at=self.now + timedelta(hours=1))
        self.assertNotIn(leg.id, self._open_ids())

    def test_closed_statuses_excluded(self):
        for status in Legislation.CLOSED_STATUSES:
            leg = self._leg(status=status)
            self.assertNotIn(leg.id, self._open_ids(),
                             f"status={status!r} should be excluded")

    def test_queryset_matches_instance_method(self):
        """Queryset predicates must agree with Legislation.voting_has_started()
        for every fixture — instance and queryset logic must not drift."""
        self._leg(voting_starts_at=self.now - timedelta(minutes=5))
        self._leg(voting_starts_at=self.now + timedelta(hours=1))
        self._leg(voting_starts_at=None, voting_manual_open=False)
        self._leg(voting_starts_at=None, voting_manual_open=True)
        open_ids = self._open_ids()
        for leg in Legislation.objects.all():
            expected = (leg.voting_has_started() and not leg.voting_closed
                        and leg.is_available()
                        and leg.status not in Legislation.CLOSED_STATUSES)
            self.assertEqual(leg.id in open_ids, expected, leg.id)

    # --- visible ------------------------------------------------------------

    def test_visible(self):
        past = self._leg()
        future = self._leg(available_at=self.now + timedelta(hours=1))
        visible = set(Legislation.objects.visible(self.now)
                      .values_list('id', flat=True))
        self.assertIn(past.id, visible)
        self.assertNotIn(future.id, visible)

    # --- call sites use the method (anti-rot guard) -------------------------

    def test_home_and_api_use_the_queryset_method(self):
        from src.view.home import home
        from src.api import views as api_views
        self.assertIn('open_for_voting', inspect.getsource(home),
                      "home.py re-derives the open-for-voting invariant "
                      "instead of using LegislationQuerySet.open_for_voting()")
        self.assertIn(
            'open_for_voting',
            inspect.getsource(api_views.LegislationViewSet.active),
            "api/views.py 'active' endpoint re-derives the invariant")
