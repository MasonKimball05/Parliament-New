"""
v3.20.0 — pledge task scoring, education meetings, and the grading bug they
sat next to.

Three subjects, and they are here together because they are one change to how
the education committee records what a pledge did:

1. **Scoring.** A task can carry a `max_score`; a chair records what each pledge
   earned. Informational — it does not decide pass/fail.
2. **The `set_status` bug.** `quiz_submissions.html` has posted a `set_status`
   hidden field since it was written and `education_toggle_completion` never
   read it, so on the grading page *Mark Incomplete* on a pending pledge marked
   him **passed**.
3. **Meetings.** An `Event` sidecar with pledge-only attendance.

⚠️ THE ONE TO READ IS `EducationAttendanceIsPledgeOnlyTests`. The requirement is
that education attendance never touches chapter attendance, and the way this
codebase has repeatedly got that wrong is by stating a rule correctly and then
leaving one call site outside it. So that class asserts the *structural*
property — the chapter `Attendance` table is untouched no matter what the
education views do — rather than checking the two or three views that exist
today.
"""

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Attendance, Committee, EducationAbsenceRequest, EducationMeeting,
    EducationMeetingAttendance, Event, ParliamentUser, PledgeTask,
    PledgeTaskCompletion, PledgeTaskQuestion, PledgeQuizAnswer,
)


def make_user(uid, name='Test User', member_type='Member', **kwargs):
    user = ParliamentUser.objects.create(
        user_id=uid, username=uid, name=name,
        member_type=member_type, member_status='Active', **kwargs
    )
    user.set_password('education-test-pass-12345!')
    user.save()
    return user


class EducationFixtureMixin:
    def build(self):
        self.committee = Committee.objects.create(
            name='Education', code='EDUCATION',
            is_active=True, is_education_committee=True,
        )
        # ⚠️ PLEDGE IDS ARE NOT NUMERIC, AND ASSUMING THEY WERE SHIPPED A 500.
        #
        # `ParliamentUser.user_id` is a CharField primary key. Initiated
        # brothers carry a roll number, but a PLEDGE carries something like
        # `P-C7JKZY` until initiation (CLAUDE.md's "pledge initiation user ID"
        # note is about exactly that migration).
        #
        # The first version of this fixture used numeric ids and carried a
        # comment claiming they were realistic. They are not, and the education
        # URLs declared `<int:pledge_pk>` — so `education_pledge_detail`
        # raised `NoReverseMatch` on the real dashboard the moment a real
        # pledge existed, while every test here passed. The completion-grid
        # toggle had the same defect and had presumably never worked for a real
        # pledge either; it built its URL in JavaScript, so it 404'd quietly
        # instead of raising.
        #
        # **A fixture that is easier than production is a fixture that tests
        # something else.** These ids are now shaped like the real thing, which
        # is what makes the `<str:>` routes load-bearing here.
        self.chair = make_user('9001', 'Edu Chair', member_type='Officer')
        self.committee.chairs.add(self.chair)
        self.pledge = make_user('P-C7JKZY', 'Pledge One', member_type='Pledge')
        self.other_pledge = make_user('P-9QW2LM', 'Pledge Two', member_type='Pledge')
        self.brother = make_user('9004', 'A Brother')

        self.client = Client()
        self.client.force_login(self.chair)


class TaskScoringTests(EducationFixtureMixin, TestCase):
    """`max_score` on the task, `score` on the completion."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Ritual Exam', max_score=60)

    def test_a_task_without_a_max_score_is_not_scored(self):
        self.assertFalse(PledgeTask.objects.create(title='Plain').is_scored)

    def test_a_task_with_a_max_score_is_scored(self):
        self.assertTrue(self.task.is_scored)

    def test_score_display_and_percent(self):
        comp = PledgeTaskCompletion.objects.create(
            task=self.task, pledge=self.pledge, status='completed', score=50,
        )
        self.assertTrue(comp.has_score)
        self.assertEqual(comp.score_display, '50/60')
        self.assertEqual(comp.score_percent, 83)

    def test_a_zero_score_is_a_real_score(self):
        """
        ⚠️ `0` is falsy. A `has_score` written as `if self.score` would report
        a pledge who scored nothing as ungraded — which reads on his page as
        "not yet marked" rather than "you got none of them right".
        """
        comp = PledgeTaskCompletion.objects.create(
            task=self.task, pledge=self.pledge, status='incomplete', score=0,
        )
        self.assertTrue(comp.has_score)
        self.assertEqual(comp.score_display, '0/60')
        self.assertEqual(comp.score_percent, 0)

    def test_an_unscored_task_shows_no_score_even_if_one_was_stored(self):
        """Clearing a task's max_score must not leave orphaned marks rendering."""
        plain = PledgeTask.objects.create(title='Plain')
        comp = PledgeTaskCompletion.objects.create(
            task=plain, pledge=self.pledge, status='completed', score=5,
        )
        self.assertFalse(comp.has_score)
        self.assertEqual(comp.score_display, '')
        self.assertIsNone(comp.score_percent)

    def test_score_percent_does_not_divide_by_zero(self):
        """A max_score of 0 should be impossible via the form, but not crash here."""
        zero = PledgeTask.objects.create(title='Zero', max_score=0)
        comp = PledgeTaskCompletion.objects.create(
            task=zero, pledge=self.pledge, status='completed', score=0,
        )
        self.assertIsNone(comp.score_percent)


class RecordingAScoreTests(EducationFixtureMixin, TestCase):
    """The grading endpoint."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Ritual Exam', max_score=60)
        self.url = reverse(
            'education_toggle_completion',
            args=[self.committee.code, self.task.pk, self.pledge.pk],
        )

    def _completion(self):
        return PledgeTaskCompletion.objects.get(task=self.task, pledge=self.pledge)

    def test_a_chair_can_record_a_score(self):
        self.client.post(self.url, {'set_status': 'completed', 'score': '50'})
        comp = self._completion()
        self.assertEqual(comp.score, 50)
        self.assertEqual(comp.status, 'completed')

    def test_a_score_above_the_maximum_is_rejected(self):
        response = self.client.post(self.url, {'set_status': 'completed', 'score': '61'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            PledgeTaskCompletion.objects.filter(task=self.task, pledge=self.pledge, score=61).exists()
        )

    def test_a_negative_score_is_rejected(self):
        self.assertEqual(
            self.client.post(self.url, {'set_status': 'completed', 'score': '-1'}).status_code,
            400,
        )

    def test_a_non_numeric_score_is_rejected(self):
        self.assertEqual(
            self.client.post(self.url, {'set_status': 'completed', 'score': 'fifty'}).status_code,
            400,
        )

    def test_a_score_on_an_unscored_task_is_rejected(self):
        """Otherwise a mark is stored that nothing will ever render."""
        plain = PledgeTask.objects.create(title='Plain')
        url = reverse('education_toggle_completion',
                      args=[self.committee.code, plain.pk, self.pledge.pk])
        self.assertEqual(url and self.client.post(url, {'set_status': 'completed', 'score': '5'}).status_code, 400)

    def test_an_empty_score_clears_it(self):
        """How a grader undoes a typo."""
        self.client.post(self.url, {'set_status': 'completed', 'score': '50'})
        self.client.post(self.url, {'set_status': 'completed', 'score': ''})
        self.assertIsNone(self._completion().score)

    def test_omitting_the_score_field_leaves_an_existing_score_alone(self):
        """
        ⚠️ THE ONE THAT MATTERS FOR THE DASHBOARD GRID. That grid posts a status
        and no score. If an absent field were read as "clear it", one click on
        the grid would silently wipe a mark a chair had typed on the grading
        page — and nothing on screen would say so.
        """
        self.client.post(self.url, {'set_status': 'completed', 'score': '50'})
        self.client.post(self.url, {'set_status': 'incomplete'})
        comp = self._completion()
        self.assertEqual(comp.score, 50)
        self.assertEqual(comp.status, 'incomplete')


class SetStatusIsHonouredTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ REGRESSION TEST FOR A LIVE GRADING BUG (v3.20.0).

    `quiz_submissions.html` posts `set_status`; the view ignored it and cycled
    `pending → completed → incomplete → pending` instead. So on the grading
    page, *Mark Incomplete* on a pledge whose quiz was pending marked him
    **passed** — the two buttons were indistinguishable, and the failure mode
    was passing someone who had failed.
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Quiz', task_type='quiz')
        self.url = reverse(
            'education_toggle_completion',
            args=[self.committee.code, self.task.pk, self.pledge.pk],
        )

    def _status(self):
        return PledgeTaskCompletion.objects.get(task=self.task, pledge=self.pledge).status

    def test_marking_incomplete_from_pending_does_not_mark_completed(self):
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='pending')
        self.client.post(self.url, {'set_status': 'incomplete'})
        self.assertEqual(self._status(), 'incomplete')

    def test_marking_completed_is_idempotent(self):
        """Clicking Mark Passed twice must not cycle on to 'incomplete'."""
        self.client.post(self.url, {'set_status': 'completed'})
        self.client.post(self.url, {'set_status': 'completed'})
        self.assertEqual(self._status(), 'completed')

    def test_an_unknown_status_is_rejected(self):
        self.assertEqual(self.client.post(self.url, {'set_status': 'promoted'}).status_code, 400)

    def test_the_control_no_set_status_still_cycles(self):
        """
        The dashboard grid depends on cycling. Fixing the explicit path must not
        take the one-click behaviour away from the page that was working.
        """
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='pending')
        self.client.post(self.url, {})
        self.assertEqual(self._status(), 'completed')
        self.client.post(self.url, {})
        self.assertEqual(self._status(), 'incomplete')


class EducationMeetingTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.homework = PledgeTask.objects.create(title='Read chapter 3')

    def _create(self, **overrides):
        payload = {
            'title': 'Pledge Meeting',
            'date_time': (timezone.now() + timezone.timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
            'location': 'Chapter room',
            'meeting_type': 'meeting',
            'points': '5',
            'attendance_required': 'on',
            'homework': [str(self.homework.pk)],
        }
        payload.update(overrides)
        return self.client.post(
            reverse('education_add_meeting', args=[self.committee.code]), payload
        )

    def test_creating_a_meeting_creates_a_calendar_event(self):
        self._create()
        meeting = EducationMeeting.objects.get()
        self.assertEqual(meeting.event.title, 'Pledge Meeting')
        self.assertEqual(meeting.event.location, 'Chapter room')
        self.assertEqual(meeting.points, 5)

    def test_the_meeting_is_visible_to_the_whole_chapter(self):
        """
        Mason's requirement: brothers should see when the pledge class meets.
        Only *attendance* is pledge-only. `visible_to=None` means everyone.
        """
        self._create()
        self.assertIsNone(EducationMeeting.objects.get().event.visible_to)

    def test_homework_links_to_real_tasks(self):
        self._create()
        self.assertEqual(
            list(EducationMeeting.objects.get().homework.all()), [self.homework]
        )

    def test_a_meeting_without_a_valid_datetime_is_rejected(self):
        self.assertEqual(self._create(date_time='not a date').status_code, 400)
        self.assertEqual(EducationMeeting.objects.count(), 0)

    def test_a_meeting_without_a_title_is_rejected(self):
        self.assertEqual(self._create(title='').status_code, 400)

    def test_deleting_a_meeting_removes_its_calendar_entry(self):
        """
        Otherwise the chapter calendar keeps an education meeting nobody can
        take attendance for.
        """
        self._create()
        meeting = EducationMeeting.objects.get()
        self.client.post(
            reverse('education_delete_meeting', args=[self.committee.code, meeting.pk])
        )
        self.assertEqual(EducationMeeting.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)


class MeetingAttendanceTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.event = Event.objects.create(
            title='Pledge Meeting',
            description='',
            date_time=timezone.now() + timezone.timedelta(days=1),
            created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=self.event, committee=self.committee, points=5, created_by=self.chair,
        )
        self.url = reverse(
            'education_meeting_attendance', args=[self.committee.code, self.meeting.pk]
        )

    def test_the_roster_contains_only_pledges(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(
            {row['pledge'].pk for row in rows},
            {self.pledge.pk, self.other_pledge.pk},
        )

    def test_marking_attendance_records_it(self):
        self.client.post(self.url, {f'status_{self.pledge.pk}': 'present'})
        record = EducationMeetingAttendance.objects.get(meeting=self.meeting, pledge=self.pledge)
        self.assertEqual(record.status, 'present')
        self.assertEqual(record.marked_by, self.chair)
        self.assertIsNotNone(record.marked_at)

    def test_marking_is_idempotent(self):
        """Saving the form twice must not raise on the unique constraint."""
        self.client.post(self.url, {f'status_{self.pledge.pk}': 'present'})
        self.client.post(self.url, {f'status_{self.pledge.pk}': 'late'})
        self.assertEqual(
            EducationMeetingAttendance.objects.filter(meeting=self.meeting, pledge=self.pledge).count(),
            1,
        )

    def test_a_crafted_post_cannot_record_attendance_for_a_brother(self):
        """
        ⚠️ The pledge-only guarantee, at the one place a request could break it.
        The roster is built from pledges; a pk outside it is ignored rather
        than trusted.
        """
        self.client.post(self.url, {f'status_{self.brother.pk}': 'present'})
        self.assertFalse(
            EducationMeetingAttendance.objects.filter(pledge=self.brother).exists()
        )

    def test_an_unknown_status_is_ignored(self):
        self.client.post(self.url, {f'status_{self.pledge.pk}': 'vibes'})
        self.assertFalse(EducationMeetingAttendance.objects.exists())

    def test_present_and_late_earn_points_and_the_others_do_not(self):
        earned = {}
        for status in ('present', 'late', 'excused', 'absent', 'pending'):
            record = EducationMeetingAttendance(
                meeting=self.meeting, pledge=self.pledge, status=status
            )
            earned[status] = record.points_earned
        self.assertEqual(earned['present'], 5)
        self.assertEqual(earned['late'], 5)
        self.assertEqual(earned['excused'], 0)
        self.assertEqual(earned['absent'], 0)
        self.assertEqual(earned['pending'], 0)


class EducationAttendanceIsPledgeOnlyTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ THE STRUCTURAL GUARANTEE, AND THE REASON EDUCATION ATTENDANCE IS ITS OWN
    TABLE.

    The requirement is that education attendance never appears in chapter
    attendance. The tempting implementation was a third `attendance_type` on the
    shared `Attendance` model — but that makes "pledges only" a property held up
    by 49 call sites all remembering to filter, and this codebase has recorded
    seven consecutive releases of a rule stated correctly and one call site left
    outside it.

    A separate table makes it true by construction, and this test asserts the
    construction rather than the call sites: whatever the education views do,
    the chapter table stays empty.
    """

    def setUp(self):
        self.build()
        self.event = Event.objects.create(
            title='Pledge Meeting', description='',
            date_time=timezone.now(), created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=self.event, committee=self.committee, points=5, created_by=self.chair,
        )

    def test_taking_education_attendance_writes_nothing_to_chapter_attendance(self):
        self.client.post(
            reverse('education_meeting_attendance', args=[self.committee.code, self.meeting.pk]),
            {
                f'status_{self.pledge.pk}': 'present',
                f'status_{self.other_pledge.pk}': 'absent',
            },
        )
        self.assertEqual(EducationMeetingAttendance.objects.count(), 2)
        self.assertEqual(
            Attendance.objects.count(), 0,
            'Education attendance leaked into the chapter-wide Attendance table. '
            'That table feeds chapter attendance stats and the excuse system; '
            'education meetings must not appear there.',
        )

    def test_the_education_table_can_only_hold_pledges(self):
        """
        `limit_choices_to` is the admin/forms half of the guarantee. Asserted
        here so that removing it shows up as a failing test rather than as a
        brother quietly appearing on a pledge roster months later.
        """
        field = EducationMeetingAttendance._meta.get_field('pledge')
        self.assertEqual(field.get_limit_choices_to(), {'member_type': 'Pledge'})


class MyTasksPageTests(EducationFixtureMixin, TestCase):
    """The pledge-facing page."""

    def setUp(self):
        self.build()
        self.client = Client()
        self.client.force_login(self.pledge)

    def test_a_score_is_shown_to_the_pledge(self):
        task = PledgeTask.objects.create(title='Ritual Exam', max_score=60)
        PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='completed', score=50,
        )
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '50/60')

    def test_points_are_split_between_tasks_and_attendance(self):
        """
        One combined number leaves a pledge unable to tell which half he is
        behind on, so the page reports both and the total.
        """
        task = PledgeTask.objects.create(title='Task', points=3)
        PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='completed',
        )
        event = Event.objects.create(
            title='Meeting', description='',
            date_time=timezone.now() - timezone.timedelta(days=1),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5, created_by=self.chair,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='present',
        )

        context = self.client.get(reverse('my_pledge_tasks')).context
        self.assertEqual(context['task_points'], 3)
        self.assertEqual(context['attendance_points'], 5)
        self.assertEqual(context['total_points'], 8)
        self.assertEqual(context['meetings_attended'], 1)

    def test_only_this_pledges_attendance_counts_toward_his_points(self):
        event = Event.objects.create(
            title='Meeting', description='',
            date_time=timezone.now() - timezone.timedelta(days=1),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5, created_by=self.chair,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.other_pledge, status='present',
        )
        context = self.client.get(reverse('my_pledge_tasks')).context
        self.assertEqual(context['attendance_points'], 0)

    def test_upcoming_meetings_are_listed_with_their_homework(self):
        homework = PledgeTask.objects.create(title='Read chapter 3')
        event = Event.objects.create(
            title='Next Meeting', description='',
            date_time=timezone.now() + timezone.timedelta(days=2),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, created_by=self.chair,
        )
        meeting.homework.add(homework)

        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertContains(response, 'Next Meeting')
        self.assertContains(response, 'Read chapter 3')

    def test_a_past_meeting_is_not_listed_as_upcoming(self):
        event = Event.objects.create(
            title='Old Meeting', description='',
            date_time=timezone.now() - timezone.timedelta(days=2),
            created_by=self.chair,
        )
        EducationMeeting.objects.create(
            event=event, committee=self.committee, created_by=self.chair,
        )
        self.assertEqual(
            list(self.client.get(reverse('my_pledge_tasks')).context['upcoming_meetings']), []
        )


class EducationPagesRenderTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ REGRESSION TESTS FOR A 500 NOBODY HAD SEEN, AND IT EXPLAINS THE OTHER BUG.

    `quiz_submissions.html` referenced `committee.committee_code` in four
    `{% url %}` tags. `Committee` has no such attribute — it is `code` — and a
    missing template variable resolves to the empty string, so every one of
    those reversed as `{% url 'education_home' '' %}` and raised
    `NoReverseMatch`. **The quiz grading page has never rendered.**

    That is almost certainly why the `set_status` bug survived: the page whose
    buttons were broken could not be opened to notice.

    v3.16.3 added `TemplateUrlNameTests`, which scans templates for `{% url %}`
    *names* that do not exist. This slipped past it because the name was real
    and the **argument** was wrong — so the guard was one level too shallow.
    The durable fix is not another scanner but this: render the page.
    """

    def setUp(self):
        self.build()

    def test_the_quiz_submissions_page_renders(self):
        task = PledgeTask.objects.create(title='Quiz', task_type='quiz', max_score=60)
        PledgeTaskQuestion.objects.create(task=task, question_text='Who founded the chapter?')
        response = self.client.get(
            reverse('education_quiz_submissions', args=[self.committee.code, task.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_the_quiz_submissions_page_renders_with_a_submission(self):
        """The branch that actually draws the grading buttons and score box."""
        task = PledgeTask.objects.create(title='Quiz', task_type='quiz', max_score=60)
        question = PledgeTaskQuestion.objects.create(task=task, question_text='Q1')
        PledgeQuizAnswer.objects.create(
            question=question, pledge=self.pledge, answer_text='An answer',
        )
        PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='pending', score=42,
        )
        response = self.client.get(
            reverse('education_quiz_submissions', args=[self.committee.code, task.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '42/60')

    def test_the_education_dashboard_renders_with_a_meeting(self):
        event = Event.objects.create(
            title='Pledge Meeting', description='',
            date_time=timezone.now() + timezone.timedelta(days=1),
            created_by=self.chair,
        )
        EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5, created_by=self.chair,
        )
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pledge Meeting')

    def test_the_attendance_page_renders(self):
        event = Event.objects.create(
            title='Pledge Meeting', description='',
            date_time=timezone.now(), created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, created_by=self.chair,
        )
        response = self.client.get(
            reverse('education_meeting_attendance', args=[self.committee.code, meeting.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pledge One')


class CommitteeIsChairIsMemoisedTests(EducationFixtureMixin, TestCase):
    """
    v3.20.0 — `Committee.is_chair()` repeated the same query several times per
    page. Reported from production dev mode as *4× the same query shape*
    (`src_parliamentuser INNER JOIN src_committee_chairs … LIMIT 1`);
    `view/committee/recruitment.py` calls it four times while building one
    view's context.
    """

    def setUp(self):
        self.build()

    def test_repeated_calls_cost_one_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.committee.is_chair(self.chair)  # prime
        with CaptureQueriesContext(connection) as captured:
            self.committee.is_chair(self.chair)
            self.committee.is_chair(self.chair)
            self.committee.is_chair(self.chair)
        self.assertEqual(len(captured.captured_queries), 0)

    def test_the_answer_is_still_right_for_each_user(self):
        """A memo that returned one user's answer for another would be a
        privilege escalation, which is the failure mode worth pinning."""
        self.assertTrue(self.committee.is_chair(self.chair))
        self.assertFalse(self.committee.is_chair(self.brother))
        self.assertTrue(self.committee.is_chair(self.chair))

    def test_the_answer_is_per_committee(self):
        other = Committee.objects.create(name='Other', code='OTHER', is_active=True)
        self.assertTrue(self.committee.is_chair(self.chair))
        self.assertFalse(other.is_chair(self.chair))

    def test_the_memo_does_not_live_on_the_committee(self):
        """
        ⚠️ The safety property. A `Committee` instance can be cached; a memo
        stored on one would answer a permission question with another request's
        data. It must be on the per-request user object instead.
        """
        self.committee.is_chair(self.chair)
        self.assertFalse(
            any(k.endswith('_chair_memo') for k in self.committee.__dict__),
            'is_chair memoised onto the Committee instance — that survives '
            'instance caching and becomes a stale permission answer.',
        )
        self.assertTrue(hasattr(self.chair, '_committee_chair_memo'))


class TaskDeleteButtonTests(EducationFixtureMixin, TestCase):
    """
    v3.20.0 — the Delete button on the education dashboard did nothing.

    ⚠️ THE VIEW WAS ALWAYS FINE. The endpoint soft-deletes correctly and
    always has; what failed was the **browser** never reaching it. The page's
    JavaScript read the CSRF token with
    `document.cookie.match(/csrftoken=…)`, and `CSRF_COOKIE_HTTPONLY = True`
    means JS can never see that cookie — so every fetch on this page sent an
    empty `X-CSRFToken` and Django answered 403. The handlers catch and
    `console.log`, so the page silently did nothing.

    That is why these tests come in two halves: the endpoint (which passed
    before the fix and still passes) and the token source (in
    `src/test_csrf_token_source.py`, which is where the bug actually lived).
    **A server-side test of this button would have been green throughout.**
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Doomed Task')
        self.url = reverse(
            'education_delete_task', args=[self.committee.code, self.task.pk]
        )

    def test_the_endpoint_soft_deletes_the_task(self):
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'deleted': True, 'task_pk': self.task.pk})
        self.task.refresh_from_db()
        self.assertFalse(self.task.is_active)

    def test_a_deleted_task_disappears_from_the_dashboard(self):
        self.client.post(self.url)
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertNotContains(response, 'Doomed Task')

    def test_a_deleted_task_disappears_from_a_pledges_page(self):
        """Soft-delete has to mean gone for the pledge, not just hidden for staff."""
        pledge_client = Client()
        pledge_client.force_login(self.pledge)
        self.assertContains(pledge_client.get(reverse('my_pledge_tasks')), 'Doomed Task')

        self.client.post(self.url)
        self.assertNotContains(pledge_client.get(reverse('my_pledge_tasks')), 'Doomed Task')

    def test_the_dashboard_hands_the_browser_a_usable_csrf_token(self):
        """
        ⚠️ THE ACTUAL BUG, ASSERTED ON THE RENDERED PAGE.

        Not "does the endpoint work" — it always did — but "can the page call
        it". The rendered token must be a real token, not the empty string the
        cookie read produced.
        """
        body = self.client.get(
            reverse('education_home', args=[self.committee.code])
        ).content.decode()
        self.assertIn("meta[name=\"csrf-token\"]", body)
        self.assertNotIn("document.cookie.match(/csrftoken", body)


class HomeworkPickerTests(EducationFixtureMixin, TestCase):
    """
    v3.20.0 — the homework picker on the Add Meeting form.

    ⚠️ IT WAS A `<select multiple>` AND THAT IS A TRAP, not just ugly: picking a
    second option requires ctrl/cmd-click, and a plain click silently
    **deselects everything else**. The likely outcome of using one is assigning
    one task when you meant three, with no feedback that it happened. Checkboxes
    post the same repeated `homework` field, so the view is unchanged — these
    tests pin that equivalence, since a UI change that quietly breaks the wire
    format would look fine and assign nothing.
    """

    def setUp(self):
        self.build()
        self.url = reverse('education_home', args=[self.committee.code])

    def test_the_picker_lists_each_task_as_a_checkbox(self):
        PledgeTask.objects.create(title='Read chapter 3')
        PledgeTask.objects.create(title='Learn the Greek alphabet')
        body = self.client.get(self.url).content.decode()
        self.assertIn('type="checkbox" name="homework"', body)
        self.assertNotIn('<select name="homework"', body)

    def test_the_picker_shows_what_each_task_is(self):
        """
        The confusion was partly that a bare title does not say whether ticking
        something assigns a quiz, a reading, or something already overdue.
        """
        PledgeTask.objects.create(
            title='Ritual Exam', task_type='quiz', max_score=60, is_required=True,
        )
        body = self.client.get(self.url).content.decode()
        self.assertIn('Ritual Exam', body)
        self.assertIn('Quiz', body)
        self.assertIn('scored out of 60', body)
        self.assertIn('required', body)

    def test_with_no_tasks_it_explains_why_the_list_is_empty(self):
        """
        ⚠️ The old control rendered an empty box with no explanation, and the
        reason is not guessable: homework links to tasks that already exist, so
        a chair who has created none sees nothing and cannot tell whether the
        feature is broken.
        """
        self.assertFalse(PledgeTask.objects.exists())
        body = self.client.get(self.url).content.decode()
        self.assertIn('No tasks exist yet', body)

    def test_ticking_several_boxes_assigns_all_of_them(self):
        """
        The wire format, which is the half a visual change can silently break.
        Repeated `homework` fields must still reach `getlist('homework')`.
        """
        one = PledgeTask.objects.create(title='Read chapter 3')
        two = PledgeTask.objects.create(title='Learn the Greek alphabet')
        three = PledgeTask.objects.create(title='Not assigned')

        self.client.post(
            reverse('education_add_meeting', args=[self.committee.code]),
            {
                'title': 'Pledge Meeting',
                'date_time': (timezone.now() + timezone.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'meeting_type': 'meeting',
                'points': '0',
                'homework': [str(one.pk), str(two.pk)],
            },
        )
        assigned = set(EducationMeeting.objects.get().homework.values_list('pk', flat=True))
        self.assertEqual(assigned, {one.pk, two.pk})
        self.assertNotIn(three.pk, assigned)

    def test_a_meeting_with_no_homework_is_fine(self):
        """'Leave them all unticked' has to actually work — no field is posted."""
        self.client.post(
            reverse('education_add_meeting', args=[self.committee.code]),
            {
                'title': 'Pledge Meeting',
                'date_time': (timezone.now() + timezone.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                'meeting_type': 'meeting',
                'points': '0',
            },
        )
        self.assertEqual(EducationMeeting.objects.get().homework.count(), 0)


class ScoringDoesNotCostAQueryPerRowTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ N+1s THE SCORING FEATURE INTRODUCED ON THE TWO PAGES IT WAS BUILT FOR.

    `has_score`, `score_display` and `score_percent` all read
    `self.task.max_score`. Both pages fetched completions without joining the
    task, so each graded row cost an extra query. Measured before the fix on a
    12-pledge / 10-task fixture: **13 × `src_pledgetask`** on the grading page
    and **11 ×** on My Tasks.

    These assert the *property* — adding rows must not add queries — rather than
    an absolute ceiling, because an absolute number here would be a second place
    to maintain the one in `test_query_budgets.py`.
    """

    def setUp(self):
        self.build()

    def _count(self, client, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.core.cache import cache
        cache.clear()
        client.get(url)          # warm caches so we measure the page, not the session
        cache.clear()
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)

    def test_grading_page_does_not_query_per_graded_pledge(self):
        task = PledgeTask.objects.create(title='Quiz', task_type='quiz', max_score=60)
        question = PledgeTaskQuestion.objects.create(task=task, question_text='Q1')
        url = reverse('education_quiz_submissions', args=[self.committee.code, task.pk])

        for pledge in (self.pledge, self.other_pledge):
            PledgeQuizAnswer.objects.create(question=question, pledge=pledge, answer_text='a')
            PledgeTaskCompletion.objects.create(
                task=task, pledge=pledge, status='completed', score=50,
            )
        before = self._count(self.client, url)

        for i in range(6):
            extra = make_user(f'P-EX{i:04d}', f'Extra {i}', member_type='Pledge')
            PledgeQuizAnswer.objects.create(question=question, pledge=extra, answer_text='a')
            PledgeTaskCompletion.objects.create(
                task=task, pledge=extra, status='completed', score=50,
            )
        after = self._count(self.client, url)

        self.assertEqual(
            before, after,
            f'Six more graded pledges added {after - before} queries. '
            f'`score_display` reads `completion.task`; the completions queryset '
            f'needs select_related("task").',
        )

    def test_my_tasks_does_not_query_per_scored_task(self):
        pledge_client = Client()
        pledge_client.force_login(self.pledge)
        url = reverse('my_pledge_tasks')

        for i in range(2):
            task = PledgeTask.objects.create(title=f'Scored {i}', max_score=20)
            PledgeTaskCompletion.objects.create(
                task=task, pledge=self.pledge, status='completed', score=15,
            )
        before = self._count(pledge_client, url)

        for i in range(2, 10):
            task = PledgeTask.objects.create(title=f'Scored {i}', max_score=20)
            PledgeTaskCompletion.objects.create(
                task=task, pledge=self.pledge, status='completed', score=15,
            )
        after = self._count(pledge_client, url)

        self.assertEqual(
            before, after,
            f'Eight more scored tasks added {after - before} queries.',
        )


class ResubmittingAQuizClearsTheStaleGradeTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ A CHAIR ADDING A QUESTION REOPENS THE QUIZ FOR EVERYONE WHO ALREADY SAT
    IT — including pledges already marked and scored.

    `already_submitted` means "has answered every question", so a new question
    makes it false again. Before scoring that just reset `completed` → `pending`
    (defensible: there are new answers to read). With scoring it would have left
    the OLD mark on the NEW answers — a pledge's page reading "50/60" beside
    answers nobody has graded.
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Quiz', task_type='quiz', max_score=60)
        self.q1 = PledgeTaskQuestion.objects.create(task=self.task, question_text='Q1')
        self.pledge_client = Client()
        self.pledge_client.force_login(self.pledge)
        self.url = reverse('pledge_take_quiz', args=[self.task.pk])

    def _completion(self):
        return PledgeTaskCompletion.objects.get(task=self.task, pledge=self.pledge)

    def test_resubmitting_after_a_new_question_clears_the_old_score(self):
        self.pledge_client.post(self.url, {f'answer_{self.q1.pk}': 'first answer'})
        comp = self._completion()
        comp.status, comp.score = 'completed', 50
        comp.save()

        q2 = PledgeTaskQuestion.objects.create(task=self.task, question_text='Q2')
        self.pledge_client.post(self.url, {
            f'answer_{self.q1.pk}': 'first answer',
            f'answer_{q2.pk}': 'second answer',
        })

        comp = self._completion()
        self.assertEqual(comp.status, 'pending')
        self.assertIsNone(
            comp.score,
            'A stale mark survived a resubmission — the page would show a score '
            'for answers no chair has read.',
        )

    def test_the_control_a_graded_quiz_keeps_its_score_when_nothing_changes(self):
        """The score must only be cleared by an actual resubmission."""
        self.pledge_client.post(self.url, {f'answer_{self.q1.pk}': 'first answer'})
        comp = self._completion()
        comp.status, comp.score = 'completed', 50
        comp.save()

        # Already answered every question, so this POST is refused as a re-take.
        self.pledge_client.post(self.url, {f'answer_{self.q1.pk}': 'changed my mind'})

        comp = self._completion()
        self.assertEqual(comp.status, 'completed')
        self.assertEqual(comp.score, 50)


class AttendanceRollUpCountsOnlyDecidedRowsTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ THE "N PLEDGES MARKED" NUMBER ON THE DASHBOARD COUNTED ROWS, NOT MARKS.

    The attendance form pre-selects `pending` for every unmarked pledge, so a
    chair who opens it and hits Save writes a row for the **whole roster**. A
    plain `Count('attendance_records')` then reports "2 pledges marked" for a
    meeting where nobody was marked at all — and the number a chair uses to
    decide whether attendance still needs taking is exactly the number that
    would be wrong.
    """

    def setUp(self):
        self.build()
        event = Event.objects.create(
            title='Pledge Meeting', description='',
            date_time=timezone.now() + timezone.timedelta(days=1),
            created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, created_by=self.chair,
        )
        self.attendance_url = reverse(
            'education_meeting_attendance', args=[self.committee.code, self.meeting.pk]
        )
        self.home_url = reverse('education_home', args=[self.committee.code])

    def _marked_count(self):
        meetings = self.client.get(self.home_url).context['upcoming_meetings']
        return meetings[0].marked_count

    def test_saving_the_form_without_marking_anyone_reports_zero(self):
        self.client.post(self.attendance_url, {
            f'status_{self.pledge.pk}': 'pending',
            f'status_{self.other_pledge.pk}': 'pending',
        })
        # Rows exist...
        self.assertEqual(EducationMeetingAttendance.objects.count(), 2)
        # ...but nobody was actually marked.
        self.assertEqual(self._marked_count(), 0)

    def test_it_counts_each_decided_pledge(self):
        self.client.post(self.attendance_url, {
            f'status_{self.pledge.pk}': 'present',
            f'status_{self.other_pledge.pk}': 'pending',
        })
        self.assertEqual(self._marked_count(), 1)

    def test_absent_and_excused_count_as_marked(self):
        """Marked means "a chair decided", not "the pledge turned up"."""
        self.client.post(self.attendance_url, {
            f'status_{self.pledge.pk}': 'absent',
            f'status_{self.other_pledge.pk}': 'excused',
        })
        self.assertEqual(self._marked_count(), 2)


class EditingAMeetingTests(EducationFixtureMixin, TestCase):
    """
    v3.20.1 — meeting editing, and the trap it closes.

    ⚠️ v3.20.0 shipped create and delete and NO EDIT. So the only way to correct
    a mistyped time was to delete the meeting and make a new one — and deleting
    cascades to `EducationMeetingAttendance`. **A typo destroyed the record of
    who turned up.** That is a data-loss trap behind the most ordinary mistake a
    chair can make, which is why `test_attendance_survives_an_edit` is the point
    of the whole change.
    """

    def setUp(self):
        self.build()
        self.homework = PledgeTask.objects.create(title='Read chapter 3')
        self.other_task = PledgeTask.objects.create(title='Learn the alphabet')
        event = Event.objects.create(
            title='Pledge Meeting', description='Bring notes',
            date_time=timezone.now() + timezone.timedelta(days=2),
            location='Chapter room', created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5,
            meeting_type='meeting', created_by=self.chair,
        )
        self.meeting.homework.add(self.homework)
        self.url = reverse('education_edit_meeting', args=[self.committee.code, self.meeting.pk])

    def _payload(self, **overrides):
        payload = {
            'title': 'Pledge Meeting',
            'date_time': (timezone.now() + timezone.timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
            'location': 'Library',
            'meeting_type': 'study',
            'points': '8',
            'attendance_required': 'on',
            'homework': [str(self.homework.pk)],
        }
        payload.update(overrides)
        return payload

    def test_the_form_renders_prefilled(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('Pledge Meeting', body)
        self.assertIn('Chapter room', body)
        self.assertIn('Bring notes', body)

    def test_editing_updates_the_meeting_and_its_calendar_entry(self):
        self.client.post(self.url, self._payload(title='Study Session'))
        self.meeting.refresh_from_db()
        self.meeting.event.refresh_from_db()
        self.assertEqual(self.meeting.event.title, 'Study Session')
        self.assertEqual(self.meeting.event.location, 'Library')
        self.assertEqual(self.meeting.meeting_type, 'study')
        self.assertEqual(self.meeting.points, 8)

    def test_attendance_survives_an_edit(self):
        """
        ⚠️ THE WHOLE POINT. Before this view existed, changing a meeting's time
        meant deleting it — and every attendance record went with it.
        """
        EducationMeetingAttendance.objects.create(
            meeting=self.meeting, pledge=self.pledge, status='present',
        )
        EducationMeetingAttendance.objects.create(
            meeting=self.meeting, pledge=self.other_pledge, status='absent',
        )

        self.client.post(self.url, self._payload(title='Moved to Thursday'))

        self.assertEqual(EducationMeetingAttendance.objects.count(), 2)
        self.assertEqual(
            EducationMeetingAttendance.objects.get(pledge=self.pledge).status, 'present',
        )
        self.assertEqual(
            EducationMeetingAttendance.objects.get(pledge=self.other_pledge).status, 'absent',
        )

    def test_editing_keeps_the_same_calendar_event(self):
        """A new Event would strand the old one on the chapter calendar."""
        event_pk = self.meeting.event.pk
        self.client.post(self.url, self._payload())
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.event.pk, event_pk)
        self.assertEqual(Event.objects.count(), 1)

    def test_homework_can_be_added_and_removed(self):
        self.client.post(self.url, self._payload(homework=[str(self.other_task.pk)]))
        self.assertEqual(list(self.meeting.homework.all()), [self.other_task])

    def test_unticking_everything_clears_the_homework(self):
        """
        `set()` on an empty list is the difference between "no change" and
        "unassigned" — and unticking a box has to mean the second one.
        """
        payload = self._payload()
        payload.pop('homework')
        self.client.post(self.url, payload)
        self.assertEqual(self.meeting.homework.count(), 0)

    def test_a_bad_date_is_rejected_and_changes_nothing(self):
        self.assertEqual(self.client.post(self.url, self._payload(date_time='nope')).status_code, 400)
        self.meeting.event.refresh_from_db()
        self.assertEqual(self.meeting.event.location, 'Chapter room')

    def test_a_blank_title_is_rejected(self):
        self.assertEqual(self.client.post(self.url, self._payload(title='')).status_code, 400)

    def test_a_meeting_from_another_committee_is_404(self):
        other = Committee.objects.create(name='Other', code='OTHER', is_active=True,
                                         is_education_committee=True)
        event = Event.objects.create(
            title='Theirs', description='', date_time=timezone.now(), created_by=self.chair,
        )
        theirs = EducationMeeting.objects.create(
            event=event, committee=other, created_by=self.chair,
        )
        url = reverse('education_edit_meeting', args=[self.committee.code, theirs.pk])
        self.assertEqual(self.client.get(url).status_code, 404)


class AMeetingIsWrittenAllAtOnceTests(EducationFixtureMixin, TestCase):
    """
    v3.21.5 — a meeting is two rows, and half of one is worse than none.

    ⚠️ `EducationMeeting.event` is a `OneToOneField`, so the `Event` must be
    saved first. Without a transaction, a failure on the second save left the
    Event behind as **a pledge-education entry on the chapter calendar with
    nothing behind it**: no attendance page, no row on the education dashboard,
    and `education_delete_meeting` deletes *through* the meeting, so nothing in
    the interface could remove it. An orphan you cannot delete from the UI is a
    database job for whoever inherits this app.

    The failure is simulated rather than waited for, because the realistic
    trigger — a constraint violation, a dropped connection mid-request — is not
    something a test can arrange honestly.
    """

    def setUp(self):
        self.build()
        self.url = reverse('education_add_meeting', args=[self.committee.code])

    def _payload(self, **overrides):
        payload = {
            'title': 'Founders Night',
            'date_time': '2026-09-01T19:00',
            'location': 'Chapter Room',
            'description': '',
            'meeting_type': 'meeting',
            'points': '2',
            'notes': '',
        }
        payload.update(overrides)
        return payload

    def test_a_failure_on_the_second_save_leaves_no_orphan_event(self):
        from unittest import mock

        before = Event.objects.count()

        with mock.patch.object(
            EducationMeeting, 'save', side_effect=RuntimeError('simulated failure')
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self._payload())

        self.assertEqual(
            Event.objects.count(), before,
            'The Event survived a failed meeting creation. It is now on the '
            'calendar with no EducationMeeting behind it, and nothing in the '
            'interface can delete it.',
        )
        self.assertEqual(EducationMeeting.objects.count(), 0)

    def test_the_control_a_successful_create_writes_both_rows(self):
        """
        CONTROL. A view that wrote nothing at all would pass the assertion
        above trivially — this is the assertion that says the fixture and the
        form actually work.
        """
        response = self.client.post(self.url, self._payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(EducationMeeting.objects.count(), 1)
        meeting = EducationMeeting.objects.get()
        self.assertEqual(meeting.event.title, 'Founders Night')
        self.assertEqual(meeting.points, 2)


class CreateAndEditShareOneFormTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ Two copies of a form drift, and the symptom is a field that silently does
    nothing on one of them. The fields are one include and the parsing is one
    function; these tests assert that rather than trusting it.
    """

    def setUp(self):
        self.build()
        PledgeTask.objects.create(title='Read chapter 3')

    def test_both_pages_render_the_same_field_names(self):
        add_page = self.client.get(
            reverse('education_home', args=[self.committee.code])
        ).content.decode()

        event = Event.objects.create(
            title='M', description='', date_time=timezone.now(), created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, created_by=self.chair,
        )
        edit_page = self.client.get(
            reverse('education_edit_meeting', args=[self.committee.code, meeting.pk])
        ).content.decode()

        for field in ('name="title"', 'name="date_time"', 'name="location"',
                      'name="description"', 'name="meeting_type"', 'name="points"',
                      'name="attendance_required"', 'name="notes"', 'name="homework"'):
            with self.subTest(field=field):
                self.assertIn(field, add_page)
                self.assertIn(field, edit_page)

    def test_the_shared_parser_is_what_both_views_use(self):
        """
        A structural assertion, because the drift this guards against is
        invisible in behaviour until someone adds a field to one form.
        """
        import inspect
        from src.view.committee import education
        for view in (education.education_add_meeting, education.education_edit_meeting):
            with self.subTest(view=view.__name__):
                self.assertIn('_apply_meeting_fields', inspect.getsource(view))


class OverdueTasksTests(EducationFixtureMixin, TestCase):
    """
    v3.20.2 — `due_date` was stored from the start and surfaced NOWHERE, so a
    task three weeks late looked exactly like one due tomorrow.

    ⚠️ Overdue is a property of a (task, pledge) PAIR, never of a task alone —
    the same task is finished for one pledge and late for another. That is why
    `is_overdue_for` takes the completion, and why it must handle `None`: a
    pledge who has never been marked is the common case and the one most worth
    flagging.
    """

    def setUp(self):
        self.build()
        from datetime import timedelta
        from django.utils import timezone as tz
        self.yesterday = tz.localdate() - timedelta(days=1)
        self.tomorrow = tz.localdate() + timedelta(days=1)

    def test_a_past_due_task_with_no_completion_is_overdue(self):
        task = PledgeTask.objects.create(title='Late', due_date=self.yesterday)
        self.assertTrue(task.is_overdue_for(None))

    def test_a_future_task_is_not_overdue(self):
        task = PledgeTask.objects.create(title='Soon', due_date=self.tomorrow)
        self.assertFalse(task.is_overdue_for(None))

    def test_a_task_with_no_due_date_is_never_overdue(self):
        self.assertFalse(PledgeTask.objects.create(title='Whenever').is_overdue_for(None))

    def test_a_completed_task_is_not_overdue(self):
        task = PledgeTask.objects.create(title='Late', due_date=self.yesterday)
        comp = PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='completed',
        )
        self.assertFalse(task.is_overdue_for(comp))

    def test_a_waived_task_is_not_overdue(self):
        """
        ⚠️ A chair has decided he does not have to do it. Showing it as overdue
        would nag him about somebody else's decision.
        """
        task = PledgeTask.objects.create(title='Late', due_date=self.yesterday)
        comp = PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='waived',
        )
        self.assertFalse(task.is_overdue_for(comp))

    def test_a_pending_task_is_still_overdue(self):
        """Submitted-but-ungraded past the deadline is late for both of them."""
        task = PledgeTask.objects.create(title='Late', due_date=self.yesterday)
        comp = PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='pending',
        )
        self.assertTrue(task.is_overdue_for(comp))

    def test_the_pledge_page_leads_with_the_overdue_count(self):
        PledgeTask.objects.create(title='Late one', due_date=self.yesterday)
        PledgeTask.objects.create(title='Late two', due_date=self.yesterday)
        PledgeTask.objects.create(title='Fine', due_date=self.tomorrow)

        pledge_client = Client()
        pledge_client.force_login(self.pledge)
        response = pledge_client.get(reverse('my_pledge_tasks'))

        self.assertEqual(response.context['overdue_count'], 2)
        self.assertContains(response, '2 tasks past due')

    def test_the_pledge_page_says_nothing_when_nothing_is_late(self):
        PledgeTask.objects.create(title='Fine', due_date=self.tomorrow)
        pledge_client = Client()
        pledge_client.force_login(self.pledge)
        response = pledge_client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.context['overdue_count'], 0)
        self.assertNotContains(response, 'past due')


class PledgeDetailPageTests(EducationFixtureMixin, TestCase):
    """
    v3.20.2 — one page per pledge for the educator.

    The dashboard grid answers "who has not done task 4" and is useless for
    "how is Jack doing", which is the question before a progress conversation
    or an initiation vote.
    """

    def setUp(self):
        self.build()
        self.url = reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk])

    def test_it_renders(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_it_shows_tasks_with_their_scores(self):
        task = PledgeTask.objects.create(title='Ritual Exam', max_score=60)
        PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='completed', score=50,
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'Ritual Exam')
        self.assertContains(response, '50/60')

    def test_it_excludes_tasks_assigned_to_other_pledges(self):
        """
        ⚠️ Counting a task that does not apply to him would misreport his
        progress — and this page is read before an initiation vote.
        """
        mine = PledgeTask.objects.create(title='Everyone')
        theirs = PledgeTask.objects.create(title='Not for him')
        theirs.assigned_to.add(self.other_pledge)

        response = self.client.get(self.url)
        titles = [row['task'].title for row in response.context['task_rows']]
        self.assertIn(mine.title, titles)
        self.assertNotIn(theirs.title, titles)

    def test_required_progress_counts_only_applicable_tasks(self):
        PledgeTask.objects.create(title='Required A', is_required=True)
        theirs = PledgeTask.objects.create(title='Required B', is_required=True)
        theirs.assigned_to.add(self.other_pledge)

        context = self.client.get(self.url).context
        self.assertEqual(context['required_total'], 1)

    def test_it_lists_attendance_and_totals_the_points(self):
        event = Event.objects.create(
            title='Pledge Meeting', description='',
            date_time=timezone.now() - timezone.timedelta(days=1),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5, created_by=self.chair,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='present',
        )

        context = self.client.get(self.url).context
        self.assertEqual(context['attended_count'], 1)
        self.assertEqual(context['attendance_points'], 5)
        self.assertContains(self.client.get(self.url), 'Pledge Meeting')

    def test_it_counts_missed_meetings(self):
        """The question is 'which did he miss', so absent is its own number."""
        event = Event.objects.create(
            title='Missed one', description='',
            date_time=timezone.now() - timezone.timedelta(days=1),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5, created_by=self.chair,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.pledge, status='absent',
        )
        context = self.client.get(self.url).context
        self.assertEqual(context['missed_count'], 1)
        self.assertEqual(context['attendance_points'], 0)

    def test_it_shows_only_this_pledges_records(self):
        event = Event.objects.create(
            title='Meeting', description='',
            date_time=timezone.now() - timezone.timedelta(days=1),
            created_by=self.chair,
        )
        meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, points=5, created_by=self.chair,
        )
        EducationMeetingAttendance.objects.create(
            meeting=meeting, pledge=self.other_pledge, status='present',
        )
        context = self.client.get(self.url).context
        self.assertEqual(context['attended_count'], 0)
        self.assertEqual(context['attendance_points'], 0)

    def test_it_flags_overdue_tasks(self):
        from datetime import timedelta
        from django.utils import timezone as tz
        PledgeTask.objects.create(title='Late', due_date=tz.localdate() - timedelta(days=3))
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['overdue_rows']), 1)
        self.assertContains(response, 'Past due')

    def test_a_non_pledge_is_404(self):
        url = reverse('education_pledge_detail', args=[self.committee.code, self.brother.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_member_outside_the_committee_cannot_read_it(self):
        outsider = Client()
        outsider.force_login(self.brother)
        self.assertEqual(outsider.get(self.url).status_code, 404)


class AttendanceBulkActionsTests(EducationFixtureMixin, TestCase):
    """
    v3.20.2 — "everyone came except two" is what attendance actually is, so the
    page offers one click for the common case.

    ⚠️ Client-side only, deliberately: nothing is written until Save
    attendance, so a misclick costs nothing and there is no endpoint that can
    bulk-write a roster.
    """

    def setUp(self):
        self.build()
        event = Event.objects.create(
            title='Pledge Meeting', description='',
            date_time=timezone.now(), created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=event, committee=self.committee, created_by=self.chair,
        )
        self.url = reverse(
            'education_meeting_attendance', args=[self.committee.code, self.meeting.pk]
        )

    def test_the_page_offers_bulk_buttons(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('data-bulk="present"', body)
        self.assertIn('data-bulk="absent"', body)
        self.assertIn('data-bulk="pending"', body)

    def test_there_is_no_bulk_endpoint(self):
        """
        The buttons only move radios. If a future change adds a server-side
        bulk write, it needs its own authz review — this asserts nobody added
        one quietly.
        """
        response = self.client.post(self.url, {'bulk': 'present'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EducationMeetingAttendance.objects.count(), 0)


class PledgeSeesHisOwnAttendanceTests(EducationFixtureMixin, TestCase):
    """
    v3.21.0 (ideas #6) — a pledge could see "attendance points: 10" and had no
    way to find out why it was not 25.

    ⚠️ If you score someone, the person being scored has to be able to audit it.
    Otherwise the first he hears of a gap is at an initiation review, from
    somebody holding a number he cannot check.
    """

    def setUp(self):
        self.build()
        self.client = Client()
        self.client.force_login(self.pledge)
        self.event = Event.objects.create(
            title='Missed Meeting', description='',
            date_time=timezone.now() - timezone.timedelta(days=2),
            created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=self.event, committee=self.committee, points=5, created_by=self.chair,
        )

    def test_he_sees_the_meetings_he_missed(self):
        EducationMeetingAttendance.objects.create(
            meeting=self.meeting, pledge=self.pledge, status='absent',
        )
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.context['missed_count'], 1)
        self.assertContains(response, 'Missed Meeting')

    def test_he_does_not_see_another_pledges_record(self):
        EducationMeetingAttendance.objects.create(
            meeting=self.meeting, pledge=self.other_pledge, status='absent',
        )
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(list(response.context['attendance_history']), [])
        self.assertEqual(response.context['missed_count'], 0)


class AbsenceRequestTests(EducationFixtureMixin, TestCase):
    """v3.21.0 (ideas #7) — asking to be excused, and the chair deciding."""

    def setUp(self):
        self.build()
        self.future = Event.objects.create(
            title='Next Meeting', description='',
            date_time=timezone.now() + timezone.timedelta(days=3),
            created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(
            event=self.future, committee=self.committee, points=5, created_by=self.chair,
        )
        self.pledge_client = Client()
        self.pledge_client.force_login(self.pledge)
        self.url = reverse('pledge_request_absence', args=[self.meeting.pk])

    def test_a_pledge_can_ask_to_be_excused(self):
        self.pledge_client.post(self.url, {'reason': 'Family funeral'})
        request = EducationAbsenceRequest.objects.get()
        self.assertEqual(request.pledge, self.pledge)
        self.assertEqual(request.status, 'pending')
        self.assertEqual(request.reason, 'Family funeral')

    def test_a_blank_reason_is_refused(self):
        self.pledge_client.post(self.url, {'reason': '   '})
        self.assertFalse(EducationAbsenceRequest.objects.exists())

    def test_he_cannot_ask_about_a_meeting_that_already_happened(self):
        """
        ⚠️ That is not a request, it is a dispute about a record — a different
        conversation, and one that should happen with a person rather than
        through a form that looks like it will be granted.
        """
        past = Event.objects.create(
            title='Last week', description='',
            date_time=timezone.now() - timezone.timedelta(days=2),
            created_by=self.chair,
        )
        old_meeting = EducationMeeting.objects.create(
            event=past, committee=self.committee, created_by=self.chair,
        )
        self.pledge_client.post(
            reverse('pledge_request_absence', args=[old_meeting.pk]),
            {'reason': 'I was ill'},
        )
        self.assertFalse(EducationAbsenceRequest.objects.exists())

    def test_asking_twice_edits_the_first_request(self):
        """The unique constraint would otherwise 500 on a second submission."""
        self.pledge_client.post(self.url, {'reason': 'First reason'})
        self.pledge_client.post(self.url, {'reason': 'Better reason'})
        self.assertEqual(EducationAbsenceRequest.objects.count(), 1)
        self.assertEqual(EducationAbsenceRequest.objects.get().reason, 'Better reason')

    def test_re_asking_after_a_decision_resets_it_to_pending(self):
        self.pledge_client.post(self.url, {'reason': 'First'})
        request = EducationAbsenceRequest.objects.get()
        request.status = 'denied'
        request.save()

        self.pledge_client.post(self.url, {'reason': 'New information'})
        self.assertEqual(EducationAbsenceRequest.objects.get().status, 'pending')

    def test_approving_marks_him_excused(self):
        """
        ⚠️ THE POINT OF THE WHOLE FLOW. An approval that left the roster
        untouched means a chair approves an absence and the pledge is still
        marked absent at the next review — the "I told him and he forgot"
        failure this exists to remove.
        """
        self.pledge_client.post(self.url, {'reason': 'Family funeral'})
        request = EducationAbsenceRequest.objects.get()

        self.client.post(
            reverse('education_review_absence', args=[self.committee.code, request.pk]),
            {'decision': 'approved', 'review_note': 'Sorry to hear it'},
        )

        request.refresh_from_db()
        self.assertEqual(request.status, 'approved')
        self.assertEqual(request.reviewed_by, self.chair)
        record = EducationMeetingAttendance.objects.get(meeting=self.meeting, pledge=self.pledge)
        self.assertEqual(record.status, 'excused')

    def test_denying_writes_no_attendance(self):
        """The meeting has not happened; he may still turn up."""
        self.pledge_client.post(self.url, {'reason': 'I would rather not'})
        request = EducationAbsenceRequest.objects.get()
        self.client.post(
            reverse('education_review_absence', args=[self.committee.code, request.pk]),
            {'decision': 'denied'},
        )
        request.refresh_from_db()
        self.assertEqual(request.status, 'denied')
        self.assertFalse(EducationMeetingAttendance.objects.exists())

    def test_an_unknown_decision_is_rejected(self):
        self.pledge_client.post(self.url, {'reason': 'x'})
        request = EducationAbsenceRequest.objects.get()
        response = self.client.post(
            reverse('education_review_absence', args=[self.committee.code, request.pk]),
            {'decision': 'maybe'},
        )
        self.assertEqual(response.status_code, 400)

    def test_the_dashboard_lists_pending_requests_only(self):
        self.pledge_client.post(self.url, {'reason': 'Pending one'})
        context = self.client.get(
            reverse('education_home', args=[self.committee.code])
        ).context
        self.assertEqual(len(context['pending_absences']), 1)

        request = EducationAbsenceRequest.objects.get()
        request.status = 'approved'
        request.save()
        context = self.client.get(
            reverse('education_home', args=[self.committee.code])
        ).context
        self.assertEqual(len(context['pending_absences']), 0)


class DuplicateTaskTests(EducationFixtureMixin, TestCase):
    """v3.21.0 (ideas #8) — cloning, so a term's tasks are not retyped."""

    def setUp(self):
        self.build()
        from datetime import date
        self.original = PledgeTask.objects.create(
            title='Ritual Exam', description='Learn it', task_type='quiz',
            phase='1', is_required=True, points=3, max_score=60,
            due_date=date(2026, 1, 1), activation_mode='immediate', is_published=True,
        )
        PledgeTaskQuestion.objects.create(task=self.original, question_text='Q1')
        self.url = reverse('education_duplicate_task', args=[self.committee.code, self.original.pk])

    def _clone(self):
        return PledgeTask.objects.exclude(pk=self.original.pk).get()

    def test_it_copies_the_content(self):
        self.client.post(self.url)
        clone = self._clone()
        self.assertEqual(clone.title, 'Ritual Exam (copy)')
        self.assertEqual(clone.description, 'Learn it')
        self.assertEqual(clone.task_type, 'quiz')
        self.assertEqual(clone.phase, '1')
        self.assertEqual(clone.max_score, 60)
        self.assertEqual(clone.points, 3)

    def test_the_clone_is_always_a_draft(self):
        """
        ⚠️ A duplicate that went live immediately would publish a half-edited
        task — still called "… (copy)" — to every pledge's page.
        """
        self.client.post(self.url)
        clone = self._clone()
        self.assertEqual(clone.activation_mode, 'manual')
        self.assertFalse(clone.is_published)
        self.assertFalse(clone.is_live)

    def test_the_due_date_is_not_copied(self):
        """Last term's date is wrong by definition, and a stale one is worse than none."""
        self.client.post(self.url)
        self.assertIsNone(self._clone().due_date)

    def test_quiz_questions_come_with_it(self):
        """A quiz without its questions is not a copy of that quiz."""
        self.client.post(self.url)
        self.assertEqual(self._clone().questions.count(), 1)

    def test_answers_do_not_come_with_it(self):
        """They belong to the sitting, not to the paper."""
        question = self.original.questions.first()
        PledgeQuizAnswer.objects.create(question=question, pledge=self.pledge, answer_text='a')
        self.client.post(self.url)
        clone_question = self._clone().questions.first()
        self.assertEqual(clone_question.answers.count(), 0)

    def test_completions_do_not_come_with_it(self):
        PledgeTaskCompletion.objects.create(
            task=self.original, pledge=self.pledge, status='completed', score=50,
        )
        self.client.post(self.url)
        self.assertEqual(self._clone().completions.count(), 0)


class QuizAnalysisTests(EducationFixtureMixin, TestCase):
    """
    v3.21.0 (ideas #9) — which questions the class got wrong.

    ⚠️ THIS FEATURE COULD NOT EXIST AS PROPOSED. `PledgeQuizAnswer` stored free
    text and nothing else, so nothing recorded whether an answer was right and
    "which question did everyone miss" was not a question the data could answer.
    v3.21.0 adds `is_correct` and per-question marking; the analysis is only as
    good as the marking, which is why every row reports how many are unmarked.
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Exam', task_type='quiz', max_score=10)
        self.q1 = PledgeTaskQuestion.objects.create(task=self.task, question_text='Easy one', display_order=0)
        self.q2 = PledgeTaskQuestion.objects.create(task=self.task, question_text='Hard one', display_order=1)
        self.url = reverse('education_quiz_analysis', args=[self.committee.code, self.task.pk])

    def _answer(self, question, pledge, correct=None):
        return PledgeQuizAnswer.objects.create(
            question=question, pledge=pledge, answer_text='an answer', is_correct=correct,
        )

    def test_it_reports_correct_and_wrong_per_question(self):
        self._answer(self.q1, self.pledge, correct=True)
        self._answer(self.q1, self.other_pledge, correct=True)
        self._answer(self.q2, self.pledge, correct=False)
        self._answer(self.q2, self.other_pledge, correct=True)

        rows = {r['question'].pk: r for r in self.client.get(self.url).context['rows']}
        self.assertEqual(rows[self.q1.pk]['percent'], 100)
        self.assertEqual(rows[self.q2.pk]['percent'], 50)

    def test_the_weakest_question_is_first(self):
        """The page answers 'what do I re-teach' — that must not be at the bottom."""
        self._answer(self.q1, self.pledge, correct=True)
        self._answer(self.q2, self.pledge, correct=False)
        rows = self.client.get(self.url).context['rows']
        self.assertEqual(rows[0]['question'].pk, self.q2.pk)

    def test_unmarked_is_not_zero_percent(self):
        """
        ⚠️ "0% correct" and "nobody has marked this" are opposite messages and
        must not share a rendering.
        """
        self._answer(self.q1, self.pledge, correct=None)
        rows = {r['question'].pk: r for r in self.client.get(self.url).context['rows']}
        self.assertIsNone(rows[self.q1.pk]['percent'])
        self.assertEqual(rows[self.q1.pk]['unmarked'], 1)

    def test_marking_an_answer(self):
        answer = self._answer(self.q1, self.pledge)
        url = reverse('education_mark_answer', args=[self.committee.code, self.task.pk, answer.pk])

        self.client.post(url, {'verdict': 'correct'})
        answer.refresh_from_db()
        self.assertIs(answer.is_correct, True)

        self.client.post(url, {'verdict': 'wrong'})
        answer.refresh_from_db()
        self.assertIs(answer.is_correct, False)

        self.client.post(url, {'verdict': 'clear'})
        answer.refresh_from_db()
        self.assertIsNone(answer.is_correct)

    def test_an_unknown_verdict_is_rejected(self):
        answer = self._answer(self.q1, self.pledge)
        url = reverse('education_mark_answer', args=[self.committee.code, self.task.pk, answer.pk])
        self.assertEqual(self.client.post(url, {'verdict': 'ok'}).status_code, 400)


class QuizAnalysisVisibilityTests(EducationFixtureMixin, TestCase):
    """
    ⚠️ MASON'S CALL: EDUCATORS BY DEFAULT, PLEDGES ONLY IF SHARED.

    The gate is a plain model field, not a feature flag — CLAUDE.md records that
    Python flag reads fail **OPEN**, and a defaulting-open gate on "who may see
    the class's results" is the wrong direction to be wrong in. A boolean
    defaulting to `False` fails closed with no seeding step to forget.
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Exam', task_type='quiz')
        PledgeTaskQuestion.objects.create(task=self.task, question_text='Q1')
        self.pledge_client = Client()
        self.pledge_client.force_login(self.pledge)
        self.url = reverse('pledge_quiz_analysis', args=[self.task.pk])

    def test_the_default_is_off(self):
        self.assertFalse(self.task.show_analysis_to_pledges)

    def test_a_pledge_cannot_see_it_by_default(self):
        self.assertEqual(self.pledge_client.get(self.url).status_code, 404)

    def test_a_pledge_can_see_it_once_shared(self):
        self.task.show_analysis_to_pledges = True
        self.task.save()
        self.assertEqual(self.pledge_client.get(self.url).status_code, 200)

    def test_the_educator_can_always_see_it(self):
        educator_url = reverse('education_quiz_analysis', args=[self.committee.code, self.task.pk])
        self.assertEqual(self.client.get(educator_url).status_code, 200)

    def test_both_audiences_are_given_the_same_numbers(self):
        """
        ⚠️ One builder, two pages. A pledge told "8 of 12 got this right" while
        a chair is told something else is worse than showing him nothing.

        ⚠️ v3.21.5 — THE FIXTURE NOW CROSSES THE MINIMUM-SUBMISSIONS THRESHOLD,
        and it did not before. This test used to build two submissions, which is
        below `PLEDGE_ANALYSIS_MIN_SUBMISSIONS`, so on the fixed tree the pledge
        page has no rows at all and the comparison raised `IndexError`.

        The claim being tested is *when both audiences are shown numbers, the
        numbers agree* — it was never a claim that a pledge is always shown
        them. Raising the fixture keeps that claim intact; the case where they
        deliberately differ has its own module,
        `src/test_quiz_analysis_threshold.py`.
        """
        self.task.show_analysis_to_pledges = True
        self.task.save()
        question = self.task.questions.first()
        PledgeQuizAnswer.objects.create(
            question=question, pledge=self.pledge, answer_text='a', is_correct=True,
        )
        PledgeQuizAnswer.objects.create(
            question=question, pledge=self.other_pledge, answer_text='b', is_correct=False,
        )
        third = make_user('P-3RDSUB', 'Pledge Three', member_type='Pledge')
        PledgeQuizAnswer.objects.create(
            question=question, pledge=third, answer_text='c', is_correct=True,
        )
        # ⚠️ The threshold counts SUBMISSIONS (completion rows), not answers.
        # This fixture used to create answers and no completions at all, so
        # `submissions` was 0 — worth noticing, because it means a quiz can have
        # answers on record and still report nothing taken.
        for pledge in (self.pledge, self.other_pledge, third):
            PledgeTaskCompletion.objects.create(task=self.task, pledge=pledge)

        educator = self.client.get(
            reverse('education_quiz_analysis', args=[self.committee.code, self.task.pk])
        ).context['rows'][0]
        pledge = self.pledge_client.get(self.url).context['rows'][0]

        self.assertEqual(educator['percent'], pledge['percent'])
        self.assertEqual(educator['correct'], pledge['correct'])
        self.assertEqual(educator['wrong'], pledge['wrong'])

    def test_a_non_pledge_is_redirected_from_the_pledge_page(self):
        brother_client = Client()
        brother_client.force_login(self.brother)
        self.assertEqual(brother_client.get(self.url).status_code, 302)
