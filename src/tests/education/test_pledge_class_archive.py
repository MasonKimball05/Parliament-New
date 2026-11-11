"""
11-10-26: pledge classes are frozen at initiation (src/pledge_class_archive.py)
and listed under "Previous Pledge Classes" on the education dashboard. The
education admin can correct a snapshot (logged); the next class's dashboard
stops counting the old class's meetings.
"""
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    EducationMeeting, EducationMeetingAttendance, EducationMemberPermission, Event, PledgeClassArchive,
    PledgePointAdjustment, PledgeTask, PledgeTaskCompletion,
)
from src.tests.education._fixtures import EducationFixtureMixin, make_user
from src.view.committee.education import education_overview
from src.view.officer.manage_members import initiate_pledge_batch


def meeting(committee, chair, when, points=5, title='Chapter history'):
    event = Event.objects.create(title=title, description='', date_time=when, created_by=chair)
    return EducationMeeting.objects.create(event=event, committee=committee, points=points, created_by=chair)


class _Base(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.pledge.pledge_class = 'Fall 2026'
        self.pledge.save()
        self.task = PledgeTask.objects.create(title='Learn the creed', points=10, is_required=True)
        self.quiz = PledgeTask.objects.create(title='History quiz', points=5, max_score=20, is_required=False)
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='completed')
        PledgeTaskCompletion.objects.create(task=self.quiz, pledge=self.pledge, status='completed', score=18)
        self.m1 = meeting(self.committee, self.chair, timezone.now() - timedelta(days=3))
        EducationMeetingAttendance.objects.create(meeting=self.m1, pledge=self.pledge, status='present')
        EducationMeetingAttendance.objects.create(meeting=self.m1, pledge=self.other_pledge, status='absent')
        PledgePointAdjustment.objects.create(committee=self.committee, pledge=self.pledge, current_delta=2,
                                             max_delta=0, reason='Helped set up', created_by=self.chair)

    def initiate(self, *pledges, numbers=None):
        numbers = numbers or {}
        return initiate_pledge_batch(self.chair, {'pledges': [
            {'user_id': p.user_id, 'role_number': numbers.get(p.user_id, str(500 + i))}
            for i, p in enumerate(pledges)]})

    def archive(self):
        return PledgeClassArchive.objects.get(committee=self.committee)


class SnapshotAtInitiationTests(_Base):

    def test_initiation_freezes_the_class(self):
        before = {ps['pledge'].pk: ps for ps in education_overview(self.committee)['pledge_summaries']}
        self.initiate(self.pledge, self.other_pledge, numbers={'P-C7JKZY': '501', 'P-9QW2LM': '502'})
        a = self.archive()
        self.assertEqual(a.name, 'Theta class (Fall 2026)')
        people = {p['user_id']: p for p in a.data['pledges']}
        one = people['P-C7JKZY']
        # The dashboard's own numbers, as they stood.
        self.assertEqual((one['points'], one['max_points']),
                         (before['P-C7JKZY']['points'], before['P-C7JKZY']['max_points']))
        self.assertEqual((one['completed'], one['total'], one['role_number']), (1, 1, '501'))
        self.assertEqual(one['attendance']['present'], 1)
        self.assertEqual(people['P-9QW2LM']['attendance']['absent'], 1)
        cell = a.data['results'][str(self.quiz.pk)]['P-C7JKZY']
        self.assertEqual((cell['status'], cell['score']), ('completed', 18))
        self.assertEqual(a.data['meetings'][0]['attendance'], {'P-C7JKZY': 'present', 'P-9QW2LM': 'absent'})
        self.assertEqual(a.data['adjustments']['P-C7JKZY'][0]['reason'], 'Helped set up')

    def test_snapshot_stays_put_when_the_tasks_change_later(self):
        self.initiate(self.pledge)
        PledgeTask.objects.filter(pk=self.task.pk).update(title='Renamed for the next class', points=99)
        a = self.archive()
        self.assertEqual(next(t for t in a.data['tasks'] if t['id'] == self.task.pk)['title'], 'Learn the creed')

    def test_a_straggler_the_same_week_joins_the_class(self):
        self.initiate(self.pledge, numbers={'P-C7JKZY': '501'})
        self.initiate(self.other_pledge, numbers={'P-9QW2LM': '502'})
        self.assertEqual(PledgeClassArchive.objects.count(), 1)
        a = self.archive()
        self.assertEqual({p['user_id'] for p in a.data['pledges']}, {'P-C7JKZY', 'P-9QW2LM'})
        # He sat the class's meeting before his classmates' initiation: it's still his.
        two = next(p for p in a.data['pledges'] if p['user_id'] == 'P-9QW2LM')
        self.assertEqual(two['attendance']['absent'], 1)
        self.assertEqual(a.data['meetings'][0]['attendance'].get('P-9QW2LM'), 'absent')
        self.assertIn('1 more pledge initiated', a.data['edits'][-1]['what'])

    def test_initiation_goes_ahead_if_the_snapshot_fails(self):
        with mock.patch('src.pledge_class_archive.build_snapshot', side_effect=RuntimeError('boom')):
            self.initiate(self.pledge)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.member_type, 'Member')
        self.assertFalse(PledgeClassArchive.objects.exists())

    def test_next_class_dashboard_drops_the_old_meetings(self):
        self.initiate(self.pledge, self.other_pledge)
        PledgeClassArchive.objects.update(initiated_at=timezone.now() - timedelta(hours=1))
        newbie = make_user('P-NEW111', 'Pledge New', member_type='Pledge')
        ps = next(p for p in education_overview(self.committee)['pledge_summaries'] if p['pledge'].pk == newbie.pk)
        self.assertEqual(ps['max_points'], 10 + 5)   # the two tasks; not the old class's 5-point meeting
        m2 = meeting(self.committee, self.chair, timezone.now() - timedelta(minutes=5))   # after the cutoff
        EducationMeetingAttendance.objects.create(meeting=m2, pledge=newbie, status='present')
        ps = next(p for p in education_overview(self.committee)['pledge_summaries'] if p['pledge'].pk == newbie.pk)
        self.assertEqual((ps['points'], ps['max_points']), (5, 20))


class ArchivePageTests(_Base):

    def setUp(self):
        super().setUp()
        self.initiate(self.pledge, self.other_pledge, numbers={'P-C7JKZY': '501', 'P-9QW2LM': '502'})
        self.a = self.archive()
        self.url = reverse('education_class_archive', args=[self.committee.code, self.a.pk])
        self.edit = reverse('education_class_archive_edit', args=[self.committee.code, self.a.pk])

    def test_dashboard_lists_it_and_the_page_shows_it(self):
        r = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertContains(r, 'Previous Pledge Classes')
        self.assertContains(r, self.url)
        r = self.client.get(self.url)
        self.assertContains(r, 'Theta class (Fall 2026)')
        self.assertContains(r, 'Learn the creed')
        self.assertContains(r, '#501')
        self.assertContains(r, 'Correct a result')

    def test_viewer_reads_but_cannot_edit(self):
        viewer = make_user('9100', 'Viewer')
        EducationMemberPermission.objects.create(committee=self.committee, user=viewer, can_view_submissions=True)
        c = Client()
        c.force_login(viewer)
        r = c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Correct a result')
        self.assertEqual(c.post(self.edit, {'action': 'rename', 'name': 'Hijacked'}).status_code, 404)
        c.force_login(self.brother)   # no education access at all
        self.assertEqual(c.get(self.url).status_code, 404)

    def test_admin_corrections_are_logged(self):
        self.client.post(self.edit, {'action': 'rename', 'name': 'Theta (the best class)', 'notes': 'Great year'})
        self.client.post(self.edit, {'action': 'pledge', 'user_id': 'P-C7JKZY', 'points': '40',
                                     'max_points': '50', 'note': 'Made up a missed meeting'})
        self.client.post(self.edit, {'action': 'result', 'user_id': 'P-9QW2LM', 'task_id': str(self.task.pk),
                                     'status': 'completed', 'score': ''})
        a = PledgeClassArchive.objects.get(pk=self.a.pk)
        self.assertEqual((a.name, a.notes), ('Theta (the best class)', 'Great year'))
        one = next(p for p in a.data['pledges'] if p['user_id'] == 'P-C7JKZY')
        self.assertEqual((one['points'], one['max_points'], one['points_percent']), (40, 50, 80))
        two = next(p for p in a.data['pledges'] if p['user_id'] == 'P-9QW2LM')
        self.assertEqual((two['completed'], two['percent']), (1, 100))   # required task now done
        whats = [e['what'] for e in a.data['edits']]
        self.assertTrue(any('Renamed' in w for w in whats))
        self.assertTrue(any('to 40/50' in w for w in whats))
        self.assertTrue(any('"Learn the creed"' in w for w in whats))
        self.assertContains(self.client.get(self.url), 'Made up a missed meeting')

    def test_bad_corrections_are_refused(self):
        r = self.client.post(self.edit, {'action': 'result', 'user_id': 'P-C7JKZY', 'task_id': str(self.quiz.pk),
                                         'status': 'completed', 'score': '25'}, follow=True)
        self.assertContains(r, 'can&#x27;t be more than 20')
        r = self.client.post(self.edit, {'action': 'pledge', 'user_id': 'P-C7JKZY', 'points': 'lots',
                                         'max_points': '5'}, follow=True)
        self.assertContains(r, 'must be a whole number')
        r = self.client.post(self.edit, {'action': 'pledge', 'user_id': 'nobody', 'points': '1',
                                         'max_points': '5'}, follow=True)
        self.assertContains(r, 'in this class')
        a = PledgeClassArchive.objects.get(pk=self.a.pk)
        self.assertEqual(len(a.data['edits']), 0)

    def test_other_committees_archives_are_not_reachable(self):
        from src.models import Committee
        other = Committee.objects.create(name='Edu 2', code='EDU2', is_active=True, is_education_committee=True)
        other.chairs.add(self.chair)
        r = self.client.get(reverse('education_class_archive', args=['EDU2', self.a.pk]))
        self.assertEqual(r.status_code, 404)
