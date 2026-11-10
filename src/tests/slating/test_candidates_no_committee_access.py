"""
11-10-26: a candidate gets no committee access to their own election, however
they got the role. The setup pages already refused to seat an applicant on
the committee, but the general committee "Add member" page doesn't check,
and a site admin on an unlocked period always got in. Either way they could
read every applicant's interview notes, their own included.
"""
from django.urls import reverse
from django.utils import timezone

from src.models import SlatingApplication, SlatingInterview, SlatingPeriod, SlatingPosition
from src.tests.mobile.test_slating_batch5 import _AppBase
from src.tests.slating.test_slating_review_fixes import member
from src.view.slating.permissions import can_manage_period, can_view_applications


class CandidateAccessTests(_AppBase):

    def setUp(self):
        super().setUp()
        SlatingInterview.objects.create(application=self.app, completed_at=timezone.now(), notes='CONFIDENTIAL')
        self.detail = reverse('slating_app_detail', args=[self.period.id, self.app.id])

    def read(self, user):
        self.client.force_login(user)
        return self.client.get(self.detail)

    def test_committee_member_who_applied_is_shut_out(self):
        # Seated through the general committee page, which never checked.
        self.committee.members.add(self.voter)
        self.assertNotContains(self.read(self.voter), 'CONFIDENTIAL', status_code=302)
        self.assertFalse(can_view_applications(self.voter, self.period))
        r = self.call('get', f'slating/periods/{self.period.id}/applications/{self.app.id}/', user=self.voter)
        self.assertEqual(r.status_code, 403)
        # Every other committee member still reads it.
        self.assertContains(self.read(self.committee_member), 'CONFIDENTIAL')

    def test_site_admin_who_applied_to_an_unlocked_period(self):
        admin = member('sr-admin2', is_admin=True)
        period = SlatingPeriod.objects.create(name='Unlocked', status='deliberation', required_approval_percentage=66)
        SlatingPosition.objects.create(period=period, title='President', code='P')
        app = SlatingApplication.objects.create(period=period, applicant=admin, status='interviewed')
        SlatingInterview.objects.create(application=app, completed_at=timezone.now(), notes='OWN NOTES')
        self.client.force_login(admin)
        r = self.client.get(reverse('slating_app_detail', args=[period.id, app.id]))
        self.assertNotContains(r, 'OWN NOTES', status_code=302)
        self.assertFalse(can_manage_period(admin, period))
        # Without an application the same admin still steps in.
        app.delete()
        fresh = member('sr-admin3', is_admin=True)
        self.assertTrue(can_view_applications(fresh, period))

    def test_withdrawing_does_not_open_the_notes(self):
        self.committee.members.add(self.voter)
        SlatingApplication.objects.filter(pk=self.app.pk).update(status='withdrawn')
        self.assertNotContains(self.read(self.voter), 'CONFIDENTIAL', status_code=302)
