"""
Every Kai page renders, under every permission state that can reach it.

WHY A SEPARATE MODULE (v3.18.0)
--------------------------------
`test_url_smoke` sweeps zero-argument pages as an admin. That is the right
default and it misses exactly the thing v3.18.0 introduced: **pages whose
markup changes with who is looking at them.** The new panels — recusal,
stand-in appointment, assignment, timeline, appeal countdown — each render only
under a particular combination of permission, party status and case state, so an
admin-only sweep exercises perhaps a third of the new template code.

A template that raises under one permission state and not another is the failure
this module exists to catch. Django renders an unresolvable `{% url %}` or a
missing context key as a 500, and only for the user whose branch reaches it.

Every assertion here is "does it render", not "what does it say" — the content
gating is covered by `test_kai_recusal` and `test_kai_appeals`.
"""

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Committee, KaiMemberPermission, KaiRecusal, KaiReport, ParliamentUser,
)


def make_user(uid, member_type='Member', status='Active', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status=status, is_admin=is_admin,
    )
    user.set_password('page-health-pass-12345!')
    user.save()
    return user


class KaiPageHealthTests(TestCase):
    """
    One Kai committee, one case with a committee member as the accused (so the
    recusal panel has something to render), and every role that can load a page.
    """

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True)

        self.chair = make_user('ph-chair', member_type='Chair')
        self.committee.chairs.add(self.chair)

        self.reviewer = make_user('ph-reviewer')          # full grant
        self.list_only = make_user('ph-listonly')         # list, no details
        self.accused = make_user('ph-accused')            # committee member + party
        self.reporter = make_user('ph-reporter')
        self.standin = make_user('ph-standin')
        self.advisor = make_user('ph-advisor', member_type='Advisor')
        self.pledge = make_user('ph-pledge', member_type='Pledge')
        self.committee.members.add(
            self.reviewer, self.list_only, self.accused, self.reporter)

        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
            can_edit_open_cases=True, can_add_activity=True, can_close_cases=True,
        )
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.list_only,
            can_view_report_list=True,
        )
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.accused,
            can_view_report_list=True, can_view_report_details=True,
            can_edit_open_cases=True, can_close_cases=True,
        )

        self.report = KaiReport.objects.create(
            title='Case with a recused seat', description='Body',
            submitted_by=self.reporter, targeted_to=self.accused,
        )
        # A genuinely ordinary case: filed by someone who holds NO seat, so
        # there is nothing to recuse. The first draft of this fixture used
        # `self.reporter`, who IS a committee member — `_sync_recusals`
        # correctly recused him as the submitter and the "no recusals" test
        # failed. That was the system being right and the fixture being wrong,
        # and it is worth keeping the note: filing a case recuses you from it.
        self.bystander = make_user('ph-bystander')
        self.plain = KaiReport.objects.create(
            title='Ordinary case', description='Body',
            submitted_by=self.bystander,
        )

    def _get(self, user, name, *args):
        client = Client()
        client.force_login(user)
        return client.get(reverse(name, args=args))

    def _assert_renders(self, user, name, *args, expect=200):
        response = self._get(user, name, *args)
        self.assertEqual(
            response.status_code, expect,
            f'{name} returned {response.status_code} for {user.user_id} '
            f'(expected {expect})',
        )
        return response

    # -- reviewer list, every role -----------------------------------------

    def test_the_list_renders_for_a_chair(self):
        self._assert_renders(self.chair, 'view_kai_reports')

    def test_the_list_renders_for_a_full_reviewer(self):
        self._assert_renders(self.reviewer, 'view_kai_reports')

    def test_the_list_renders_for_a_list_only_reviewer(self):
        """The gated bulk dropdown and redacted cards are this user's branch."""
        self._assert_renders(self.list_only, 'view_kai_reports')

    def test_the_list_renders_for_a_pure_standin(self):
        """No committee grant at all — reaches the list only via an appointment."""
        KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused',
            replacement=self.standin, granted_permissions={'can_view_report_list': True},
        )
        self._assert_renders(self.standin, 'view_kai_reports')

    def test_the_list_renders_with_a_stale_case(self):
        """Exercises the aging banner and the stale badge, which need a real date."""
        KaiReport.objects.filter(pk=self.plain.pk).update(
            submitted_at=timezone.now() - timedelta(days=40))
        response = self._assert_renders(self.chair, 'view_kai_reports')
        self.assertTrue(response.context['stale_count'] >= 1)
        self.assertIsNotNone(response.context['oldest_pending'])

    def test_the_list_renders_under_every_assignment_filter(self):
        for value in ('all', 'me', 'unassigned'):
            client = Client()
            client.force_login(self.chair)
            response = client.get(reverse('view_kai_reports'), {'assigned': value})
            self.assertEqual(response.status_code, 200, f'assigned={value}')

    def test_the_list_renders_under_every_status_filter(self):
        for value in ('all', 'pending', 'reviewed', 'archived'):
            client = Client()
            client.force_login(self.chair)
            response = client.get(reverse('view_kai_reports'), {'status': value})
            self.assertEqual(response.status_code, 200, f'status={value}')

    # -- case detail -------------------------------------------------------

    def test_the_case_detail_renders_the_recusal_panel_for_the_chair(self):
        """
        The chair is the only role that sees the appointment <select>, so this
        is the only test that renders that branch.
        """
        response = self._assert_renders(
            self.chair, 'manage_kai_report', self.report.id)
        self.assertTrue(response.context['can_appoint_standins'])
        self.assertTrue(response.context['recusals'])
        self.assertTrue(response.context['eligible_standins'])

    def test_the_case_detail_renders_for_a_reviewer_who_cannot_appoint(self):
        response = self._assert_renders(
            self.reviewer, 'manage_kai_report', self.report.id)
        self.assertFalse(response.context['can_appoint_standins'])

    def test_the_case_detail_renders_with_no_recusals(self):
        """The ordinary case — the panel must render nothing, not raise."""
        response = self._assert_renders(
            self.chair, 'manage_kai_report', self.plain.id)
        self.assertEqual(list(response.context['recusals']), [])

    def test_the_case_detail_renders_for_an_appointed_standin(self):
        KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused',
            replacement=self.standin,
            granted_permissions={
                'can_view_report_list': True, 'can_view_report_details': True},
        )
        self._assert_renders(self.standin, 'manage_kai_report', self.report.id)

    def test_the_print_view_renders(self):
        self._assert_renders(self.chair, 'print_kai_report', self.plain.id)

    def test_a_recused_member_is_redirected_not_500d(self):
        """A refusal must be a redirect with a message, not a crash."""
        self._assert_renders(
            self.accused, 'manage_kai_report', self.report.id, expect=302)

    # -- member-facing -----------------------------------------------------

    def test_the_member_dashboard_renders(self):
        self._assert_renders(self.reporter, 'user_kai_dashboard')
        self._assert_renders(self.accused, 'user_kai_dashboard')

    def test_the_member_report_view_renders_for_both_parties(self):
        self._assert_renders(
            self.reporter, 'user_view_kai_report', self.report.id)
        self._assert_renders(
            self.accused, 'user_view_kai_report', self.report.id)

    def test_the_member_report_view_renders_with_an_open_appeal_window(self):
        """The countdown branch — needs a notified case to reach it."""
        self.report.accused_notified = True
        self.report.accused_notified_at = timezone.now() - timedelta(days=2)
        self.report.save()
        response = self._assert_renders(
            self.accused, 'user_view_kai_report', self.report.id)
        self.assertTrue(response.context['can_appeal'])

    def test_the_member_report_view_renders_with_a_closed_appeal_window(self):
        self.report.accused_notified = True
        self.report.accused_notified_at = timezone.now() - timedelta(days=40)
        self.report.save()
        response = self._assert_renders(
            self.accused, 'user_view_kai_report', self.report.id)
        self.assertFalse(response.context['can_appeal'])

    # -- committee pages ---------------------------------------------------

    def test_the_committee_home_renders_for_every_kai_role(self):
        for user in (self.chair, self.reviewer, self.list_only, self.accused):
            self._assert_renders(user, 'committee_home', self.committee.code)

    def test_the_committee_home_renders_for_a_non_kai_member(self):
        """The Kai block must be absent, not broken, for everyone else."""
        outsider = make_user('ph-outsider')
        response = self._get(outsider, 'committee_home', self.committee.code)
        self.assertLess(response.status_code, 500)

    # -- adjacent pages that render Kai data -------------------------------

    def test_global_search_renders_for_every_kai_role(self):
        for user in (self.chair, self.list_only, self.accused):
            client = Client()
            client.force_login(user)
            response = client.get(reverse('global_search'), {'q': 'Case'})
            self.assertEqual(response.status_code, 200, user.user_id)

    def test_the_kai_admin_pages_render(self):
        self._assert_renders(self.chair, 'manage_kai_templates')
        self._assert_renders(self.chair, 'kai_form_builder')
        self._assert_renders(
            self.chair, 'manage_kai_permissions', self.committee.code)

    def test_the_csv_exports_render(self):
        for user in (self.chair, self.list_only):
            response = self._get(user, 'export_kai_reports_csv')
            self.assertLess(response.status_code, 500, user.user_id)
