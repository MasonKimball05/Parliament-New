"""
Destroy notes erases the committee's whole record of an election (11-22-26).

It erased only the interviews' notes, strengths and concerns. It now also
erases the interviews' recommendation levels and recommended positions, the
verdicts on applications (back to "submitted"), review notes and reviewer,
GPA screenshots, levels and checks, and slates nobody voted on; only once the
results are published. Election setup offers it too, not just Interviews.

    python manage.py test src.tests.slating.test_destroy_committee_records
"""
from django.urls import reverse
from django.utils import timezone

from src.models import (Slate, SlatingActivity, SlatingApplication, SlatingInterview, SlatingPeriod, SlatingVote)
from src.tests.slating.test_slating_review_fixes import _Base, member
from src.view.slating.interview_manager import DestroyRefused, committee_records_left, destroy_period_notes


class DestroyCommitteeRecordsTests(_Base):

    def setUp(self):
        super().setUp()
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='results_published')
        self.period.refresh_from_db()
        SlatingApplication.objects.filter(pk=self.app.pk).update(
            status='slated', review_notes='Strong candidate', reviewer=self.manager,
            gpa_verified=True, gpa_level=2, reported_gpa='3.100', gpa_screenshot='slating/gpa/sim.png')
        self.withdrawn = SlatingApplication.objects.create(
            period=self.period, applicant=member('dc-w'), status='withdrawn', review_notes='Left early')
        self.interview = SlatingInterview.objects.create(
            application=self.app, completed_at=timezone.now(), notes='CONFIDENTIAL', strengths='s',
            concerns='c', recommendation='strongly_recommend')
        self.interview.recommended_positions.add(self.pres)
        self.draft = Slate.objects.create(period=self.period, name='Draft', slate_type='draft')
        self.voted_alt = Slate.objects.create(period=self.period, name='Alt', slate_type='alternative')
        SlatingVote.objects.create(period=self.period, slate=self.voted_alt, vote_choice='reject')

    def test_everything_the_committee_wrote_is_erased(self):
        self.assertEqual(committee_records_left(self.period),
                         {'interviews': 1, 'applications': 2, 'slates': 1, 'any': True, 'allowed': True})
        self.assertEqual(destroy_period_notes(self.period, self.manager), 1)

        i = SlatingInterview.objects.get(pk=self.interview.pk)
        self.assertTrue(i.notes_destroyed)
        self.assertEqual((i.strengths, i.concerns, i.recommendation), ('', '', ''))
        self.assertNotIn('CONFIDENTIAL', i.notes)
        self.assertFalse(i.recommended_positions.exists())
        self.assertIsNotNone(i.completed_at)

        a = SlatingApplication.objects.get(pk=self.app.pk)
        self.assertEqual((a.status, a.review_notes, a.reviewer, a.gpa_verified, a.gpa_level),
                         ('submitted', '', None, False, None))
        self.assertFalse(a.gpa_screenshot)
        self.assertEqual(str(a.reported_gpa), '3.100')   # what the applicant typed stays
        w = SlatingApplication.objects.get(pk=self.withdrawn.pk)
        self.assertEqual((w.status, w.review_notes), ('withdrawn', ''))

        slates = set(Slate.objects.filter(period=self.period).values_list('pk', flat=True))
        self.assertEqual(slates, {self.slate.pk, self.voted_alt.pk})   # voted-on slates stay
        self.assertTrue(SlatingVote.objects.filter(slate=self.voted_alt).exists())

        self.assertEqual(committee_records_left(self.period)['any'], False)
        self.assertTrue(SlatingActivity.objects.filter(
            action='notes_destroyed', metadata={'count': 1, 'applications': 2, 'slates': 1}).exists())

    def test_already_destroyed_notes_lose_their_recommendation_too(self):
        SlatingInterview.objects.filter(pk=self.interview.pk).update(
            notes_destroyed=True, notes='[DESTROYED - Slate Approved]', strengths='', concerns='')
        destroy_period_notes(self.period, self.manager)
        i = SlatingInterview.objects.get(pk=self.interview.pk)
        self.assertEqual(i.recommendation, '')
        self.assertFalse(i.recommended_positions.exists())

    def test_refused_before_the_results_are_published(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_closed')
        self.period.refresh_from_db()
        self.assertFalse(committee_records_left(self.period)['allowed'])
        with self.assertRaises(DestroyRefused):
            destroy_period_notes(self.period, self.manager)
        self.assertEqual(SlatingApplication.objects.get(pk=self.app.pk).status, 'slated')

    def test_setup_page_has_the_button_once_results_are_published(self):
        self.client.force_login(self.manager)
        setup = reverse('slating_period_setup', args=[self.period.id])
        r = self.client.get(setup)
        self.assertContains(r, 'data-destroy-records')
        self.assertContains(r, 'name="return_to" value="setup"')
        r = self.client.post(reverse('slating_destroy_notes', args=[self.period.id]),
                             {'confirm': 'DESTROY', 'return_to': 'setup'})
        self.assertRedirects(r, setup, fetch_redirect_response=False)
        self.assertEqual(SlatingApplication.objects.get(pk=self.app.pk).status, 'submitted')
        self.assertContains(self.client.get(setup), 'Nothing left to destroy.')
        self.assertEqual(self.client.get(reverse('slating_results', args=[self.period.id])).status_code, 200)

    def test_setup_page_has_no_button_mid_election(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='deliberation')
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse('slating_period_setup', args=[self.period.id])),
                               'data-destroy-records')
