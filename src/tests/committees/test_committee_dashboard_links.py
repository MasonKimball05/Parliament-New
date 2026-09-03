"""
`committee_dashboard_links()` — v3.29.0.

Home-page quick links to the Kai, Service Hours, Recruitment and Education
committee management dashboards, shown only when the viewer actually has
access to that specific dashboard — requested by Mason: "several dashboards
for committees and navigating to them can be a bit tedious."

Each link mirrors its dashboard's own access predicate — EXCEPT
Recruitment and Education deliberately drop the `is_admin`/`is_officer`
chapter-wide bypass those two views allow (v3.29.2; see the comment
above the Recruitment/Education block in `src/view/home.py` for why).
These tests exercise the resulting predicates from the outside: someone
WITH a committee-specific tie sees the link, someone WITHOUT one does
not — even if they'd still be let into the dashboard itself via the
bypass. Slating already has its own equivalent (`has_slating_access`)
and isn't covered here.
"""
from django.test import TestCase

from src.models import Committee, KaiMemberPermission, ParliamentUser, Role
from src.view.home import committee_dashboard_links


def _member(user_id, name, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=name, username=user_id, member_type=member_type)
    user.set_password('testpass123')
    user.save()
    return user


class KaiDashboardLinkTests(TestCase):
    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai Committee', code='KAILINK1', is_kai_committee=True)
        self.chair = _member('link-kai-chair', 'Kai Chair')
        self.committee.chairs.add(self.chair)
        self.reviewer = _member('link-kai-rev', 'Kai Reviewer')
        self.outsider = _member('link-kai-out', 'Outsider')

    def test_chair_sees_the_link(self):
        links = committee_dashboard_links(self.chair)
        self.assertTrue(any(l['label'] == 'Kai Committee' for l in links))

    def test_member_with_a_view_permission_grant_sees_the_link(self):
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer, can_view_report_list=True)
        links = committee_dashboard_links(self.reviewer)
        self.assertTrue(any(l['label'] == 'Kai Committee' for l in links))

    def test_member_with_no_grant_does_not_see_the_link(self):
        links = committee_dashboard_links(self.outsider)
        self.assertFalse(any(l['label'] == 'Kai Committee' for l in links))

    def test_member_with_a_different_grant_does_not_see_the_link(self):
        # can_add_activity without can_view_report_list — matches
        # view_kai_reports's own gate, which checks the list flag
        # specifically.
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer, can_add_activity=True)
        links = committee_dashboard_links(self.reviewer)
        self.assertFalse(any(l['label'] == 'Kai Committee' for l in links))

    def test_no_kai_committee_configured_does_not_crash(self):
        Committee.objects.filter(pk=self.committee.pk).delete()
        links = committee_dashboard_links(self.chair)
        self.assertFalse(any(l['label'] == 'Kai Committee' for l in links))


class ServiceHoursDashboardLinkTests(TestCase):
    def setUp(self):
        self.vpp_role = Role.objects.create(name='VP Programming', code='VPP')
        self.vpp = _member('link-vpp-1', 'VPP Holder')
        self.vpp.roles.add(self.vpp_role)
        self.admin = _member('link-vpp-admin', 'Admin', member_type='Officer')
        self.admin.is_admin = True
        self.admin.save()
        self.outsider = _member('link-vpp-out', 'Outsider')

    def test_vpp_role_holder_sees_the_link(self):
        links = committee_dashboard_links(self.vpp)
        self.assertTrue(any(l['label'] == 'Service Hours' for l in links))

    def test_admin_sees_the_link(self):
        links = committee_dashboard_links(self.admin)
        self.assertTrue(any(l['label'] == 'Service Hours' for l in links))

    def test_ordinary_member_does_not_see_the_link(self):
        links = committee_dashboard_links(self.outsider)
        self.assertFalse(any(l['label'] == 'Service Hours' for l in links))


class RecruitmentDashboardLinkTests(TestCase):
    def setUp(self):
        self.committee = Committee.objects.create(
            name='Recruitment Committee', code='RECLINK1', is_recruitment_committee=True)
        self.chair = _member('link-rec-chair', 'Rec Chair')
        self.committee.chairs.add(self.chair)
        self.plain_member = _member('link-rec-mem', 'Rec Member')
        self.committee.members.add(self.plain_member)
        self.advisor = _member('link-rec-adv', 'Rec Advisor')
        self.committee.advisors.add(self.advisor)
        self.outsider = _member('link-rec-out', 'Outsider')

    def test_chair_sees_the_link(self):
        links = committee_dashboard_links(self.chair)
        link = next((l for l in links if l['label'] == 'Recruitment'), None)
        self.assertIsNotNone(link)
        self.assertIn(self.committee.code, link['url'])

    def test_plain_member_sees_the_link(self):
        links = committee_dashboard_links(self.plain_member)
        self.assertTrue(any(l['label'] == 'Recruitment' for l in links))

    def test_advisor_sees_the_link(self):
        links = committee_dashboard_links(self.advisor)
        self.assertTrue(any(l['label'] == 'Recruitment' for l in links))

    def test_outsider_does_not_see_the_link(self):
        links = committee_dashboard_links(self.outsider)
        self.assertFalse(any(l['label'] == 'Recruitment' for l in links))

    def test_holder_of_the_committees_linked_role_sees_the_link(self):
        role = Role.objects.create(name='VP Recruitment', code='VPR-LINK')
        self.committee.role = role
        self.committee.save(update_fields=['role'])
        role_holder = _member('link-rec-role', 'Role Holder')
        role_holder.roles.add(role)
        links = committee_dashboard_links(role_holder)
        self.assertTrue(any(l['label'] == 'Recruitment' for l in links))


class EducationDashboardLinkTests(TestCase):
    def setUp(self):
        self.committee = Committee.objects.create(
            name='Education Committee', code='EDULINK1', is_active=True,
            is_education_committee=True)
        self.chair = _member('link-edu-chair', 'Edu Chair')
        self.committee.chairs.add(self.chair)
        self.officer = _member('link-edu-off', 'Officer', member_type='Officer')
        self.plain_member = _member('link-edu-mem', 'Plain Member')

    def test_chair_sees_the_link(self):
        links = committee_dashboard_links(self.chair)
        link = next((l for l in links if l['label'] == 'Education'), None)
        self.assertIsNotNone(link)
        self.assertIn(self.committee.code, link['url'])

    def test_officer_without_being_chair_does_not_see_the_link(self):
        # v3.29.2 — education_home itself lets any officer in (chair-or-
        # officer), but the home-page shortcut is deliberately narrower:
        # it only shows for someone with an actual tie to THIS committee.
        # Reported by Mason on prod: he could reach Education via the
        # chapter-wide officer bypass despite not being on the committee,
        # and the quick link was advertising it as if he were.
        links = committee_dashboard_links(self.officer)
        self.assertFalse(any(l['label'] == 'Education' for l in links))

    def test_plain_member_does_not_see_the_link(self):
        links = committee_dashboard_links(self.plain_member)
        self.assertFalse(any(l['label'] == 'Education' for l in links))

    def test_inactive_committee_is_not_linked(self):
        self.committee.is_active = False
        self.committee.save(update_fields=['is_active'])
        links = committee_dashboard_links(self.chair)
        self.assertFalse(any(l['label'] == 'Education' for l in links))


class AdminBypassDoesNotGrantCommitteeSpecificLinksTests(TestCase):
    """
    v3.29.2 regression test for the exact bug Mason reported on prod: an
    admin with no real tie to the Recruitment or Education committees was
    seeing both quick links, because `is_admin`/`is_officer` were OR'd
    into their access checks. Service Hours is unaffected — it's gated by
    Role, not committee membership, and admin access there is the
    dashboard's actual intended rule (see `@vpp_required`), not a bypass
    of a committee-specific one.
    """

    def setUp(self):
        self.admin = _member('link-adminbypass-1', 'Admin No Committee', member_type='Officer')
        self.admin.is_admin = True
        self.admin.save()
        Committee.objects.create(
            name='Recruitment Committee', code='RECBYPASS1', is_recruitment_committee=True)
        Committee.objects.create(
            name='Education Committee', code='EDUBYPASS1', is_active=True,
            is_education_committee=True)

    def test_admin_with_no_committee_tie_does_not_see_recruitment_or_education(self):
        links = committee_dashboard_links(self.admin)
        labels = {l['label'] for l in links}
        self.assertNotIn('Recruitment', labels)
        self.assertNotIn('Education', labels)

    def test_admin_with_no_committee_tie_still_sees_service_hours(self):
        links = committee_dashboard_links(self.admin)
        self.assertTrue(any(l['label'] == 'Service Hours' for l in links))


class NoAccessAnywhereTests(TestCase):
    def test_a_member_with_no_committee_access_at_all_gets_no_links(self):
        member = _member('link-none-1', 'Nobody Special')
        self.assertEqual(committee_dashboard_links(member), [])
