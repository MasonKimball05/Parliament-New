"""
The Kai Committee's review side in the app (src/mobile/views_kai_review.py):
the case list and bulk actions, one case and its actions, recusals and
stand-ins, report templates and the committee's member grants.

The app and the website share every read and write (src/view/kai_reports.py,
src/view/committee/manage_kai_permissions.py), so several tests compare the
app's answer with the page's context, or do a thing both ways and compare the
rows and emails. Confidentiality is the point: the accused (and anyone
recused) gets nothing about the case, the identity grants are honoured field
by field, and the search box can't be used to read what a row withholds.

    python manage.py test src.tests.mobile.test_kai_review_batch6
"""
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import (
    ActivityLog, Committee, KaiClosureRequest, KaiMemberPermission, KaiRecusal, KaiReport, KaiReportActivity,
    KaiReportTemplate,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict

API = '/api/mobile/v1/'
ALL = dict(can_view_report_list=True, can_view_report_details=True, can_view_submitter_identity=True,
           can_view_accused_identity=True, can_edit_open_cases=True, can_add_activity=True, can_close_cases=True)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.chair = make_user('kr-chair', email='chair@example.com')
        self.member = make_user('kr-member', email='member@example.com')
        self.reporter = make_user('kr-reporter', email='reporter@example.com')
        self.accused = make_user('kr-accused', email='accused@example.com')
        self.outsider = make_user('kr-outsider')
        self.kai = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.kai.members.add(self.chair, self.member)
        self.kai.chairs.add(self.chair)
        self.api = Client()
        self.tokens = {}

    def access(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def get(self, user, p):
        return self.api.get(API + p, **bearer(self.access(user)))

    def post(self, user, p, body=None):
        return self.api.post(API + p, body or {}, content_type='application/json', **bearer(self.access(user)))

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c

    def grant(self, user, **flags):
        self.kai.members.add(user)
        return KaiMemberPermission.objects.create(committee=self.kai, user=user, **flags)

    def case(self, title='The case', by=None, to=None, **kw):
        report = KaiReport.objects.create(
            title=title, description=f'{title} secret body', category='behavioral',
            submitted_by=by or self.reporter, targeted_to=to if to is not None else self.accused, **kw)
        KaiReportActivity.objects.create(report=report, user=report.submitted_by, action='created',
                                         details='Report submitted')
        return report

    def assertError(self, r, status, code):
        self.assertEqual(r.status_code, status, r.content)
        self.assertEqual(r.json()['error'], code)


# ── The list ─────────────────────────────────────────────────────────────────

class ReviewListTests(_Base):

    def test_who_may_open_the_list(self):
        self.case()
        self.assertError(self.get(self.outsider, 'kai/review/'), 403, 'forbidden')
        # A site admin is not a Kai reviewer (v3.18.2).
        admin = make_user('kr-admin', is_admin=True)
        self.assertError(self.get(admin, 'kai/review/'), 403, 'forbidden')
        self.assertEqual(self.get(self.chair, 'kai/review/').status_code, 200)
        flag('kai_reports', False)
        self.assertError(self.get(self.chair, 'kai/review/'), 403, 'feature_disabled')

    def test_rows_and_counts_match_the_page(self):
        for i in range(3):
            self.case(f'Case {i}', status='pending' if i else 'reviewed')
        query = '?status=pending&search=Case'
        b = self.get(self.chair, 'kai/review/' + query).json()
        ctx = self.web(self.chair).get(reverse('view_kai_reports') + query).context
        self.assertEqual([r['id'] for r in b['reports']], [r.id for r in ctx['reports']])
        self.assertEqual(b['counts'], ctx['counts'])
        self.assertEqual(b['category_counts'], ctx['category_counts'])
        self.assertEqual(b['search_placeholder'], ctx['search_placeholder'])
        self.assertEqual(b['stats']['outcomes']['pending'], ctx['outcome_pending'])
        self.assertEqual(len(b['recent_activity']), len(ctx['recent_activities']))
        self.assertTrue(b['can_manage_permissions'])
        self.assertTrue(b['can_manage_templates'])
        self.assertEqual(b['bulk_actions'], ['mark_reviewed', 'mark_pending', 'archive'])

    def test_a_list_only_reviewer_reads_no_body_and_no_names(self):
        self.case()
        viewer = make_user('kr-listonly')
        self.grant(viewer, can_view_report_list=True)
        b = self.get(viewer, 'kai/review/').json()
        row = b['reports'][0]
        self.assertIsNone(row['description'])
        self.assertEqual((row['submitter'], row['accused']), ('Anonymous', 'Redacted'))
        self.assertNotIn('kr-reporter', str(b))
        self.assertNotIn('kr-accused', str(b))
        self.assertNotIn('secret body', str(b))
        self.assertEqual(b['bulk_actions'], [])
        self.assertFalse(b['can_manage_permissions'])
        # The search box can't reach a field the viewer may not read.
        self.assertEqual(self.get(viewer, 'kai/review/?search=secret').json()['reports'], [])
        self.assertEqual(self.get(viewer, 'kai/review/?search=kr-reporter').json()['reports'], [])

    def test_the_accused_sees_their_own_case_redacted_and_cannot_search_it(self):
        # A full reviewer who is named in a case.
        reviewer = make_user('kr-namedreviewer')
        self.grant(reviewer, **ALL)
        self.case('Their case', to=reviewer)
        b = self.get(reviewer, 'kai/review/').json()
        row = b['reports'][0]
        self.assertEqual(row['viewer_recusal'], 'accused')
        self.assertIsNone(row['description'])
        self.assertEqual((row['submitter'], row['accused']), ('Anonymous', 'Redacted'))
        self.assertFalse(row['can_open'])
        self.assertNotIn('kr-reporter', str(b))
        # The body and the reporter's name are not an oracle; the title still is.
        self.assertEqual(self.get(reviewer, 'kai/review/?search=secret').json()['reports'], [])
        self.assertEqual(self.get(reviewer, 'kai/review/?search=reporter').json()['reports'], [])
        self.assertEqual(len(self.get(reviewer, 'kai/review/?search=Their').json()['reports']), 1)
        # Its activity is dropped from the recent feed.
        self.assertEqual(b['recent_activity'], [])

    def test_the_submitter_reads_their_case_but_the_card_says_so(self):
        reviewer = make_user('kr-subreviewer')
        self.grant(reviewer, **ALL)
        self.case('Filed', by=reviewer)
        row = self.get(reviewer, 'kai/review/').json()['reports'][0]
        self.assertEqual(row['viewer_recusal'], 'submitter')
        self.assertIsNone(row['description'])
        self.assertTrue(row['can_open'])

    def test_a_stand_in_sees_only_their_case(self):
        mine = self.case('Mine')
        self.case('Not mine')
        stand_in = make_user('kr-standin')
        KaiRecusal.objects.create(report=mine, user=self.member, reason='unavailable', replacement=stand_in,
                                  granted_permissions={'can_view_report_details': True})
        b = self.get(stand_in, 'kai/review/').json()
        self.assertEqual([r['id'] for r in b['reports']], [mine.id])
        self.assertTrue(b['standin_only'])
        self.assertEqual(b['counts']['all'], 1)
        self.assertEqual(b['bulk_actions'], [])
        # The page's all-case analytics and activity feed are not sent to a pure stand-in.
        self.assertEqual(b['recent_activity'], [])
        self.assertEqual(b['stats']['outcomes'], {'pending': 0, 'heard': 0, 'thrown_out': 0})
        self.assertNotIn('Not mine', str(b))


class BulkTests(_Base):

    def test_archive_skips_the_callers_own_cases(self):
        reviewer = make_user('kr-bulk')
        self.grant(reviewer, **ALL)
        theirs = self.case('Mine', by=reviewer)
        named = self.case('Named', to=reviewer)
        other = self.case('Other')
        r = self.post(reviewer, 'kai/review/bulk/',
                      {'action': 'archive', 'report_ids': [theirs.id, named.id, other.id]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], '1 report(s) archived.')
        self.assertEqual(set(KaiReport.objects.filter(status='archived').values_list('id', flat=True)), {other.id})
        self.assertTrue(ActivityLog.objects.filter(action_type='kai_action',
                                                   metadata__action='bulk_archive').exists())

    def test_permissions_and_validation(self):
        c = self.case()
        viewer = make_user('kr-bulk-list')
        self.grant(viewer, can_view_report_list=True)
        self.assertError(self.post(viewer, 'kai/review/bulk/', {'action': 'archive', 'report_ids': [c.id]}),
                         403, 'forbidden')
        self.assertError(self.post(self.outsider, 'kai/review/bulk/', {'action': 'archive', 'report_ids': [c.id]}),
                         403, 'forbidden')
        self.assertError(self.post(self.chair, 'kai/review/bulk/', {'action': 'export_csv', 'report_ids': [c.id]}),
                         400, 'invalid_input')
        self.assertError(self.post(self.chair, 'kai/review/bulk/', {'action': 'archive', 'report_ids': []}),
                         400, 'invalid_input')
        self.assertError(self.post(self.chair, 'kai/review/bulk/', {'action': 'archive', 'report_ids': ['x']}),
                         400, 'invalid_input')
        self.assertEqual(KaiReport.objects.get().status, 'pending')


# ── One case ─────────────────────────────────────────────────────────────────

class CaseReadTests(_Base):

    def test_the_accused_and_the_manually_recused_get_nothing(self):
        reviewer = make_user('kr-acc-rev')
        self.grant(reviewer, **ALL)
        named = self.case('Named', to=reviewer)
        r = self.get(reviewer, f'kai/review/{named.id}/')
        self.assertError(r, 403, 'recused')
        self.assertNotIn('secret', r.content.decode())
        self.assertNotIn('kr-reporter', r.content.decode())
        other = self.case('Other')
        KaiRecusal.objects.create(report=other, user=self.member, reason='conflict', recorded_by=self.chair)
        self.grant_member_all()
        self.assertError(self.get(self.member, f'kai/review/{other.id}/'), 403, 'recused')
        # …and can't act on it either.
        self.assertError(self.post(self.member, f'kai/review/{other.id}/', {'action': 'mark_reviewed'}),
                         403, 'recused')

    def grant_member_all(self):
        KaiMemberPermission.objects.update_or_create(committee=self.kai, user=self.member, defaults=ALL)

    def test_no_details_grant_no_case(self):
        c = self.case()
        viewer = make_user('kr-list2')
        self.grant(viewer, can_view_report_list=True)
        self.assertError(self.get(viewer, f'kai/review/{c.id}/'), 403, 'forbidden')
        self.assertError(self.get(self.outsider, f'kai/review/{c.id}/'), 403, 'forbidden')
        self.assertEqual(self.get(self.chair, 'kai/review/999999/').status_code, 404)

    def test_a_details_only_reviewer_sees_no_names_anywhere(self):
        c = self.case()
        viewer = make_user('kr-details')
        self.grant(viewer, can_view_report_list=True, can_view_report_details=True)
        b = self.get(viewer, f'kai/review/{c.id}/').json()
        self.assertIn('secret body', b['description'])
        self.assertTrue(b['has_accused'])
        self.assertIsNone(b['accused_name'])
        self.assertIsNone(b['submitter_name'])
        self.assertIsNone(b['accused_section'])
        self.assertIsNone(b['submitter_section'])
        self.assertIsNone(b['chair_notes'])
        self.assertIsNone(b['committee_notes'])
        self.assertEqual(b['closure_requests'], [])
        self.assertEqual(b['assignable'], [])
        # The 'created' row is authored by the submitter: redacted.
        self.assertEqual(b['activity'][-1]['actor'], 'Anonymous')
        self.assertNotIn('kr-reporter', str(b))
        self.assertNotIn('kr-accused', str(b))
        self.assertEqual(b['can'], {'edit': False, 'notes': False, 'close': False, 'archive': False,
                                    'notify_accused': False, 'notify_submitter': False, 'appoint': False})

    def test_the_chair_sees_what_the_page_shows(self):
        c = self.case(chair_notes='private', committee_notes='decided')
        KaiClosureRequest.objects.create(report=c, requested_by=self.reporter, reason='Done', request_type='closure')
        b = self.get(self.chair, f'kai/review/{c.id}/').json()
        ctx = self.web(self.chair).get(reverse('manage_kai_report', args=[c.id])).context
        self.assertEqual(b['accused_name'], 'User kr-accused')
        self.assertEqual(b['submitter_name'], 'User kr-reporter')
        self.assertEqual(b['chair_notes'], 'private')
        self.assertEqual(b['committee_notes'], 'decided')
        self.assertEqual(b['accused_section']['accused']['email'], 'accused@example.com')
        self.assertEqual(b['submitter_section']['email'], 'reporter@example.com')
        self.assertEqual([a['actor'] for a in b['activity']], [a.display_actor for a in ctx['activity_log']])
        self.assertEqual([a['details'] for a in b['activity']], [a.display_details for a in ctx['activity_log']])
        self.assertEqual([m['user_id'] for m in b['assignable']], [m.pk for m in ctx['assignable_members']])
        self.assertEqual([r['id'] for r in b['closure_requests']], [cr.id for cr in ctx['closure_requests']])
        self.assertEqual(b['recusals']['can_appoint'], True)
        self.assertTrue(all(b['can'].values()))

    def test_the_submitter_reads_but_cannot_decide(self):
        reviewer = make_user('kr-sub2')
        self.grant(reviewer, **ALL)
        c = self.case(by=reviewer)
        b = self.get(reviewer, f'kai/review/{c.id}/').json()
        self.assertTrue(b['access']['is_submitter_readonly'])
        self.assertFalse(b['can']['edit'])
        self.assertFalse(b['can']['close'])
        self.assertError(self.post(reviewer, f'kai/review/{c.id}/', {'action': 'archive'}), 403, 'forbidden')
        self.assertEqual(KaiReport.objects.get().status, 'pending')

    def test_a_seated_accused_gets_a_recusal_seat_on_load(self):
        c = self.case(to=self.member)
        b = self.get(self.chair, f'kai/review/{c.id}/').json()
        self.assertEqual([s['reason'] for s in b['recusals']['seats']], ['accused'])
        self.assertFalse(b['recusals']['seats'][0]['can_end'])

    def test_party_names_follow_the_identity_flags(self):
        # v4.4.2: who asked to close or drop a case, and whose seat is
        # recused, were named past the identity flags, on the page and here.
        c = self.case(to=self.member)
        KaiClosureRequest.objects.create(report=c, requested_by=self.reporter, reason='Done', request_type='drop')
        viewer = make_user('kr-closer')
        self.grant(viewer, can_view_report_list=True, can_view_report_details=True, can_close_cases=True)
        b = self.get(viewer, f'kai/review/{c.id}/').json()
        self.assertEqual(b['closure_requests'][0]['requested_by'], 'The submitter')
        self.assertEqual(b['recusals']['seats'][0]['member'], 'The accused')
        page = self.web(viewer).get(reverse('manage_kai_report', args=[c.id]))
        self.assertContains(page, 'Requested by The submitter')
        self.assertNotContains(page, self.reporter.name)
        self.assertNotContains(page, self.member.name)
        # With the flags, the names are shown.
        KaiMemberPermission.objects.filter(user=viewer).update(
            can_view_submitter_identity=True, can_view_accused_identity=True)
        b = self.get(viewer, f'kai/review/{c.id}/').json()
        self.assertEqual(b['closure_requests'][0]['requested_by'], self.reporter.name)
        self.assertEqual(b['recusals']['seats'][0]['member'], self.member.name)

    def test_a_stand_in_is_not_told_whose_seat_it_is(self):
        c = self.case(to=self.member)
        self.get(self.chair, f'kai/review/{c.id}/')   # materialises the accused's seat
        standin = make_user('kr-standin')
        KaiRecusal.objects.filter(report=c, user=self.member).update(replacement=standin)
        b = self.get(standin, 'kai/').json()
        self.assertEqual(b['standing_in'][0]['standing_in_for'], 'a recused member')
        page = self.web(standin).get(reverse('user_kai_dashboard'))
        self.assertContains(page, 'Standing in for a recused member')
        self.assertNotContains(page, self.member.name)


class CaseActionTests(_Base):

    def test_mark_reviewed_like_the_website(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'mark_reviewed'})
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual(b['status'], 'reviewed')
        self.assertEqual(b['message'], 'Report "The case" marked as reviewed.')
        self.assertFalse(b['recused'])
        app_rows = list(c.activity_log.filter(action='status_changed').values_list('details', flat=True))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['reporter@example.com'])
        self.assertTrue(ActivityLog.objects.filter(action_type='kai_action', metadata__action='mark_reviewed').exists())

        # The same through the page.
        mail.outbox = []
        c2 = self.case('Second')
        resp = self.web(self.chair).post(reverse('manage_kai_report', args=[c2.id]), {'action': 'mark_reviewed'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(list(c2.activity_log.filter(action='status_changed').values_list('details', flat=True)),
                         app_rows)
        self.assertEqual(len(mail.outbox), 1)

    def test_action_permissions(self):
        c = self.case()
        viewer = make_user('kr-notes')
        self.grant(viewer, can_view_report_list=True, can_view_report_details=True, can_add_activity=True)
        self.assertError(self.post(viewer, f'kai/review/{c.id}/', {'action': 'mark_reviewed'}), 403, 'forbidden')
        self.assertError(self.post(viewer, f'kai/review/{c.id}/', {'action': 'archive'}), 403, 'forbidden')
        r = self.post(viewer, f'kai/review/{c.id}/', {'action': 'update_notes', 'chair_notes': 'Seen'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(KaiReport.objects.get().chair_notes, 'Seen')
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/', {'action': 'nope'}), 400, 'invalid_input')

    def test_validation_keeps_the_pages_words(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'update_tags', 'tags': ['urgent', 'Bob Smith']})
        self.assertError(r, 400, 'invalid_input')
        self.assertIn('not in the allowed list', r.json()['detail'])
        self.assertEqual(KaiReport.objects.get().tags, [])
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'update_tags', 'tags': ['urgent']})
        self.assertEqual(r.json()['tag_labels'], ['Urgent'])
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/',
                                   {'action': 'update_deliberation', 'deliberation_outcome': 'banished'}),
                         400, 'invalid_input')
        # A party can't be assigned their own case.
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/',
                                   {'action': 'assign_case', 'assigned_to': self.reporter.pk}), 400, 'invalid_input')
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'assign_case', 'assigned_to': self.member.pk})
        self.assertEqual(r.json()['assigned_to']['user_id'], self.member.pk)
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/',
                                   {'action': 'notify_accused', 'accused_notification_message': ''}),
                         400, 'invalid_input')
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/',
                                   {'action': 'update_accused', 'accused_id': self.accused.pk,
                                    'accused_email': 'not-an-email'}), 400, 'invalid_input')

    def test_deliberation_emails_only_the_accused(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/', {
            'action': 'update_deliberation', 'deliberation_outcome': 'heard', 'committee_notes': 'Hearing',
            'closed_by_accused_request': True})
        self.assertEqual(r.status_code, 200, r.content)
        report = KaiReport.objects.get()
        self.assertEqual((report.deliberation_outcome, report.status, report.closed_by_accused_request),
                         ('heard', 'archived', True))
        self.assertEqual([m.to for m in mail.outbox], [['accused@example.com']])
        self.assertIsNone(r.json()['submitter_section'])   # minutes closed: no "notify submitter"

    def test_notify_accused_and_closure_decisions(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'notify_accused',
                                                          'accused_notification_message': 'You were reported.'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['accused_section']['notified'])
        self.assertEqual(mail.outbox[-1].to, ['accused@example.com'])
        cr = KaiClosureRequest.objects.create(report=c, requested_by=self.reporter, reason='Done')
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/',
                                   {'action': 'deny_closure', 'closure_request_id': cr.id}), 400, 'invalid_input')
        r = self.post(self.chair, f'kai/review/{c.id}/',
                      {'action': 'approve_closure', 'closure_request_id': cr.id, 'review_notes': 'OK'})
        self.assertEqual(r.json()['status'], 'archived')
        cr.refresh_from_db()
        self.assertEqual((cr.status, cr.reviewed_by), ('approved', self.chair))

    def test_naming_yourself_takes_you_off_the_case(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'update_accused', 'accused_id': self.chair.pk})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'messages': r.json()['messages'], 'message': r.json()['message'],
                                    'recused': True})

    def test_link_and_unlink(self):
        c, other = self.case(), self.case('Other')
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'link_report', 'related_report_id': other.id})
        self.assertEqual([x['id'] for x in r.json()['related']], [other.id])
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/',
                                   {'action': 'link_report', 'related_report_id': 999999}), 400, 'invalid_input')
        r = self.post(self.chair, f'kai/review/{c.id}/', {'action': 'unlink_report', 'related_report_id': other.id})
        self.assertEqual(r.json()['related'], [])


# ── Recusals and stand-ins ───────────────────────────────────────────────────

class RecusalTests(_Base):

    def setUp(self):
        super().setUp()
        KaiMemberPermission.objects.create(committee=self.kai, user=self.member, **ALL)
        self.stand_in = make_user('kr-sub')

    def test_recuse_and_fill_in_one_step_then_end_it(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/recuse/', {
            'members': [{'member': self.member.pk, 'reason': 'conflict', 'replacement': self.stand_in.pk}],
            'notes': 'Roommate'})
        self.assertEqual(r.status_code, 200, r.content)
        seat = KaiRecusal.objects.get(report=c, user=self.member)
        self.assertEqual((seat.reason, seat.replacement, seat.notes), ('conflict', self.stand_in, 'Roommate'))
        self.assertTrue(seat.granted_permissions['can_close_cases'])
        # The recused member is off the case; the stand-in is on it, and only it.
        self.assertError(self.get(self.member, f'kai/review/{c.id}/'), 403, 'recused')
        b = self.get(self.stand_in, f'kai/review/{c.id}/').json()
        self.assertTrue(b['access']['is_standin'])
        other = self.case('Other')
        self.assertError(self.get(self.stand_in, f'kai/review/{other.id}/'), 403, 'forbidden')
        # Withdraw the stand-in, then end the recusal.
        r = self.post(self.chair, f'kai/review/{c.id}/standin/remove/', {'recusal_id': seat.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertError(self.get(self.stand_in, f'kai/review/{c.id}/'), 403, 'forbidden')
        r = self.post(self.chair, f'kai/review/{c.id}/standin/', {'recusal_id': seat.id,
                                                                 'replacement': self.stand_in.pk})
        self.assertEqual(r.json()['recusals']['seats'][0]['replacement'], 'User kr-sub')
        r = self.post(self.chair, f'kai/review/{c.id}/recuse/end/', {'recusal_id': seat.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(KaiRecusal.objects.filter(report=c).exists())
        self.assertEqual(self.get(self.member, f'kai/review/{c.id}/').status_code, 200)
        self.assertEqual(list(c.activity_log.filter(action__in=['standin_appointed', 'standin_removed'])
                              .values_list('action', flat=True).order_by('id')),
                         ['standin_appointed', 'standin_appointed', 'standin_removed', 'standin_appointed',
                          'standin_removed'])

    def test_only_an_unrecused_chair(self):
        c = self.case()
        admin = make_user('kr-admin2', is_admin=True)
        for who in (self.member, admin, self.outsider):
            self.assertError(self.post(who, f'kai/review/{c.id}/recuse/',
                                       {'members': [{'member': self.member.pk}]}), 403, 'forbidden')
        accused_chair_case = self.case('Against the chair', to=self.chair)
        self.assertError(self.post(self.chair, f'kai/review/{accused_chair_case.id}/recuse/',
                                   {'members': [{'member': self.member.pk}]}), 403, 'recused')
        self.assertFalse(KaiRecusal.objects.filter(reason='conflict').exists())

    def test_the_seat_rules_hold(self):
        c = self.case(to=self.member)
        self.get(self.chair, f'kai/review/{c.id}/')   # materialises the accused's seat
        seat = KaiRecusal.objects.get(report=c, user=self.member)
        # A case-derived recusal can't be lifted; a party can't stand in.
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/recuse/end/', {'recusal_id': seat.id}),
                         400, 'invalid_input')
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/standin/',
                                   {'recusal_id': seat.id, 'replacement': self.reporter.pk}), 400, 'invalid_input')
        self.assertEqual(self.post(self.chair, f'kai/review/{c.id}/standin/',
                                   {'recusal_id': 999999, 'replacement': self.stand_in.pk}).status_code, 404)
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/recuse/', {'members': []}), 400, 'invalid_input')
        self.assertError(self.post(self.chair, f'kai/review/{c.id}/recuse/',
                                   {'members': [{'member': self.stand_in.pk, 'reason': 'accused'}]}),
                         400, 'invalid_input')

    def test_self_recusal_answers_recused(self):
        c = self.case()
        r = self.post(self.chair, f'kai/review/{c.id}/recuse/', {'members': [{'member': self.chair.pk}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['recused'])
        self.assertEqual(r.json()['messages'][0]['level'], 'warning')


# ── Templates ────────────────────────────────────────────────────────────────

class TemplateTests(_Base):

    def body(self, **kw):
        b = {'name': 'Noise', 'description': 'Late noise', 'category': 'social', 'title_template': 'Noise',
             'description_template': 'What happened', 'suggested_tags': ['urgent'], 'is_active': True}
        b.update(kw)
        return b

    def test_create_edit_delete(self):
        r = self.post(self.chair, 'kai/templates/', self.body())
        self.assertEqual(r.status_code, 201, r.content)
        t = KaiReportTemplate.objects.get()
        self.assertEqual((t.name, t.suggested_tags, t.is_active, t.created_by), ('Noise', ['urgent'], True, self.chair))
        self.assertEqual(r.json()['message'], 'Template "Noise" created successfully.')
        r = self.post(self.chair, f'kai/templates/{t.id}/', self.body(name='Quiet', is_active=False))
        self.assertEqual(r.status_code, 200, r.content)
        t.refresh_from_db()
        self.assertEqual((t.name, t.is_active), ('Quiet', False))
        self.assertEqual(self.get(self.chair, 'kai/templates/').json()['templates'][0]['name'], 'Quiet')
        r = self.post(self.chair, f'kai/templates/{t.id}/delete/')
        self.assertEqual(r.json()['message'], 'Template "Quiet" deleted successfully.')
        self.assertFalse(KaiReportTemplate.objects.exists())

    def test_who_and_validation(self):
        # The pages' gate: a chair (Committee.is_chair) or a site admin.
        admin = make_user('kr-admin3', is_admin=True)
        self.assertEqual(self.get(admin, 'kai/templates/').status_code, 200)
        self.assertError(self.get(self.member, 'kai/templates/'), 403, 'forbidden')
        self.assertError(self.post(self.member, 'kai/templates/', self.body()), 403, 'forbidden')
        r = self.post(self.chair, 'kai/templates/', self.body(suggested_tags=['Bob Smith']))
        self.assertError(r, 400, 'invalid_input')
        self.assertIn('not in the allowed list', r.json()['detail'])
        self.assertError(self.post(self.chair, 'kai/templates/', self.body(name='')), 400, 'invalid_input')
        self.assertError(self.post(self.chair, 'kai/templates/', self.body(category='x')), 400, 'invalid_input')
        self.assertFalse(KaiReportTemplate.objects.exists())
        t = KaiReportTemplate.objects.create(name='A', description='d', category='social', title_template='t',
                                             description_template='d')
        self.assertError(self.post(self.chair, f'kai/templates/{t.id}/', self.body(suggested_tags=['nope'])),
                         400, 'invalid_input')
        t.refresh_from_db()
        self.assertEqual(t.name, 'A')
        flag('kai_reports', False)
        self.assertError(self.get(self.chair, 'kai/templates/'), 403, 'feature_disabled')

    def test_create_has_its_own_pledge_restriction(self):
        pledge = make_user('kr-pledge', member_type='Pledge')
        self.kai.chairs.add(pledge)
        restrict('create_kai_template', [])
        self.assertEqual(self.get(pledge, 'kai/templates/').status_code, 200)
        self.assertError(self.post(pledge, 'kai/templates/', self.body()), 403, 'pledge_restricted')


# ── Member grants ────────────────────────────────────────────────────────────

class PermissionGrantTests(_Base):

    def test_rows_match_the_page(self):
        self.grant(self.member, can_view_report_list=True)
        b = self.get(self.chair, 'committees/KAI/kai-permissions/').json()
        ctx = self.web(self.chair).get(reverse('manage_kai_permissions', args=['KAI'])).context
        self.assertEqual([(m['user_id'], m['is_chair'], m['can_view_report_list']) for m in b['members']],
                         [(r['member'].pk, r['is_chair'], r['can_view_report_list']) for r in ctx['member_rows']])
        self.assertEqual(len(b['fields']), 7)

    def test_only_an_actual_chair(self):
        admin = make_user('kr-admin4', is_admin=True)
        for who in (admin, self.member, self.outsider):
            self.assertError(self.get(who, 'committees/KAI/kai-permissions/'), 403, 'forbidden')
            self.assertError(self.post(who, 'committees/KAI/kai-permissions/reset/'), 403, 'forbidden')
        # The exec-board shortcut grants nothing.
        self.kai.is_exec_board = True
        self.kai.save()
        self.assertError(self.get(self.member, 'committees/KAI/kai-permissions/'), 403, 'forbidden')
        other = Committee.objects.create(name='Other', code='OTH')
        other.chairs.add(self.chair)
        self.assertEqual(self.get(self.chair, 'committees/OTH/kai-permissions/').status_code, 404)

    def test_set_and_reset(self):
        r = self.post(self.chair, f'committees/KAI/kai-permissions/{self.member.pk}/',
                      {'can_view_report_list': True, 'can_view_report_details': True})
        self.assertEqual(r.status_code, 200, r.content)
        perm = KaiMemberPermission.objects.get(user=self.member)
        self.assertEqual((perm.can_view_report_list, perm.can_view_report_details, perm.can_close_cases,
                          perm.granted_by), (True, True, False, self.chair))
        self.assertError(self.post(self.chair, f'committees/KAI/kai-permissions/{self.chair.pk}/',
                                   {'can_view_report_list': True}), 400, 'invalid_input')
        self.assertError(self.post(self.chair, f'committees/KAI/kai-permissions/{self.member.pk}/',
                                   {'can_view_report_list': 'yes'}), 400, 'invalid_input')
        self.assertEqual(self.post(self.chair, 'committees/KAI/kai-permissions/nobody/', {}).status_code, 404)
        r = self.post(self.chair, 'committees/KAI/kai-permissions/reset/')
        self.assertEqual(r.json()['deleted'], 1)
        self.assertFalse(KaiMemberPermission.objects.exists())

    def test_the_committee_dashboard_links_it_for_the_chair_only(self):
        links = [x['path'] for x in self.get(self.chair, 'committees/KAI/').json()['web_links']]
        self.assertIn(reverse('manage_kai_permissions', args=['KAI']), links)
        links = [x['path'] for x in self.get(self.member, 'committees/KAI/').json()['web_links']]
        self.assertNotIn(reverse('manage_kai_permissions', args=['KAI']), links)
