"""
Points earned so far, out of the max currently possible, on the education
dashboard's Pledge Progress card — 09-22-26, Mason's request: "next to the
pledge tasks completed add something to show how many points out of maximum
points they can have gotten so far based on what events/tasks are active
for them."

THE RULE
--------
"So far" means only what a pledge could actually have earned by now:

* A task counts toward BOTH max and earned only if it is `is_live` (not a
  draft, not a timed task whose activation date hasn't arrived). A task
  that isn't live yet cannot have contributed a point either way.
* A meeting counts only if it has already happened (`event.date_time` in
  the past). A future meeting's points aren't earnable yet.
* Earned uses the exact same rules already on the pledge's own page
  (`my_pledge_tasks`, src/view/pledge_tasks.py): task status must be
  'completed' (not 'waived' — waived means excused, not earned), and
  attendance status must be in `EducationMeetingAttendance.EARNS_POINTS`
  ('present'/'late').
* A waived or incomplete LIVE task still counts toward max — matches how
  `pledge_summaries['total']` already counts a waived required task in its
  own denominator on this same page.
* A task assigned to specific pledges only counts for those pledges.

Run with: python manage.py test src.tests.education.test_education_dashboard_points
"""
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Event, EducationMeeting, EducationMeetingAttendance,
    PledgeTask, PledgeTaskCompletion,
)
from src.tests.education._fixtures import EducationFixtureMixin, make_user


def make_meeting(committee, chair, when, points=0, title='Meeting'):
    event = Event.objects.create(
        title=title, description='', date_time=when, created_by=chair,
    )
    return EducationMeeting.objects.create(
        event=event, committee=committee, points=points, created_by=chair,
    )


class PointsSoFarTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def _get(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        summaries = {
            ps['pledge'].pk: ps for ps in response.context['pledge_summaries']
        }
        return summaries[self.pledge.pk]

    def test_no_points_anywhere_yields_none_percent_and_zero_max(self):
        ps = self._get()
        self.assertEqual(ps['max_points'], 0)
        self.assertEqual(ps['points'], 0)
        self.assertIsNone(ps['points_percent'])

    def test_a_live_completed_task_is_earned(self):
        task = PledgeTask.objects.create(title='Do a thing', points=10)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        ps = self._get()
        self.assertEqual(ps['max_points'], 10)
        self.assertEqual(ps['points'], 10)
        self.assertEqual(ps['points_percent'], 100)

    def test_a_live_uncompleted_task_counts_toward_max_only(self):
        PledgeTask.objects.create(title='Not done yet', points=10)
        ps = self._get()
        self.assertEqual(ps['max_points'], 10)
        self.assertEqual(ps['points'], 0)
        self.assertEqual(ps['points_percent'], 0)

    def test_a_waived_task_counts_toward_max_but_not_earned(self):
        task = PledgeTask.objects.create(title='Excused', points=10)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='waived')
        ps = self._get()
        self.assertEqual(ps['max_points'], 10)
        self.assertEqual(ps['points'], 0)

    def test_a_draft_manual_task_is_excluded_entirely(self):
        """
        Not live — a chair hasn't published it, so a pledge could not
        possibly have earned it, and it shouldn't count against the max
        either. Matches `pledge_may_see_task`'s notion of live.
        """
        task = PledgeTask.objects.create(
            title='Draft', points=10, activation_mode='manual', is_published=False,
        )
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        ps = self._get()
        self.assertEqual(ps['max_points'], 0)
        self.assertEqual(ps['points'], 0)

    def test_a_not_yet_timed_task_is_excluded(self):
        task = PledgeTask.objects.create(
            title='Future task', points=10, activation_mode='timed',
            activates_at=timezone.now() + timezone.timedelta(days=7),
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 0)

    def test_a_task_assigned_to_someone_else_only_does_not_count_for_this_pledge(self):
        task = PledgeTask.objects.create(title='Just for the other one', points=10)
        task.assigned_to.add(self.other_pledge)
        ps = self._get()
        self.assertEqual(ps['max_points'], 0)

        other_ps = None
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        for row in response.context['pledge_summaries']:
            if row['pledge'].pk == self.other_pledge.pk:
                other_ps = row
        self.assertEqual(other_ps['max_points'], 10)

    def test_a_zero_point_task_does_not_inflate_max(self):
        task = PledgeTask.objects.create(title='Worth nothing', points=0)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        ps = self._get()
        self.assertEqual(ps['max_points'], 0)

    def test_a_past_meeting_attended_is_earned(self):
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='present', marked_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 5)
        self.assertEqual(ps['points'], 5)

    def test_late_also_earns_meeting_points(self):
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='late', marked_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], 5)

    def test_a_past_meeting_missed_counts_toward_max_only(self):
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='absent', marked_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 5)
        self.assertEqual(ps['points'], 0)

    def test_a_past_meeting_with_no_attendance_row_at_all_counts_toward_max_only(self):
        """The common case: nobody has taken attendance for this meeting yet."""
        make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 5)
        self.assertEqual(ps['points'], 0)

    def test_a_future_meeting_is_excluded_entirely(self):
        """
        Even if somehow marked present in advance — a future meeting's
        points cannot have been earned "so far".
        """
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() + timezone.timedelta(days=3), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='present', marked_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 0)
        self.assertEqual(ps['points'], 0)

    def test_excused_earns_nothing_but_still_counts_toward_max(self):
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='excused', marked_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 5)
        self.assertEqual(ps['points'], 0)

    def test_tasks_and_meetings_combine(self):
        task = PledgeTask.objects.create(title='Do a thing', points=10)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='present', marked_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['max_points'], 15)
        self.assertEqual(ps['points'], 15)
        self.assertEqual(ps['points_percent'], 100)

    def test_matches_the_pledges_own_my_tasks_total_points(self):
        """
        The earned side of this must agree with what the pledge sees on
        their own page — same source of truth, no separate arithmetic that
        could drift from it.
        """
        task = PledgeTask.objects.create(title='Shared math', points=10)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        meeting = make_meeting(
            self.committee, self.chair, timezone.now() - timezone.timedelta(days=1), points=5,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='present', marked_by=self.chair,
        )

        dashboard_ps = self._get()

        pledge_client = Client()
        pledge_client.force_login(self.pledge)
        my_tasks_response = pledge_client.get(reverse('my_pledge_tasks'))
        self.assertEqual(my_tasks_response.status_code, 200)

        self.assertEqual(dashboard_ps['points'], my_tasks_response.context['total_points'])


class PointsSoFarQueryScalingTests(EducationFixtureMixin, TestCase):
    """
    No query-per-pledge or query-per-meeting for the points computation.
    Doesn't assert a hard ceiling (that needs a real measurement run, which
    this module doesn't have); asserts the constant-free property instead —
    adding more pledges and more past meetings must not add more queries.
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Common task', points=10)
        for i in range(3):
            make_meeting(
                self.committee, self.chair,
                timezone.now() - timezone.timedelta(days=i + 1), points=5,
                title=f'Past meeting {i}',
            )

    def _query_count(self):
        client = Client()
        client.force_login(self.chair)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)

    def test_more_pledges_and_meetings_does_not_scale_query_count(self):
        baseline = self._query_count()

        for i in range(10):
            pledge = make_user(f'P-EXTRA{i:03d}', f'Extra Pledge {i}', member_type='Pledge')
            PledgeTaskCompletion.objects.create(
                task=self.task, pledge=pledge,
                status='completed' if i % 2 else 'pending',
            )
        for i in range(5):
            make_meeting(
                self.committee, self.chair,
                timezone.now() - timezone.timedelta(days=i + 10), points=5,
                title=f'More past meeting {i}',
            )

        scaled = self._query_count()

        self.assertLessEqual(
            scaled, baseline + 5,
            f'query count scaled with data volume ({baseline} -> {scaled}) — '
            f'looks like an N+1 was introduced in the points computation',
        )
