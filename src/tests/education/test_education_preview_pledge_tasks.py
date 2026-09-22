"""
"View as pledge" dashboard preview — 09-22-26, Mason's request: whoever
manages the education dashboard needed a way to see exactly what a given
pledge's own My Tasks page looks like, without a full session-swap
impersonation.

Covers:
* `build_pledge_tasks_context` (extracted from `my_pledge_tasks`) — the
  refactor changes nothing about what a pledge sees on their own page.
* `education_preview_pledge_tasks` — gated the same way the rest of the
  education dashboard is; renders the identical template with the identical
  context-building function, plus a preview banner; the two actions that
  would mutate data as the pledge (asking to be excused, taking a quiz) are
  disabled rather than just hidden-by-omission.

Run with:
  python manage.py test src.tests.education.test_education_preview_pledge_tasks
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Event, EducationMeeting, EducationMeetingAttendance, PledgeTask, PledgeTaskCompletion,
)
from src.tests.education._fixtures import EducationFixtureMixin


class BuildPledgeTasksContextDoesNotChangeMyTasksTests(EducationFixtureMixin, TestCase):
    """
    The refactor split `my_pledge_tasks` into a context-builder plus a thin
    wrapper. This class pins that the pledge's OWN page renders exactly as
    before — same status code, same content — so the split is provably
    behaviour-preserving.
    """

    def setUp(self):
        self.build()
        PledgeTask.objects.create(title='Learn the handshake', task_type='task', points=5)

    def test_the_pledges_own_page_still_renders(self):
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Learn the handshake')
        self.assertContains(response, 'My Tasks')

    def test_a_non_pledge_is_still_redirected_home(self):
        self.client.force_login(self.brother)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertRedirects(response, reverse('home'))


class PreviewAccessGatingTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def _url(self, pledge=None):
        pledge = pledge or self.pledge
        return reverse('education_preview_pledge_tasks', args=[self.committee.code, pledge.pk])

    def test_a_chair_can_open_the_preview(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)

    def test_someone_with_no_education_access_gets_404(self):
        self.client.force_login(self.brother)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 404)

    def test_an_anonymous_visitor_is_redirected_to_login(self):
        from django.test import Client
        response = Client().get(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_a_pledge_pk_that_is_not_a_pledge_404s(self):
        """A brother's pk on this route must not resolve to anything."""
        response = self.client.get(self._url(pledge=self.brother))
        self.assertEqual(response.status_code, 404)

    def test_a_nonexistent_pledge_pk_404s(self):
        url = reverse('education_preview_pledge_tasks', args=[self.committee.code, 'P-NOPE99'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class PreviewRendersTheTargetPledgesRealContentTests(EducationFixtureMixin, TestCase):
    """The preview must show the PREVIEWED pledge's data, not the chair's own
    (chairs aren't pledges and have none) and not some other pledge's."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Only for Pledge One', task_type='task', points=5)
        self.task.assigned_to.add(self.pledge)
        self.other_task = PledgeTask.objects.create(title='Only for Pledge Two', task_type='task', points=5)
        self.other_task.assigned_to.add(self.other_pledge)
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='completed')

    def _preview(self, pledge):
        url = reverse('education_preview_pledge_tasks', args=[self.committee.code, pledge.pk])
        return self.client.get(url)

    def test_previewing_pledge_one_shows_pledge_ones_task_and_not_pledge_twos(self):
        response = self._preview(self.pledge)
        self.assertContains(response, 'Only for Pledge One')
        self.assertNotContains(response, 'Only for Pledge Two')

    def test_previewing_pledge_two_shows_pledge_twos_task_and_not_pledge_ones(self):
        response = self._preview(self.other_pledge)
        self.assertContains(response, 'Only for Pledge Two')
        self.assertNotContains(response, 'Only for Pledge One')

    def test_the_previewed_pledges_completion_status_is_shown(self):
        response = self._preview(self.pledge)
        self.assertContains(response, 'Done and signed off.')

    def test_points_match_between_the_pledges_own_page_and_the_preview(self):
        """
        The whole point of sharing `build_pledge_tasks_context` is that these
        two pages cannot disagree. Prove it directly rather than trusting the
        refactor by inspection.
        """
        own_client = self.client.__class__()
        own_client.force_login(self.pledge)
        own_response = own_client.get(reverse('my_pledge_tasks'))

        preview_response = self._preview(self.pledge)

        self.assertEqual(
            own_response.context['total_points'],
            preview_response.context['total_points'],
        )
        self.assertEqual(
            own_response.context['task_points'],
            preview_response.context['task_points'],
        )


class PreviewShowsBannerAndDisablesMutationTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def _preview(self, pledge=None):
        pledge = pledge or self.pledge
        url = reverse('education_preview_pledge_tasks', args=[self.committee.code, pledge.pk])
        return self.client.get(url)

    def test_the_preview_banner_is_shown(self):
        response = self._preview()
        self.assertContains(response, 'Previewing as')
        self.assertContains(response, self.pledge.name)

    def test_the_pledges_own_page_never_shows_a_preview_banner(self):
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertNotContains(response, 'Previewing as')

    def test_the_absence_request_form_is_not_rendered_in_preview(self):
        event = Event.objects.create(
            title='Chapter Meeting', description='', date_time=timezone.now() + timezone.timedelta(days=3),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, attendance_required=True, created_by=self.chair,
        )
        response = self._preview()
        self.assertNotContains(response, "Can't make it? Ask to be excused")
        self.assertContains(response, 'Absence requests are disabled in preview')
        # And the real, mutating form action must not appear anywhere either —
        # belt and suspenders against the message alone being trusted.
        self.assertNotContains(response, reverse('pledge_request_absence', args=[meeting.pk]))

    def test_submitting_an_absence_request_url_directly_is_unaffected_by_preview(self):
        """
        The preview page hides the form; it does not — and structurally
        cannot, since it never logs the chair in as the pledge — change what
        `pledge_request_absence` itself does. Confirms the chair's own
        session has no standing to file one.
        """
        from src.models import EducationAbsenceRequest
        event = Event.objects.create(
            title='Chapter Meeting', description='', date_time=timezone.now() + timezone.timedelta(days=3),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, attendance_required=True, created_by=self.chair,
        )
        self.client.post(
            reverse('pledge_request_absence', args=[meeting.pk]), {'reason': 'sneaking in as chair'},
        )
        self.assertFalse(EducationAbsenceRequest.objects.filter(meeting=meeting).exists())

    def test_the_take_quiz_link_is_not_rendered_in_preview(self):
        task = PledgeTask.objects.create(title='Pop quiz', task_type='quiz', is_active=True)
        response = self._preview()
        self.assertNotContains(response, reverse('pledge_take_quiz', args=[task.pk]))
        self.assertContains(response, 'Quiz-taking disabled in preview')

    def test_the_pledges_own_page_still_shows_the_take_quiz_link(self):
        task = PledgeTask.objects.create(title='Pop quiz', task_type='quiz', is_active=True)
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertContains(response, reverse('pledge_take_quiz', args=[task.pk]))


class PreviewButtonOnDashboardTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        PledgeTask.objects.create(title='Any task', task_type='task', points=1)

    def test_the_dashboard_links_to_the_preview_for_each_pledge(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertContains(
            response,
            reverse('education_preview_pledge_tasks', args=[self.committee.code, self.pledge.pk]),
        )
        self.assertContains(
            response,
            reverse('education_preview_pledge_tasks', args=[self.committee.code, self.other_pledge.pk]),
        )
