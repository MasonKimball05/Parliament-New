"""
11-14-26: an applicant can't tell from his own status where the committee
stands before voting opens. Placing him on the slate builder set "Slated"
(even on a draft) and reviewers set Recommended / Not Recommended, all shown
on My Applications. Until voting opens they read "Interviewed" (or
"Submitted" before his interview); the committee still sees the real one.
"""
from django.urls import reverse
from django.utils import timezone

from src.models import SlatingApplication, SlatingInterview, SlatingPeriod
from src.tests.mobile.test_slating_batch5 import _AppBase
from src.view.slating.apply import status_for_applicant


class ApplicantStatusTests(_AppBase):

    def setUp(self):
        super().setUp()
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='deliberation')
        self.period.refresh_from_db()

    def set(self, status, interviewed=True):
        SlatingApplication.objects.filter(pk=self.app.pk).update(status=status)
        SlatingInterview.objects.filter(application=self.app).delete()
        if interviewed:
            SlatingInterview.objects.create(application=self.app, completed_at=timezone.now())
        self.app.refresh_from_db()

    def test_decisions_are_hidden_until_voting_opens(self):
        for status in ('slated', 'recommended', 'not_recommended'):
            self.set(status)
            self.assertEqual(status_for_applicant(self.app), ('interviewed', 'Interviewed'))
            self.set(status, interviewed=False)
            self.assertEqual(status_for_applicant(self.app), ('submitted', 'Submitted'))
        self.set('under_review', interviewed=False)
        self.assertEqual(status_for_applicant(self.app)[0], 'submitted')
        for status in ('draft', 'interview_scheduled', 'interviewed', 'withdrawn'):
            self.set(status)
            self.assertEqual(status_for_applicant(self.app)[0], status)

    def test_real_status_once_voting_opens(self):
        self.set('slated')
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_open')
        self.app.refresh_from_db()
        self.assertEqual(status_for_applicant(self.app), ('slated', 'Slated'))

    def test_web_pages_show_the_neutral_status(self):
        self.set('slated')
        self.client.force_login(self.voter)
        for url in (reverse('slating_my_applications'), reverse('slating_dashboard')):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            html = r.content.decode()
            self.assertNotIn('Slated', html.replace('Slating', ''), url)
            self.assertIn('Interviewed', html, url)

    def test_committee_still_sees_the_real_status(self):
        self.set('slated')
        self.client.force_login(self.committee_member)
        r = self.client.get(reverse('slating_app_detail', args=[self.period.id, self.app.id]))
        self.assertContains(r, 'Slated')

    def test_app_endpoints(self):
        self.set('slated')
        hub = self.call('get', 'slating/', user=self.voter).json()
        mine = next(a for a in hub['applications'] if a['id'] == self.app.id)
        self.assertEqual((mine['status'], mine['status_label']), ('interviewed', 'Interviewed'))
        self.assertFalse(mine['can_withdraw'])
        self.set('submitted', interviewed=False)
        hub = self.call('get', 'slating/', user=self.voter).json()
        mine = next(a for a in hub['applications'] if a['id'] == self.app.id)
        self.assertFalse(mine['can_withdraw'])          # deliberation: no one gets the button
        self.set('slated')
        apps = self.call('get', 'slating/my-applications/', user=self.voter).json()['applications']
        self.assertEqual(next(a for a in apps if a['id'] == self.app.id)['status_label'], 'Interviewed')
