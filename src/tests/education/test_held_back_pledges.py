"""
11-11-26 (Mason's answers on pledge class edge cases):

1. Whoever holds the education committee's role (the VP of Education, when
   there is one) has a chair's access.
2. A pledge becomes a member through Initiate Pledges: the member editors
   refuse it unless "Initiate anyway" is ticked, and then save his class.
5. The education chair can remove (and restore) a member on a class page.
6. A task can be re-required for a held-back pledge, keeping the earlier
   attempt (and quiz answers).
4/7. At the start of a semester, held-back pledges are recorded on the last
   semester's class page; with the reset option on, their points restart.
"""
import datetime as dt
import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    EducationMeetingAttendance, PledgeClassArchive, PledgePointAdjustment, PledgeQuizAnswer, PledgeTask,
    PledgeTaskAttempt, PledgeTaskCompletion, PledgeTaskQuestion, Role,
)
from src.pledge_class_archive import pledge_type_change_error, save_held_back, set_reset_enabled
from src.tests.education._fixtures import EducationFixtureMixin, make_user
from src.tests.education.test_pledge_class_archive import meeting
from src.view.committee.education import _get_education_access, education_overview
from src.view.officer.manage_members import MemberChangeRefused, apply_member_edit


def aware(*args):
    return timezone.make_aware(dt.datetime(*args))


class _Base(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Learn the creed', points=10)
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='completed')

    def summary(self, pledge):
        return next(ps for ps in education_overview(self.committee)['pledge_summaries'] if ps['pledge'].pk == pledge.pk)


class VicePresidentAccessTests(_Base):

    def test_role_holder_has_a_chairs_access(self):
        vpe = make_user('9200', 'Vice Pres Ed')
        role = Role.objects.create(name='Vice President of Education', code='VPE')
        self.committee.role = role
        self.committee.save()
        self.assertFalse(_get_education_access(vpe, self.committee)['is_full_access'])
        vpe.roles.add(role)
        access = _get_education_access(vpe, self.committee)
        self.assertTrue(access['is_full_access'] and access['is_chair'])


class InitiateAnywayTests(_Base):

    def test_member_editor_refuses_then_allows_with_a_snapshot(self):
        with self.assertRaises(MemberChangeRefused) as ctx:
            apply_member_edit(self.chair, self.pledge, {'member_type': 'Member'})
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn('Initiate Pledges', ctx.exception.message)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.member_type, 'Pledge')
        self.chair.is_admin = True
        self.chair.save()
        apply_member_edit(self.chair, self.pledge, {'member_type': 'Member', 'confirm_skip_initiation': True})
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.member_type, 'Member')
        self.assertTrue(any(p['user_id'] == 'P-C7JKZY'
                            for p in PledgeClassArchive.objects.get().data['pledges']))

    def test_only_pledge_to_something_else_is_checked(self):
        self.assertEqual(pledge_type_change_error(self.brother, 'Officer', False), '')
        self.assertEqual(pledge_type_change_error(self.pledge, 'Pledge', False), '')

    def test_web_edit_member_shows_the_reason(self):
        self.chair.is_admin = True
        self.chair.save()
        r = self.client.post(reverse('edit_member', args=[self.pledge.pk]), json.dumps({
            'name': self.pledge.name, 'member_type': 'Member', 'member_status': 'Active'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 409)
        self.assertIn('Initiate anyway', r.json()['error'])


class RemoveFromClassTests(_Base):

    def test_remove_and_restore(self):
        from src.view.officer.manage_members import initiate_pledge_batch
        initiate_pledge_batch(self.chair, {'pledges': [{'user_id': 'P-C7JKZY', 'role_number': '601'},
                                                       {'user_id': 'P-9QW2LM', 'role_number': '602'}]})
        a = PledgeClassArchive.objects.get()
        edit = reverse('education_class_archive_edit', args=[self.committee.code, a.pk])
        self.client.post(edit, {'action': 'remove', 'user_id': 'P-9QW2LM'})
        a.refresh_from_db()
        self.assertEqual([p['user_id'] for p in a.data['pledges']], ['P-C7JKZY'])
        self.assertEqual(a.data['removed'][0]['pledge']['name'], 'Pledge Two')
        self.assertContains(self.client.get(reverse('education_class_archive', args=[self.committee.code, a.pk])),
                            'Restore')
        self.client.post(edit, {'action': 'restore', 'user_id': 'P-9QW2LM'})
        a.refresh_from_db()
        self.assertEqual({p['user_id'] for p in a.data['pledges']}, {'P-C7JKZY', 'P-9QW2LM'})
        self.assertEqual(a.data['removed'], [])


class RerequireTests(_Base):

    def test_rerequire_keeps_the_earlier_attempt_and_answers(self):
        quiz = PledgeTask.objects.create(title='History quiz', task_type='quiz', points=5, max_score=10)
        q = PledgeTaskQuestion.objects.create(task=quiz, question_text='Founded when?')
        PledgeQuizAnswer.objects.create(question=q, pledge=self.pledge, answer_text='1839', is_correct=True)
        PledgeTaskCompletion.objects.create(task=quiz, pledge=self.pledge, status='completed', score=9, notes='Good')
        url = reverse('education_rerequire_task', args=[self.committee.code, self.pledge.pk])
        self.client.post(url, {'task_pk': quiz.pk})
        self.assertFalse(PledgeTaskCompletion.objects.filter(task=quiz, pledge=self.pledge).exists())
        self.assertFalse(PledgeQuizAnswer.objects.filter(pledge=self.pledge).exists())
        a = PledgeTaskAttempt.objects.get(task=quiz, pledge=self.pledge)
        self.assertEqual((a.status, a.score, a.notes), ('completed', 9, 'Good'))
        self.assertEqual(a.answers, [{'question': 'Founded when?', 'answer': '1839', 'is_correct': True}])
        page = self.client.get(reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk]))
        self.assertContains(page, 'Earlier (Fall 2026)')
        # "Re-require all" catches the rest.
        self.client.post(url, {'all': '1'})
        self.assertFalse(PledgeTaskCompletion.objects.filter(pledge=self.pledge).exists())
        self.assertEqual(PledgeTaskAttempt.objects.filter(pledge=self.pledge).count(), 2)

    def test_nothing_to_rerequire(self):
        url = reverse('education_rerequire_task', args=[self.committee.code, self.other_pledge.pk])
        r = self.client.post(url, {'task_pk': self.task.pk}, follow=True)
        self.assertContains(r, 'nothing to re-require')

    def test_viewer_cannot_rerequire(self):
        from src.models import EducationMemberPermission
        viewer = make_user('9300', 'Viewer')
        EducationMemberPermission.objects.create(committee=self.committee, user=viewer, can_view_submissions=True)
        self.client.force_login(viewer)
        url = reverse('education_rerequire_task', args=[self.committee.code, self.pledge.pk])
        self.assertEqual(self.client.post(url, {'task_pk': self.task.pk}).status_code, 404)


class NewSemesterTests(_Base):
    """'Now' is January 2 2027: Fall 2026 has ended."""

    def setUp(self):
        super().setUp()
        self.now = aware(2027, 1, 2, 12)
        self.fall_meeting = meeting(self.committee, self.chair, aware(2026, 12, 1, 19), points=5)
        EducationMeetingAttendance.objects.create(meeting=self.fall_meeting, pledge=self.pledge, status='present')
        self.newbie = make_user('P-NEW222', 'Spring Pledge', member_type='Pledge')

    def test_held_back_pledge_goes_on_last_semesters_page(self):
        self.assertEqual(save_held_back(self.now), 1)
        a = PledgeClassArchive.objects.get(term='Fall 2026')
        self.assertTrue(a.data['no_initiations'])
        held = a.data['held_back'][0]
        self.assertEqual(held['pledge']['user_id'], 'P-C7JKZY')
        self.assertEqual((held['pledge']['points'], held['pledge']['max_points']), (15, 15))
        self.assertEqual(held['meetings'][0]['status'], 'present')
        self.assertFalse(held['points_reset'])
        self.assertEqual(save_held_back(self.now), 0)        # daily: nothing twice
        r = self.client.get(reverse('education_class_archive', args=[self.committee.code, a.pk]))
        self.assertContains(r, 'Held Back')
        self.assertContains(r, 'No one was initiated this semester')

    def test_reset_option_zeroes_his_points_and_rerequire_backs_out_of_it(self):
        set_reset_enabled(True, self.chair)
        save_held_back(self.now)
        adj = PledgePointAdjustment.objects.get(pledge=self.pledge, reason__startswith='Semester reset: ')
        self.assertEqual((adj.current_delta, adj.max_delta), (-15, -15))
        self.assertIn('reached 15/15 in Fall 2026', adj.reason)
        ps = self.summary(self.pledge)
        self.assertEqual((ps['points'], ps['max_points']), (0, 0))
        # Re-requiring his creed task: it's open again, worth 10, earned 0.
        PledgePointAdjustment.objects.filter(pk=adj.pk).update(created_at=timezone.now() + dt.timedelta(minutes=1))
        url = reverse('education_rerequire_task', args=[self.committee.code, self.pledge.pk])
        self.client.post(url, {'task_pk': self.task.pk})
        ps = self.summary(self.pledge)
        self.assertEqual((ps['points'], ps['max_points']), (0, 10))

    def test_setting_toggle_is_for_the_education_admin(self):
        url = reverse('education_held_back_setting', args=[self.committee.code])
        self.client.post(url, {'reset': 'on'})
        from src.pledge_class_archive import reset_enabled
        self.assertTrue(reset_enabled())
        self.client.post(url, {})
        self.assertFalse(reset_enabled())
