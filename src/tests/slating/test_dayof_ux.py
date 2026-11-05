"""
Slating day-of usability (11-05-26): attendance at the meeting, the hub's way
in for the slating manager, application review for the manager, the ballots'
confirm / disabled Cast Vote, the slate-changed fingerprint and the Role
picker on Add Position.

    python manage.py test src.tests.slating.test_dayof_ux
"""
import json

from django.test import Client
from django.urls import reverse

from src.models import Role, SlatingAttendance, SlatingPeriod, SlatingPosition
from src.slating_rounds import slate_rows
from src.tests.mobile.sess_helpers import bearer, make_device
from src.tests.slating.test_slating_review_fixes import _Base, member
from src.view.slating.api import ballot_fingerprint
from src.view.slating.attendance import not_present_message

API = '/api/mobile/v1/'
JSON = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest', 'HTTP_ACCEPT': 'application/json'}


class AttendanceTests(_Base):

    def setUp(self):
        super().setUp()
        self.url = reverse('slating_attendance', args=[self.period.id])
        self.pledge = member('dx-pledge', member_type='Pledge')
        self.advisor = member('dx-advisor', member_type='Advisor')
        self.alum = member('dx-alum')
        self.alum.member_status = 'Alumni'
        self.alum.save()
        self.inactive = member('dx-inactive', role_number='4242')
        self.inactive.member_status = 'Inactive'
        self.inactive.save()
        self.client.force_login(self.manager)

    def test_lists_only_people_who_can_vote(self):
        ids = {e['member'].user_id for e in self.client.get(self.url).context['members_with_status']}
        self.assertIn(self.inactive.user_id, ids)            # inactive members may vote
        self.assertTrue(ids.isdisjoint({self.pledge.user_id, self.advisor.user_id, self.alum.user_id}))

    def test_search_filters_by_name_or_roll_number(self):
        page = self.client.get(self.url, {'q': '4242'})
        self.assertEqual([e['member'].user_id for e in page.context['members_with_status']],
                         [self.inactive.user_id])
        self.assertEqual(page.context['total_eligible'], self.client.get(self.url).context['total_eligible'])

    def test_toggle_in_place_answers_json(self):
        r = self.client.post(self.url, {'action': 'mark_present', 'member_id': self.inactive.user_id}, **JSON)
        body = r.json()
        self.assertEqual((body['ok'], body['present'], body['member_id']), (True, True, self.inactive.user_id))
        self.assertEqual(body['present_count'], 2)          # the fixture's voter + this one
        self.assertEqual(SlatingAttendance.objects.get(member=self.inactive).marked_by, self.manager)
        r = self.client.post(self.url, {'action': 'mark_absent', 'member_id': self.inactive.user_id}, **JSON)
        self.assertEqual((r.json()['present'], r.json()['present_count']), (False, 1))

    def test_plain_post_still_redirects_keeping_the_search(self):
        r = self.client.post(self.url, {'action': 'mark_present', 'member_id': self.inactive.user_id, 'q': 'Mem'})
        self.assertRedirects(r, self.url + '?q=Mem', fetch_redirect_response=False)
        self.assertTrue(SlatingAttendance.objects.filter(member=self.inactive).exists())

    def test_cannot_mark_a_pledge_present(self):
        r = self.client.post(self.url, {'action': 'mark_present', 'member_id': self.pledge.user_id})
        self.assertEqual(r.status_code, 404)

    def test_mark_all_listed_present(self):
        # The listed ids only; a pledge's id slipped in is ignored.
        r = self.client.post(self.url, {'action': 'mark_all_present', 'listed': '1',
                                        'member_ids': [self.inactive.user_id, self.pledge.user_id]}, **JSON)
        self.assertEqual(r.json()['added'], 1)
        self.assertFalse(SlatingAttendance.objects.filter(member=self.pledge).exists())
        self.assertFalse(SlatingAttendance.objects.filter(member=self.committee_member).exists())
        # A listed search with nobody in it marks nobody.
        r = self.client.post(self.url, {'action': 'mark_all_present', 'listed': '1'}, **JSON)
        self.assertEqual(r.json()['added'], 0)
        # Without a list: everyone eligible.
        self.client.post(self.url, {'action': 'mark_all_present'})
        self.assertTrue(SlatingAttendance.objects.filter(member=self.committee_member).exists())
        self.assertFalse(SlatingAttendance.objects.filter(member__in=[self.pledge, self.advisor, self.alum]).exists())

    def test_committee_member_cannot_mark(self):
        self.client.force_login(self.committee_member)
        r = self.client.post(self.url, {'action': 'mark_all_present'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SlatingAttendance.objects.count(), 1)

    def test_page_has_no_inline_script_and_loads_the_static_file(self):
        page = self.client.get(self.url).content.decode()
        self.assertIn('js/slating/attendance.js', page)
        self.assertIn('Mark all listed present', page)

    def test_app_mark_all_present(self):
        r = Client().post(f'{API}slating/periods/{self.period.id}/attendance/',
                          json.dumps({'action': 'mark_all_present', 'member_ids': [self.inactive.user_id]}),
                          content_type='application/json', **bearer(make_device(self.manager)[1]))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['added'], 1)

    def test_not_present_message_names_the_chair(self):
        self.assertEqual(not_present_message(self.period),
                         f"You're not checked in yet. Ask {self.manager.name} (slating chair) to mark you "
                         "present, then tap Cast Vote again.")


class DashboardTests(_Base):

    def rows(self, user):
        self.client.force_login(user)
        r = self.client.get(reverse('slating_dashboard'))
        return r, {row['period'].id: row for row in r.context['period_rows']}

    def test_manager_gets_manage_election(self):
        r, rows = self.rows(self.manager)
        self.assertTrue(rows[self.period.id]['can_manage'])
        self.assertContains(r, 'Manage election')

    def test_locked_out_admin_sees_who_runs_it(self):
        r, rows = self.rows(self.admin)
        self.assertTrue(rows[self.period.id]['locked_out'])
        self.assertContains(r, f'Fall</span> &mdash; managed by {self.manager.name}')
        self.assertNotContains(r, 'No election periods yet')
        self.assertNotContains(r, 'Manage election')

    def test_voted_shows_you_voted(self):
        from src.slating_ballots import cast_slate
        r, _ = self.rows(self.voter)
        self.assertContains(r, 'Cast Vote')
        cast_slate(self.voter, self.period, self.slate, list(self.slate.candidates.all()), 'approve', [])
        r, rows = self.rows(self.voter)
        self.assertTrue(rows[self.period.id]['has_voted'])
        self.assertContains(r, 'You voted')
        self.assertNotContains(r, 'Cast Vote')

    def test_no_cast_vote_for_people_who_cannot_vote(self):
        for kind in ('Pledge', 'Advisor'):
            r, rows = self.rows(member(f'dx-{kind}', member_type=kind))
            self.assertFalse(rows[self.period.id]['can_vote'])
            self.assertNotContains(r, 'Cast Vote')

    def test_apply_now_for_eligible_members_and_pledges(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='nominations_open')
        r, rows = self.rows(member('dx-applicant'))
        self.assertTrue(rows[self.period.id]['can_apply'])
        self.assertContains(r, 'Apply Now')
        # Pledges may apply (they can't vote).
        _, rows = self.rows(member('dx-pledge2', member_type='Pledge'))
        self.assertTrue(rows[self.period.id]['can_apply'])
        # Advisors and committee members can't; someone who applied already doesn't need to.
        for user in (member('dx-adv2', member_type='Advisor'), self.committee_member, self.voter):
            _, rows = self.rows(user)
            self.assertFalse(rows[self.period.id]['can_apply'], user.user_id)

    def test_app_hub_matches(self):
        p = Client().get(f'{API}slating/', **bearer(make_device(self.admin)[1])).json()['periods'][0]
        self.assertEqual((p['locked_out'], p['managed_by'], p['is_chair']), (True, self.manager.name, False))


class ReviewTests(_Base):

    def test_manager_gets_the_review_panel(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('slating_app_detail', args=[self.period.id, self.app.id]))
        self.assertTrue(r.context['can_review'])

    def test_read_only_committee_member_has_no_bulk_or_schedule(self):
        self.client.force_login(self.committee_member)
        r = self.client.get(reverse('slating_applications', args=[self.period.id]))
        self.assertNotContains(r, 'Change status to')
        r = self.client.get(reverse('slating_app_detail', args=[self.period.id, self.app.id]))
        self.assertFalse(r.context['can_review'])
        self.assertNotContains(r, 'Schedule Interview')

    def test_list_shows_ranked_positions_and_roll_number(self):
        self.voter.role_number = '777'
        self.voter.save()
        self.app.position_preferences = {'first_choice': [self.vp.id], 'second_choice': [self.pres.id]}
        self.app.save()
        self.client.force_login(self.manager)
        r = self.client.get(reverse('slating_applications', args=[self.period.id]))
        self.assertContains(r, '<span class="font-medium">1st:</span> VP')
        self.assertContains(r, '<span class="font-medium">2nd:</span> President')
        self.assertContains(r, '#777')
        self.assertContains(r, 'data-confirm="Change the status of every selected application?"')


class BallotTests(_Base):

    def test_slate_ballot_confirms_and_names_the_login_password(self):
        self.client.force_login(self.voter)
        r = self.client.get(reverse('slating_vote', args=[self.period.id]))
        self.assertContains(r, 'data-confirm="Cast your ballot? Your vote is secret and can&#x27;t be changed')
        self.assertContains(r, 'Parliament login password')
        self.assertContains(r, 'js/slating/ballot.js')
        for hook in ('id="vote_approve"', 'name="rejected_positions"', 'js-vote-form', 'id="password"'):
            self.assertContains(r, hook)

    def test_fingerprint_matches_the_api_and_ignores_count_only_changes(self):
        self.client.force_login(self.voter)
        page = self.client.get(reverse('slating_vote', args=[self.period.id]))
        api = self.client.get(reverse('slating_api_voting_status', args=[self.period.id])).json()
        self.assertContains(page, f'data-slate-fingerprint="{api["slate_fingerprint"]}"')
        before = api['slate_fingerprint']
        # A swap with the same number of candidates changes it.
        self.c_vp.write_in_member = member('dx-swap')
        self.c_vp.save()
        self.assertNotEqual(ballot_fingerprint(slate_rows(self.period, self.slate)), before)

    def test_vacant_position_row_is_in_both(self):
        SlatingPosition.objects.create(period=self.period, title='Secretary', code='S')
        rows = slate_rows(self.period, self.slate)
        self.client.force_login(self.voter)
        api = self.client.get(reverse('slating_api_voting_status', args=[self.period.id])).json()
        self.assertEqual(api['slate_fingerprint'], ballot_fingerprint(rows))
        self.assertNotEqual(api['slate_candidate_count'], len(rows))   # what used to raise the false banner


class PositionRoleTests(_Base):

    def test_add_and_edit_position_with_a_role(self):
        role = Role.objects.create(name='Treasurer Role', code='TREASX')
        self.client.force_login(self.manager)
        page = self.client.get(reverse('slating_positions', args=[self.period.id]))
        self.assertContains(page, 'name="role" id="role"')
        self.assertContains(page, 'id="edit_role"')
        self.client.post(reverse('slating_add_position', args=[self.period.id]),
                         {'title': 'Treasurer', 'code': 'TRS', 'role': role.id})
        position = SlatingPosition.objects.get(period=self.period, code='TRS')
        self.assertEqual(position.role, role)
        url = reverse('slating_edit_position', args=[self.period.id, position.id])
        self.assertEqual(self.client.get(url).json()['role_id'], role.id)
        self.client.post(url, {'title': 'Treasurer', 'role': ''})
        position.refresh_from_db()
        self.assertIsNone(position.role)


