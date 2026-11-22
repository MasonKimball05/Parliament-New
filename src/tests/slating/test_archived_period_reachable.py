"""
An archived slating period can still be managed (11-22-26).

The slating dashboard and the app's hub listed only active periods, so once a
period was archived nothing linked to it: its chair couldn't get back to
destroy the interview notes, as the bylaws require, or delete it. Both now
list archived periods to whoever ran or reviewed them.

    python manage.py test src.tests.slating.test_archived_period_reachable
"""
from django.urls import reverse
from django.utils import timezone

from src.models import SlatingInterview, SlatingPeriod
from src.tests.mobile.test_slating_batch5 import _AppBase
from src.tests.slating.test_slating_review_fixes import member


class ArchivedPeriodTests(_AppBase):

    def setUp(self):
        super().setUp()
        self.interview = SlatingInterview.objects.create(application=self.app, completed_at=timezone.now(),
                                                         notes='CONFIDENTIAL')
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='archived')
        self.committee.members.add(self.committee_member)

    def dashboard(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('slating_dashboard'))

    def test_the_manager_sees_it_with_manage_and_interviews(self):
        r = self.dashboard(self.manager)
        self.assertContains(r, f'data-archived-period="{self.period.id}"')
        self.assertContains(r, '1 interview still has notes')
        self.assertContains(r, reverse('slating_period_setup', args=[self.period.id]))
        self.assertContains(r, reverse('slating_interviews', args=[self.period.id]))

    def test_a_committee_member_sees_it_without_manage(self):
        if not self.period.slating_committee_id:
            self.skipTest('fixture period has no slating committee')
        r = self.dashboard(self.committee_member)
        self.assertContains(r, f'data-archived-period="{self.period.id}"')
        self.assertNotContains(r, reverse('slating_period_setup', args=[self.period.id]))

    def test_an_ordinary_member_does_not(self):
        self.assertNotContains(self.dashboard(member('ap-plain')), 'data-archived-elections')

    def test_the_manager_can_still_destroy_the_notes(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse('slating_destroy_notes', args=[self.period.id]), {'confirm': 'DESTROY'})
        self.assertEqual(r.status_code, 302)
        self.interview.refresh_from_db()
        self.assertTrue(self.interview.notes_destroyed)
        self.assertContains(self.dashboard(self.manager), 'interview notes destroyed')

    def test_the_app_lists_it(self):
        d = self.call('get', 'slating/', user=self.manager).json()
        self.assertNotIn(self.period.id, [p['id'] for p in d['periods']])
        self.assertEqual(d['archived'], [{'id': self.period.id, 'name': self.period.name,
                                          'term': self.period.academic_term, 'is_chair': True,
                                          'is_committee': True, 'notes_remaining': 1}])

    def test_the_app_hides_it_from_an_ordinary_member(self):
        self.assertEqual(self.call('get', 'slating/', user=member('ap-plain2')).json()['archived'], [])
