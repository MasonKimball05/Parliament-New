"""
Recruitment dashboard "Assignments" tab — v3.29.0.

Requested by Mason: "recruitment dashboard [needs] a counter part that
allows for better viewing of assignments for prospects. Currently I only
see a way to assign, but not a good way to really view who has who and
whatnot." The existing "Candidates" tab already has an Assigned To
column, but answering "how many prospects does each of us have" meant
scrolling and counting by eye. `_group_candidates_by_assignee` groups the
same queryset the dashboard already fetches, so the new tab costs no
extra queries — see its docstring in src/view/committee/recruitment.py.
"""
from django.test import TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser, RecruitmentCandidate
from src.view.committee.recruitment import _group_candidates_by_assignee


def _member(user_id, name, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=name, username=user_id, member_type=member_type)
    user.set_password('testpass123')
    user.save()
    return user


class GroupCandidatesByAssigneeTests(TestCase):
    """Unit tests on the pure grouping function — no view/HTTP involved."""

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Recruitment', code='RECGROUP1', is_recruitment_committee=True)
        self.alice = _member('grp-alice', 'Alice')
        self.bob = _member('grp-bob', 'Bob')

    def _candidate(self, name, assigned_to=None, status='prospect'):
        return RecruitmentCandidate.objects.create(
            committee=self.committee, name=name, assigned_to=assigned_to, status=status)

    def test_groups_by_assignee(self):
        c1 = self._candidate('Prospect One', assigned_to=self.alice)
        c2 = self._candidate('Prospect Two', assigned_to=self.alice)
        c3 = self._candidate('Prospect Three', assigned_to=self.bob)
        rows = _group_candidates_by_assignee([c1, c2, c3])
        by_name = {row['assignee'].name: {c.name for c in row['candidates']} for row in rows}
        self.assertEqual(by_name['Alice'], {'Prospect One', 'Prospect Two'})
        self.assertEqual(by_name['Bob'], {'Prospect Three'})

    def test_unassigned_candidates_get_their_own_group(self):
        c1 = self._candidate('No Owner', assigned_to=None)
        rows = _group_candidates_by_assignee([c1])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['assignee'])
        self.assertEqual(rows[0]['candidates'], [c1])

    def test_unassigned_group_sorts_last_even_if_largest(self):
        assigned = self._candidate('Assigned', assigned_to=self.alice)
        unassigned = [self._candidate(f'Unowned {i}') for i in range(5)]
        rows = _group_candidates_by_assignee([assigned] + unassigned)
        self.assertEqual(rows[-1]['assignee'], None)
        self.assertEqual(len(rows[-1]['candidates']), 5)

    def test_assigned_groups_sort_alphabetically_by_name(self):
        self._candidate('X', assigned_to=self.bob)
        self._candidate('Y', assigned_to=self.alice)
        rows = _group_candidates_by_assignee(
            list(RecruitmentCandidate.objects.filter(committee=self.committee).select_related('assigned_to')))
        names = [row['assignee'].name for row in rows]
        self.assertEqual(names, ['Alice', 'Bob'])

    def test_empty_list_returns_empty(self):
        self.assertEqual(_group_candidates_by_assignee([]), [])


class RecruitmentDashboardAssignmentsTabTests(TestCase):
    """Exercises the real view + template through the tab."""

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Recruitment', code='RECDASH1', is_recruitment_committee=True)
        self.chair = _member('dash-chair', 'Dash Chair')
        self.committee.chairs.add(self.chair)
        self.plain_member = _member('dash-member', 'Dash Member')
        self.committee.members.add(self.plain_member)
        self.candidate = RecruitmentCandidate.objects.create(
            committee=self.committee, name='Jordan Prospect', assigned_to=self.chair)
        self.client.force_login(self.chair)

    def test_dashboard_renders_the_assignments_tab_button(self):
        resp = self.client.get(reverse('recruitment_dashboard', args=[self.committee.code]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-tab="assignments"')
        self.assertContains(resp, 'Assignments')

    def test_dashboard_context_groups_candidates_by_assignee(self):
        resp = self.client.get(reverse('recruitment_dashboard', args=[self.committee.code]))
        rows = resp.context['candidates_by_assignee']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['assignee'], self.chair)
        self.assertEqual(list(rows[0]['candidates']), [self.candidate])

    def test_assigned_candidate_name_appears_in_the_assignments_tab_markup(self):
        resp = self.client.get(reverse('recruitment_dashboard', args=[self.committee.code]))
        self.assertContains(resp, 'Jordan Prospect')

    def test_member_without_private_access_gets_no_candidates_by_assignee(self):
        outsider = _member('dash-outsider', 'Outsider')
        self.committee.members.add(outsider)
        self.client.force_login(outsider)
        resp = self.client.get(reverse('recruitment_dashboard', args=[self.committee.code]))
        self.assertIsNone(resp.context['candidates_by_assignee'])


class NonPrivilegedMemberPastEventsRegressionTests(TestCase):
    """
    Regression test for a pre-existing bug found while building the
    Assignments tab: `past` was sliced (`[:20]`) and THEN, for a
    non-privileged member, `.filter(visibility='public')` was called on
    the already-sliced queryset — `TypeError: Cannot filter a query once
    a slice has been taken`, unconditionally, a 500 for every
    non-privileged member of any recruitment committee. Unrelated to the
    Assignments tab itself; caught by exercising the dashboard as a
    non-chair, non-permissioned member with at least one past event.
    """

    def test_dashboard_loads_for_a_non_privileged_member_with_past_events(self):
        from datetime import timedelta

        from django.utils import timezone

        from src.models import Event, RecruitmentEvent

        committee = Committee.objects.create(
            name='Recruitment', code='RECPASTFIX1', is_recruitment_committee=True)
        member = _member('past-fix-member', 'Past Fix Member')
        committee.members.add(member)

        event = Event.objects.create(
            title='Old Rush Event', date_time=timezone.now() - timedelta(days=10),
            created_by=member)
        RecruitmentEvent.objects.create(committee=committee, event=event, visibility='public')

        self.client.force_login(member)
        resp = self.client.get(reverse('recruitment_dashboard', args=[committee.code]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['past']), 1)
