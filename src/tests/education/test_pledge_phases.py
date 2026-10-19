"""
v3.46.0 — pledge phases, set and advanced from the education dashboard.

Mason, 10-14-26: "there's no way to advance a pledges phase on the education
dashboard." Tasks and page restrictions were already written per phase, but
no pledge had a phase (`pledge_phase` was scrapped 06-24-26), so every pledge
counted as 'all': Phase 2/3 tasks showed to everyone from day one, and a page
ticked for "Phase 1" only was blocked for everyone.

Mason's decisions (10-14-26): per-pledge phase plus an "advance all" button;
the phase unlocks that phase's tasks (and earlier ones) and the pages ticked
for it.
"""
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from src import pledge_phases
from src.models import EducationMemberPermission, ParliamentUser, PledgePageRestriction, PledgeTask, PledgeTaskQuestion
from src.tests.education._fixtures import EducationFixtureMixin, make_user


class PhaseRuleTests(SimpleTestCase):

    def test_unlocked_task_phases_are_cumulative(self):
        self.assertEqual(pledge_phases.unlocked_task_phases('1'), ['all', '1'])
        self.assertEqual(pledge_phases.unlocked_task_phases('2'), ['all', '1', '2'])
        self.assertEqual(pledge_phases.unlocked_task_phases('3'), ['all', '1', '2', '3'])

    def test_bad_values_count_as_the_first_phase(self):
        for junk in (None, '', 'all', '7', 2.5):
            self.assertEqual(pledge_phases.unlocked_task_phases(junk), ['all', '1'])

    def test_next_phase_stops_at_the_last(self):
        self.assertEqual(pledge_phases.next_phase('1'), '2')
        self.assertEqual(pledge_phases.next_phase('2'), '3')
        self.assertIsNone(pledge_phases.next_phase('3'))

    def test_model_choices_match_the_phase_list(self):
        field = ParliamentUser._meta.get_field('pledge_phase')
        self.assertEqual(list(field.choices), pledge_phases.PHASE_CHOICES)
        self.assertEqual(field.default, pledge_phases.FIRST_PHASE)

    def test_task_phase_choices_cover_every_pledge_phase(self):
        task_phases = {value for value, _ in PledgeTask.PHASE_CHOICES}
        self.assertEqual(task_phases, {'all', *pledge_phases.PHASES})


class TaskUnlockTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.general = PledgeTask.objects.create(title='General task', phase='all', activation_mode='immediate')
        self.p1 = PledgeTask.objects.create(title='Phase one task', phase='1', activation_mode='immediate')
        self.p2 = PledgeTask.objects.create(title='Phase two task', phase='2', activation_mode='immediate')
        self.p3 = PledgeTask.objects.create(title='Phase three task', phase='3', activation_mode='immediate')
        self.client.force_login(self.pledge)

    def titles(self):
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.status_code, 200)
        return {item['task'].title for group in response.context['phase_groups'] for item in group['items']}

    def test_a_new_pledge_starts_in_phase_one(self):
        self.assertEqual(self.pledge.pledge_phase, '1')

    def test_phase_one_sees_general_and_phase_one_only(self):
        self.assertEqual(self.titles(), {'General task', 'Phase one task'})

    def test_advancing_unlocks_the_next_phase_and_keeps_earlier_ones(self):
        self.pledge.pledge_phase = '2'
        self.pledge.save()
        self.assertEqual(self.titles(), {'General task', 'Phase one task', 'Phase two task'})
        self.pledge.pledge_phase = '3'
        self.pledge.save()
        self.assertEqual(self.titles(), {'General task', 'Phase one task', 'Phase two task', 'Phase three task'})

    def test_my_tasks_shows_the_phase(self):
        self.pledge.pledge_phase = '2'
        self.pledge.save()
        self.assertContains(self.client.get(reverse('my_pledge_tasks')), 'Phase 2')

    def test_a_locked_quiz_cannot_be_opened(self):
        quiz = PledgeTask.objects.create(title='Phase two quiz', task_type='quiz', phase='2',
                                         activation_mode='immediate')
        PledgeTaskQuestion.objects.create(task=quiz, question_text='Q?', display_order=1)
        response = self.client.get(reverse('pledge_take_quiz', args=[quiz.pk]))
        self.assertTemplateUsed(response, 'pledge/quiz_not_available.html')
        self.pledge.pledge_phase = '2'
        self.pledge.save()
        response = self.client.get(reverse('pledge_take_quiz', args=[quiz.pk]))
        self.assertTemplateNotUsed(response, 'pledge/quiz_not_available.html')

    def test_chair_preview_matches_the_pledges_phase(self):
        self.client.force_login(self.chair)
        response = self.client.get(reverse('education_preview_pledge_tasks', args=[self.committee.code, self.pledge.pk]))
        self.assertEqual(response.status_code, 200)
        titles = {i['task'].title for g in response.context['phase_groups'] for i in g['items']}
        self.assertNotIn('Phase two task', titles)


class PageUnlockTests(EducationFixtureMixin, TestCase):
    """`chapter_documents` is gated by @pledge_page_allowed."""

    def setUp(self):
        self.build()
        cache.clear()
        self.addCleanup(cache.clear)
        PledgePageRestriction.objects.update_or_create(
            url_name='chapter_documents', defaults={'allowed_phases': ['2', '3']})
        PledgePageRestriction.invalidate_cache('chapter_documents')
        self.client.force_login(self.pledge)

    def status(self):
        return self.client.get(reverse('chapter_documents')).status_code

    def test_a_page_ticked_for_later_phases_is_blocked_in_phase_one(self):
        self.assertEqual(self.status(), 403)

    def test_it_opens_once_the_pledge_reaches_that_phase(self):
        self.pledge.pledge_phase = '2'
        self.pledge.save()
        self.assertNotEqual(self.status(), 403)


class SetPhaseViewTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def url(self, pledge=None):
        return reverse('education_set_pledge_phase', args=[self.committee.code, (pledge or self.pledge).pk])

    def test_chair_sets_a_phase(self):
        response = self.client.post(self.url(), {'phase': '2'})
        self.assertRedirects(response, reverse('education_home', args=[self.committee.code]) + '#pledge-progress',
                             fetch_redirect_response=False)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '2')

    def test_a_phase_can_be_moved_back(self):
        self.pledge.pledge_phase = '3'
        self.pledge.save()
        self.client.post(self.url(), {'phase': '1'})
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '1')

    def test_junk_phase_changes_nothing(self):
        for junk in ('4', 'all', '', '1; DROP'):
            self.client.post(self.url(), {'phase': junk})
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '1')

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_member_without_education_access_gets_404(self):
        self.client.force_login(self.brother)
        self.assertEqual(self.client.post(self.url(), {'phase': '2'}).status_code, 404)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '1')

    def test_view_only_permission_cannot_change_a_phase(self):
        EducationMemberPermission.objects.create(committee=self.committee, user=self.brother,
                                                 can_view_submissions=True)
        self.client.force_login(self.brother)
        self.assertEqual(self.client.post(self.url(), {'phase': '2'}).status_code, 403)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '1')

    def test_a_pledge_cannot_change_his_own_phase(self):
        self.client.force_login(self.pledge)
        self.assertEqual(self.client.post(self.url(), {'phase': '3'}).status_code, 404)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '1')

    def test_a_non_pledge_is_404(self):
        self.assertEqual(self.client.post(self.url(self.brother), {'phase': '2'}).status_code, 404)


class AdvanceAllViewTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.third = make_user('P-ZZ3PH3', 'Pledge Three', member_type='Pledge', pledge_phase='3')
        self.url = reverse('education_advance_all_phases', args=[self.committee.code])

    def phases(self):
        return {u.pk: u.pledge_phase for u in ParliamentUser.objects.filter(member_type='Pledge')}

    def test_everyone_moves_up_one_and_the_last_phase_stays(self):
        self.other_pledge.pledge_phase = '2'
        self.other_pledge.save()
        self.client.post(self.url)
        self.assertEqual(self.phases(), {self.pledge.pk: '2', self.other_pledge.pk: '3', self.third.pk: '3'})

    def test_non_pledges_are_untouched(self):
        self.client.post(self.url)
        self.brother.refresh_from_db()
        self.assertEqual(self.brother.pledge_phase, '1')

    def test_inactive_pledges_are_untouched(self):
        self.other_pledge.is_active = False
        self.other_pledge.save()
        self.client.post(self.url)
        self.other_pledge.refresh_from_db()
        self.assertEqual(self.other_pledge.pledge_phase, '1')

    def test_needs_manage_permission(self):
        self.client.force_login(self.brother)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(self.phases()[self.pledge.pk], '1')

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class DashboardControlsTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def test_managers_get_the_controls(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertContains(response, reverse('education_advance_all_phases', args=[self.committee.code]))
        self.assertContains(response, reverse('education_set_pledge_phase', args=[self.committee.code, self.pledge.pk]))

    def test_view_only_members_see_the_phase_but_no_controls(self):
        EducationMemberPermission.objects.create(committee=self.committee, user=self.brother,
                                                 can_view_submissions=True)
        self.client.force_login(self.brother)
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertNotContains(response, reverse('education_advance_all_phases', args=[self.committee.code]))
        self.assertContains(response, 'Phase 1')


class ChairSideCountsFollowThePhaseTests(EducationFixtureMixin, TestCase):
    """
    v3.46.1 — v3.46.0 hid locked tasks from the pledge but the chair-side
    numbers still counted them: the dashboard's "points so far" max included
    live Phase 2/3 tasks a Phase 1 pledge cannot open, and the pledge detail
    page called a locked task "overdue".
    """

    def setUp(self):
        self.build()
        import datetime
        past = datetime.date.today() - datetime.timedelta(days=7)
        self.p1 = PledgeTask.objects.create(title='Phase one task', phase='1', points=10,
                                            activation_mode='immediate', due_date=past)
        self.p2 = PledgeTask.objects.create(title='Phase two task', phase='2', points=30,
                                            activation_mode='immediate', due_date=past)

    def summary(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        return next(ps for ps in response.context['pledge_summaries'] if ps['pledge'].pk == self.pledge.pk)

    def overdue_titles(self):
        response = self.client.get(reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk]))
        self.assertEqual(response.status_code, 200)
        return {row['task'].title for row in response.context['overdue_rows']}

    def test_points_max_leaves_out_locked_tasks(self):
        self.assertEqual(self.summary()['max_points'], 10)

    def test_points_max_grows_when_the_phase_unlocks(self):
        self.pledge.pledge_phase = '2'
        self.pledge.save()
        self.assertEqual(self.summary()['max_points'], 40)

    def test_a_locked_task_is_not_overdue(self):
        self.assertEqual(self.overdue_titles(), {'Phase one task'})

    def test_it_is_overdue_once_unlocked(self):
        self.pledge.pledge_phase = '2'
        self.pledge.save()
        self.assertEqual(self.overdue_titles(), {'Phase one task', 'Phase two task'})
