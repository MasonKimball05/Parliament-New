"""
v3.31.5 — Mason relayed a live bug report on the education dashboard:

    "I cannot edit tasks after I make them, nor can I change them from
    pending to complete to incomplete. I also cannot add questions to the
    quizzes. I also cannot set due dates."

Investigation found four distinct root causes, none of them a recurrence of
the v3.31.1 CSP/onclick bug fixed earlier the same day:

1. **"Cannot set due dates"** — `PledgeTask.due_date` has existed on the model
   since it was added and is even rendered on the dashboard grid
   (`{% if row.task.due_date %}Due {{ ... }}{% endif %}`), but the Add Task
   modal had no input for it and `education_add_task` never read it from
   POST. There was no migration to write — only UI and view code were
   missing.
2. **"Cannot edit tasks after I make them"** — there was no
   `education_edit_task` view, URL or template at all. Only Add, Duplicate,
   Delete and Publish/Unpublish existed. Fixed by mirroring
   `education_edit_meeting`'s shape exactly: a shared `_apply_task_fields()`
   helper (mirroring `_apply_meeting_fields`) used by both the Add Task modal
   and a new full `education_task_form.html` edit page, fed by a shared
   `_education_task_fields.html` partial (mirroring
   `_education_meeting_fields.html`).
3. **"Cannot add questions to the quizzes"** — `education_add_quiz_question`
   and `education_delete_quiz_question` were fully implemented and correct;
   there was simply no template anywhere that called either one. Fixed with
   a GET-only `education_manage_quiz_questions` page that posts to those same
   two existing endpoints via `Parliament.post()`.
4. **"Cannot change pending → complete → incomplete"** — already fixed by the
   earlier v3.31.1 CSP work in this session (the `toggle-completion`
   data-action wiring). Re-verified here rather than taken on faith.

These tests cover 1–3 (new code) and re-confirm 4 (already-fixed code) at the
rendered-page level, the same way `test_education_dashboard_button_wiring.py`
verifies the CSP fix by rendering the actual page rather than reading the
template source.
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from src.models import PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion
from src.tests.education._fixtures import EducationFixtureMixin, make_user


class AddTaskDueDateTests(EducationFixtureMixin, TestCase):
    """Root cause 1 — the Add Task modal/view never read due_date at all."""

    def setUp(self):
        self.build()
        self.url = reverse('education_add_task', args=[self.committee.code])

    def test_add_task_modal_has_a_due_date_field(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn('name="due_date"', html)

    def test_due_date_is_saved_on_create(self):
        self.client.post(self.url, {'title': 'Read the bylaws', 'due_date': '2027-03-01'})
        task = PledgeTask.objects.get(title='Read the bylaws')
        self.assertEqual(task.due_date, date(2027, 3, 1))

    def test_blank_due_date_saves_as_none(self):
        self.client.post(self.url, {'title': 'No deadline'})
        task = PledgeTask.objects.get(title='No deadline')
        self.assertIsNone(task.due_date)

    def test_a_malformed_due_date_is_ignored_rather_than_500ing(self):
        response = self.client.post(self.url, {'title': 'Bad date', 'due_date': 'not-a-date'})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(PledgeTask.objects.get(title='Bad date').due_date)


class EditTaskViewTests(EducationFixtureMixin, TestCase):
    """Root cause 2 — there was no edit view, URL or template at all."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(
            title='Original Title',
            description='Original description',
            task_type='task',
            phase='1',
            due_date=date(2027, 1, 15),
            points=5,
            max_score=None,
            is_required=True,
            display_order=2,
            created_by=self.chair,
        )
        self.url = reverse('education_edit_task', args=[self.committee.code, self.task.pk])

    def test_get_returns_200(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_get_prefills_the_existing_values(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('value="Original Title"', html)
        self.assertIn('value="2027-01-15"', html)

    def test_a_non_officer_non_chair_gets_404(self):
        self.client.force_login(self.brother)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_post_updates_the_task(self):
        response = self.client.post(self.url, {
            'title': 'Updated Title',
            'description': 'Updated description',
            'task_type': 'reading',
            'phase': '2',
            'due_date': '2027-06-01',
            'points': '10',
            'display_order': '3',
            'activation_mode': 'immediate',
        })
        self.assertRedirects(response, reverse('education_home', args=[self.committee.code]))
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Updated Title')
        self.assertEqual(self.task.description, 'Updated description')
        self.assertEqual(self.task.task_type, 'reading')
        self.assertEqual(self.task.phase, '2')
        self.assertEqual(self.task.due_date, date(2027, 6, 1))
        self.assertEqual(self.task.points, 10)
        self.assertEqual(self.task.display_order, 3)

    def test_due_date_can_be_cleared_via_edit(self):
        self.client.post(self.url, {'title': 'Original Title', 'activation_mode': 'immediate'})
        self.task.refresh_from_db()
        self.assertIsNone(self.task.due_date)

    def test_a_blank_title_is_rejected_and_nothing_is_saved(self):
        response = self.client.post(self.url, {'title': '', 'due_date': '2030-01-01'})
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Original Title')
        self.assertNotEqual(self.task.due_date, date(2030, 1, 1))

    def test_editing_does_not_touch_a_pledges_recorded_completion(self):
        """
        ⚠️ THE POINT OF THE VIEW. Deleting-and-recreating a task to "fix" it
        would CASCADE and destroy this row — that is exactly why an edit path
        exists at all.
        """
        completion = PledgeTaskCompletion.objects.create(
            task=self.task, pledge=self.pledge, status='completed', score=None,
        )
        self.client.post(self.url, {'title': 'Renamed', 'activation_mode': 'immediate'})
        completion.refresh_from_db()
        self.assertEqual(completion.status, 'completed')
        self.assertEqual(completion.task_id, self.task.pk)

    def test_assigned_pledges_can_be_cleared_via_edit(self):
        self.task.assigned_to.set([self.pledge])
        self.assertEqual(list(self.task.assigned_to.all()), [self.pledge])
        self.client.post(self.url, {'title': 'Original Title', 'activation_mode': 'immediate'})
        self.assertEqual(list(self.task.assigned_to.all()), [])

    def test_assigned_pledges_can_be_changed_via_edit(self):
        self.client.post(self.url, {
            'title': 'Original Title', 'activation_mode': 'immediate',
            'assigned_to': [self.other_pledge.pk],
        })
        self.assertEqual(list(self.task.assigned_to.all()), [self.other_pledge])

    def test_switching_to_immediate_forces_published_true(self):
        self.task.activation_mode = 'manual'
        self.task.is_published = False
        self.task.save()
        self.client.post(self.url, {'title': 'Original Title', 'activation_mode': 'immediate'})
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_published)

    def test_editing_a_published_manual_task_does_not_silently_unpublish_it(self):
        """
        ⚠️ THE REGRESSION THIS GUARDS AGAINST. `education_add_task` sets
        `is_published=False` for a brand-new manual/timed task on purpose —
        it starts as a draft. If `_apply_task_fields` (shared by add AND
        edit) applied that same "manual/timed => False" rule unconditionally,
        editing an unrelated field on an ALREADY-PUBLISHED manual task would
        silently hide it from pledges again the moment a chair fixed a typo.
        """
        self.task.activation_mode = 'manual'
        self.task.is_published = True
        self.task.save()
        self.client.post(self.url, {
            'title': 'Fixed a typo', 'activation_mode': 'manual',
        })
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_published, 'editing unpublished an already-published manual task')
        self.assertEqual(self.task.title, 'Fixed a typo')


class EditTaskAndQuestionsLinksOnTheGridTests(EducationFixtureMixin, TestCase):
    """The dashboard grid must actually link to the new pages."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Plain Task')
        self.quiz = PledgeTask.objects.create(title='A Quiz', task_type='quiz')

    def test_a_chair_sees_an_edit_link_for_every_task(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn(reverse('education_edit_task', args=[self.committee.code, self.task.pk]), html)
        self.assertIn(reverse('education_edit_task', args=[self.committee.code, self.quiz.pk]), html)

    def test_a_non_chair_officer_sees_no_edit_link(self):
        officer = make_user('9005', 'Non-chair Officer', member_type='Officer')
        client = Client()
        client.force_login(officer)
        html = client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertNotIn(reverse('education_edit_task', args=[self.committee.code, self.task.pk]), html)

    def test_only_the_quiz_task_links_to_manage_questions(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn(reverse('education_manage_quiz_questions', args=[self.committee.code, self.quiz.pk]), html)
        self.assertNotIn(reverse('education_manage_quiz_questions', args=[self.committee.code, self.task.pk]), html)

    def test_no_inline_event_handlers_leaked_onto_the_new_edit_page(self):
        """Same CSP trace as v3.31.1 — this page is new, so it gets its own check."""
        import re
        url = reverse('education_edit_task', args=[self.committee.code, self.task.pk])
        html = self.client.get(url).content.decode()
        self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', html, re.IGNORECASE))


class ManageQuizQuestionsPageTests(EducationFixtureMixin, TestCase):
    """Root cause 3 — the backend existed; there was no page."""

    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        self.plain_task = PledgeTask.objects.create(title='Plain Task')
        self.url = reverse('education_manage_quiz_questions', args=[self.committee.code, self.quiz.pk])

    def test_get_returns_200(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_a_non_quiz_task_404s(self):
        bad_url = reverse('education_manage_quiz_questions', args=[self.committee.code, self.plain_task.pk])
        self.assertEqual(self.client.get(bad_url).status_code, 404)

    def test_existing_questions_are_listed(self):
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='Who founded the chapter?')
        html = self.client.get(self.url).content.decode()
        self.assertIn('Who founded the chapter?', html)

    def test_the_add_endpoint_is_reachable_and_the_new_question_then_appears(self):
        """
        End-to-end, not just "the view exists": call the real endpoint the
        page's JS posts to, the same way `Parliament.post()` would, then
        confirm the manage page reflects it on reload.
        """
        add_url = reverse('education_add_quiz_question', args=[self.committee.code, self.quiz.pk])
        response = self.client.post(add_url, {
            'question_text': 'What year was it founded?',
            'answer_hint': '1839',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PledgeTaskQuestion.objects.filter(task=self.quiz).count(), 1)

        html = self.client.get(self.url).content.decode()
        self.assertIn('What year was it founded?', html)

    def test_the_delete_endpoint_is_reachable_and_the_question_then_disappears(self):
        question = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='Delete me')
        delete_url = reverse(
            'education_delete_quiz_question', args=[self.committee.code, self.quiz.pk, question.pk]
        )
        response = self.client.post(delete_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PledgeTaskQuestion.objects.filter(pk=question.pk).exists())

    def test_a_non_officer_non_chair_gets_404(self):
        self.client.force_login(self.brother)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_no_inline_event_handlers(self):
        import re
        html = self.client.get(self.url).content.decode()
        self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', html, re.IGNORECASE))


class EditQuizQuestionTests(EducationFixtureMixin, TestCase):
    """
    v3.31.6 — Mason: "for quizzes you cannot edit the questions after making
    them can we change that." Add and Delete existed; there was no way to
    fix a typo short of deleting and re-adding the question (losing its
    `display_order` and CASCADEing any pledge answers already submitted).
    """

    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        self.question = PledgeTaskQuestion.objects.create(
            task=self.quiz, question_text='Who fouded the chapter?',
            answer_hint='Wrong spelling on purpose', display_order=0,
        )
        self.url = reverse(
            'education_edit_quiz_question', args=[self.committee.code, self.quiz.pk, self.question.pk]
        )
        self.manage_url = reverse('education_manage_quiz_questions', args=[self.committee.code, self.quiz.pk])

    def test_editing_updates_the_question(self):
        response = self.client.post(self.url, {
            'question_text': 'Who founded the chapter?',
            'answer_hint': 'John Founder, 1839',
            'display_order': '2',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.question.refresh_from_db()
        self.assertEqual(self.question.question_text, 'Who founded the chapter?')
        self.assertEqual(self.question.answer_hint, 'John Founder, 1839')
        self.assertEqual(self.question.display_order, 2)

    def test_the_edited_text_appears_on_the_manage_page(self):
        self.client.post(self.url, {
            'question_text': 'Who founded the chapter?', 'answer_hint': '', 'display_order': '0',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        html = self.client.get(self.manage_url).content.decode()
        self.assertIn('Who founded the chapter?', html)
        self.assertNotIn('Who fouded the chapter?', html)

    def test_editing_does_not_touch_a_pledges_existing_answer(self):
        from src.models import PledgeQuizAnswer
        answer = PledgeQuizAnswer.objects.create(
            question=self.question, pledge=self.pledge, answer_text='1839', is_correct=True,
        )
        self.client.post(self.url, {
            'question_text': 'Who founded the chapter?', 'answer_hint': '', 'display_order': '0',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        answer.refresh_from_db()
        self.assertEqual(answer.answer_text, '1839')
        self.assertTrue(answer.is_correct)

    def test_blank_question_text_is_rejected(self):
        response = self.client.post(self.url, {
            'question_text': '', 'answer_hint': '', 'display_order': '0',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.question.refresh_from_db()
        self.assertEqual(self.question.question_text, 'Who fouded the chapter?')

    def test_a_non_officer_non_chair_gets_404(self):
        self.client.force_login(self.brother)
        response = self.client.post(self.url, {
            'question_text': 'Hijacked', 'answer_hint': '', 'display_order': '0',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 404)
        self.question.refresh_from_db()
        self.assertEqual(self.question.question_text, 'Who fouded the chapter?')

    def test_the_manage_page_has_edit_buttons_and_inline_edit_forms(self):
        html = self.client.get(self.manage_url).content.decode()
        self.assertIn('data-action="edit-question"', html)
        self.assertIn(f'id="question-edit-{self.question.pk}"', html)
        self.assertIn('data-action="cancel-edit-question"', html)

    def test_no_inline_event_handlers_on_the_manage_page(self):
        import re
        html = self.client.get(self.manage_url).content.decode()
        self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', html, re.IGNORECASE))


class ToggleCompletionIsGenuinelyWiredTests(EducationFixtureMixin, TestCase):
    """
    Root cause 4 — Mason also reported being unable to change a task's status
    from pending to complete to incomplete. Investigation concluded this was
    already fixed by the earlier v3.31.1 CSP/data-action work in this same
    session, not a new bug. Re-verified here at the rendered-page level
    (mirroring test_education_dashboard_button_wiring.py) rather than taken
    on faith, since nothing in that session's changelog specifically named
    this symptom as closed.
    """

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Ritual Exam')

    def test_the_grid_cell_has_the_correct_data_action_wiring(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn('data-action="toggle-completion"', html)
        self.assertIn(f'data-task-pk="{self.task.pk}"', html)
        self.assertIn(f'data-pledge-pk="{self.pledge.pk}"', html)

    def test_the_delegated_listener_dispatches_toggle_completion(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn("case 'toggle-completion':", html)
        self.assertIn('toggleCompletion(COMMITTEE_CODE, el.dataset.taskPk, el.dataset.pledgePk, el);', html)

    def test_clicking_through_the_real_endpoint_cycles_pending_to_completed_to_incomplete(self):
        url = reverse('education_toggle_completion', args=[self.committee.code, self.task.pk, self.pledge.pk])
        first = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(first.json()['status'], 'completed')
        second = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(second.json()['status'], 'incomplete')
        third = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(third.json()['status'], 'pending')


class CreatingAQuizRedirectsStraightToItsQuestionsTests(EducationFixtureMixin, TestCase):
    """
    v3.31.6 — Mason, mid-session, about the fix for root cause 3 above: "when
    a user makes a quiz can we just give them another popup/redirect to make
    the quiz instead of them having to go find it in the tasks?" A brand-new
    quiz has zero questions and is useless to pledges until someone adds at
    least one, so `education_add_task` now sends a quiz straight to its own
    Manage Questions page instead of back to the dashboard.
    """

    def setUp(self):
        self.build()
        self.url = reverse('education_add_task', args=[self.committee.code])

    def test_creating_a_quiz_redirects_to_its_manage_questions_page(self):
        response = self.client.post(self.url, {'title': 'Founders Quiz', 'task_type': 'quiz'})
        task = PledgeTask.objects.get(title='Founders Quiz')
        self.assertRedirects(
            response,
            reverse('education_manage_quiz_questions', args=[self.committee.code, task.pk]) + '?created=1',
        )

    def test_creating_a_non_quiz_task_still_redirects_to_the_dashboard(self):
        response = self.client.post(self.url, {'title': 'Read a chapter', 'task_type': 'reading'})
        self.assertRedirects(response, reverse('education_home', args=[self.committee.code]))

    def test_the_manage_questions_page_shows_a_just_created_banner(self):
        response = self.client.post(self.url, {'title': 'Founders Quiz', 'task_type': 'quiz'}, follow=True)
        self.assertContains(response, '"Founders Quiz" was created.')

    def test_visiting_manage_questions_directly_shows_no_banner(self):
        task = PledgeTask.objects.create(title='Existing Quiz', task_type='quiz')
        html = self.client.get(
            reverse('education_manage_quiz_questions', args=[self.committee.code, task.pk])
        ).content.decode()
        self.assertNotIn('was created.', html)
