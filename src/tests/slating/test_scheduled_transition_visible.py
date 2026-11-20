"""
A scheduled officer transition can be seen and edited after it's saved (11-20-26).

Once scheduled from slating it showed nowhere, and reopening the slating form
defaulted to "Execute immediately", so re-saving an edit ran it at once.
Role Transitions now lists it (Edit and Cancel for site admins), and the form
opens on the saved time.

    python manage.py test src.tests.slating.test_scheduled_transition_visible
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from src.models import Role, SlatingPeriod
from src.tests.slating.test_saturday_fixes import _Fix
from src.tests.slating.test_slating_review_fixes import member


class ScheduledTransitionTests(_Fix):

    def setUp(self):
        super().setUp()
        self.role = Role.objects.create(name='Sim Treasurer', code='SIMT')
        self.pres.role = self.role
        self.pres.save()
        self.holder = member('st-holder')
        self.holder.roles.add(self.role)
        self.newbie = member('st-new')
        self.at = (timezone.localtime() + timedelta(days=7)).replace(hour=18, minute=30, second=0, microsecond=0)
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            status='results_published', officer_transition_at=self.at,
            officer_transition_data={str(self.pres.id): self.newbie.user_id})
        self.url = reverse('slating_transition', args=[self.period.id])
        self.client.force_login(self.admin)

    def test_role_transitions_lists_it_for_a_site_admin(self):
        r = self.client.get(reverse('role_transitions'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'data-scheduled-transition="{self.period.id}"')
        self.assertContains(r, f'Scheduled transition: {self.period.name}')
        self.assertContains(r, 'Sim Treasurer')
        self.assertContains(r, self.newbie.name)
        self.assertContains(r, f'replaces {self.holder.name}')
        self.assertContains(r, f'href="{self.url}"')
        self.assertContains(r, 'name="return_to" value="role_transitions"')

    def test_an_officer_sees_it_read_only(self):
        officer = member('st-officer', member_type='Officer')
        self.client.force_login(officer)
        r = self.client.get(reverse('role_transitions'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'Scheduled transition: {self.period.name}')
        self.assertNotContains(r, 'name="return_to" value="role_transitions"')
        self.assertContains(r, 'Only a site admin can change or cancel it.')

    def test_done_or_cancelled_transitions_are_not_listed(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(officer_transition_completed=True)
        self.assertNotContains(self.client.get(reverse('role_transitions')), 'data-scheduled-transition=')
        SlatingPeriod.objects.filter(pk=self.period.pk).update(officer_transition_completed=False,
                                                                officer_transition_data={})
        self.assertNotContains(self.client.get(reverse('role_transitions')), 'data-scheduled-transition=')

    def test_cancel_from_role_transitions_goes_back_there(self):
        r = self.client.post(self.url, {'action': 'cancel_scheduled', 'return_to': 'role_transitions'})
        self.assertRedirects(r, reverse('role_transitions'), fetch_redirect_response=False)
        p = self.fresh()
        self.assertIsNone(p.officer_transition_at)
        self.assertEqual(p.officer_transition_data, {})

    def test_the_form_opens_on_the_saved_schedule(self):
        r = self.client.get(self.url)
        self.assertContains(r, f'value="{self.at:%Y-%m-%d}"')
        self.assertContains(r, 'value="18:30"')
        self.assertContains(r, 'id="timing_scheduled" value="scheduled" checked')
        self.assertNotContains(r, 'id="timing_immediate" value="immediate" checked')

    def test_resaving_an_edit_keeps_it_scheduled(self):
        other = member('st-other')
        r = self.client.post(self.url, {
            'action': 'confirm', 'reviewed': '1', 'timing': 'scheduled',
            f'enable_{self.pres.id}': 'on', f'member_{self.pres.id}': other.user_id,
            'effective_date': f'{self.at:%Y-%m-%d}', 'effective_time': '18:30'})
        self.assertEqual(r.status_code, 302)
        p = self.fresh()
        self.assertFalse(p.officer_transition_completed)
        self.assertEqual(timezone.localtime(p.officer_transition_at), self.at)
        self.assertEqual(p.officer_transition_data, {str(self.pres.id): other.user_id})
        self.assertFalse(other.roles.filter(pk=self.role.pk).exists())

    def test_execute_immediately_ignores_the_prefilled_date(self):
        r = self.client.post(self.url, {
            'action': 'confirm', 'reviewed': '1', 'timing': 'immediate',
            f'enable_{self.pres.id}': 'on', f'member_{self.pres.id}': self.newbie.user_id,
            'effective_date': f'{self.at:%Y-%m-%d}', 'effective_time': '18:30'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.fresh().officer_transition_completed)
        self.assertTrue(self.newbie.roles.filter(pk=self.role.pk).exists())

    # ── The app's Role Transitions (officer/transitions/) ─────────────────────

    def test_app_lists_it_with_edit_for_a_site_admin(self):
        d = self.api(self.admin, 'get', 'officer/transitions/').json()
        self.assertEqual(len(d['scheduled']), 1)
        s = d['scheduled'][0]
        self.assertEqual((s['period_id'], s['period_name'], s['can_edit']), (self.period.id, self.period.name, True))
        self.assertEqual(timezone.localtime(timezone.datetime.fromisoformat(s['scheduled_at'])), self.at)
        self.assertEqual(s['changes'], [{'position': 'President', 'role': 'Sim Treasurer', 'new': self.newbie.name,
                                         'removed': [self.holder.name], 'already': False}])

    def test_app_officer_sees_it_without_edit(self):
        officer = member('st-officer2', member_type='Officer')
        d = self.api(officer, 'get', 'officer/transitions/').json()
        self.assertEqual([s['can_edit'] for s in d['scheduled']], [False])

    def test_app_list_is_empty_once_it_has_run(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(officer_transition_completed=True)
        self.assertEqual(self.api(self.admin, 'get', 'officer/transitions/').json()['scheduled'], [])
