"""
v4.4.0 — a committee's chair tools in the app (views_committee_management):
create, recalculate, push/unpush and delete votes; add and remove people;
member permissions; attendance; publish and delete documents.

Who may do each is the web view's own rule (chairs; the VP and admins for
roles; admins for deleting votes; officers for attendance; designated members
for documents and the Manage Members page), and the work and its logs are the
web's, via src/committee_management. On the Kai committee nothing extra is
granted: a KaiMemberPermission holder is not a chair, and adding a chair there
is what widens Kai access, on the app exactly as on the website.

    python manage.py test src.tests.mobile.test_committee_management
"""
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ActivityLog, Attendance, Committee, CommitteeDocument, CommitteeLegislation, CommitteePermissions,
    CommitteeVote, KaiMemberPermission, Legislation, Role,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/committees/'


def _leg(committee, poster, **extra):
    fields = dict(title='Budget', description='The budget', posted_by=poster,
                  available_at=timezone.now() - timedelta(hours=1))
    fields.update(extra)
    return CommitteeLegislation.objects.create(committee=committee, **fields)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], REQUIRE_2FA_FOR_ADMINS=False,
                   REQUIRE_2FA_FOR_OFFICERS=False)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.chair = make_user('ct-chair')
        self.member = make_user('ct-member')
        self.voter = make_user('ct-voter')
        self.outsider = make_user('ct-out')
        self.admin = make_user('ct-admin', is_admin=True)
        self.officer = make_user('ct-officer', member_type='Officer')
        self.vp = make_user('ct-vp')
        self.pledge = make_user('ct-pledge', member_type='Pledge')
        self.role = Role.objects.create(name='Test VP', code='TESTVP')
        self.vp.roles.add(self.role)
        self.c = Committee.objects.create(name='Finance', code='FIN', role=self.role)
        self.c.chairs.add(self.chair)
        self.c.members.add(self.member, self.voter)
        self.c.voting_members.add(self.voter)
        self.tokens = {}
        self.api = Client()

    def token(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def get(self, user, path):
        return self.api.get(API + path, **bearer(self.token(user)))

    def post(self, user, path, data=None):
        return self.api.post(API + path, data=data or {}, content_type='application/json', **bearer(self.token(user)))


class VoteToolTests(_Base):

    def create(self, user, **body):
        data = {'title': 'Budget', 'description': 'Approve it', 'available_at': timezone.now().isoformat()}
        data.update(body)
        return self.post(user, 'FIN/votes/new/', data)

    def test_only_chairs_create(self):
        for user in (self.member, self.voter, self.admin, self.vp, self.outsider):
            r = self.create(user)
            self.assertEqual(r.status_code, 403, user.pk)
            self.assertEqual(r.json()['error'], 'no_access')
        self.assertFalse(CommitteeLegislation.objects.exists())

    def test_chair_creates_a_vote(self):
        r = self.create(self.chair, required_percentage='67', voting_ends_at=(timezone.now() + timedelta(days=1)).isoformat())
        self.assertEqual(r.status_code, 201)
        leg = CommitteeLegislation.objects.get()
        self.assertEqual((leg.title, leg.posted_by, leg.required_percentage, leg.vote_mode),
                         ('Budget', self.chair, '67', 'percentage'))
        self.assertIsNotNone(leg.voting_ends_at)
        self.assertEqual(r.json()['vote']['id'], leg.id)

    def test_create_form_choices(self):
        body = self.get(self.chair, 'FIN/votes/new/').json()
        self.assertEqual([t['value'] for t in body['thresholds']], ['51', '60', '67', '75', '100'])
        self.assertEqual(body['max_options'], 10)
        self.assertEqual(self.get(self.member, 'FIN/votes/new/').status_code, 403)

    def test_plurality_and_piecewise(self):
        r = self.create(self.chair, vote_mode='plurality', plurality_options=['A', ' B ', '', 'C'],
                        plurality_votes_allowed=9, plurality_runoff_enabled=True, plurality_runoff_count=7)
        self.assertEqual(r.status_code, 201)
        leg = CommitteeLegislation.objects.get()
        self.assertEqual(leg.plurality_options, ['A', 'B', 'C'])
        self.assertEqual((leg.plurality_votes_allowed, leg.plurality_runoff_count), (3, 3))
        self.create(self.chair, vote_mode='piecewise', required_number=4)
        self.assertEqual(CommitteeLegislation.objects.get(vote_mode='piecewise').required_number, 4)

    def test_validation(self):
        cases = [
            {'description': ''},
            {'title': '  '},
            {'available_at': None},
            {'available_at': 'tomorrow'},
            {'vote_mode': 'ranked'},
            {'required_percentage': '50'},
            {'vote_mode': 'plurality', 'plurality_options': ['Only one']},
            {'vote_mode': 'plurality', 'plurality_options': 'A,B'},
            {'vote_mode': 'piecewise', 'required_number': 0},
            {'vote_mode': 'piecewise', 'required_number': 'lots'},
        ]
        for body in cases:
            r = self.create(self.chair, **body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input', body)
        self.assertFalse(CommitteeLegislation.objects.exists())

    def test_feature_flags(self):
        flag('anonymous_voting', False)
        self.create(self.chair, anonymous=True)
        self.assertFalse(CommitteeLegislation.objects.get().anonymous_vote)
        flag('committee_voting', False)
        self.assertEqual(self.create(self.chair).json()['error'], 'feature_disabled')
        self.assertEqual(self.get(self.chair, 'FIN/votes/manage/').json()['error'], 'feature_disabled')

    def test_manage_list(self):
        open_leg = _leg(self.c, self.member, title='Open')
        CommitteeVote.objects.create(user=self.voter, legislation=open_leg, vote_choice='yes')
        closed = _leg(self.c, self.chair, title='Closed', voting_closed=True, voting_ended_at=timezone.now())
        self.assertEqual(self.get(self.member, 'FIN/votes/manage/').status_code, 403)
        self.assertEqual(self.get(self.officer, 'FIN/votes/manage/').status_code, 403)

        body = self.get(self.chair, 'FIN/votes/manage/').json()
        self.assertTrue(body['can_create'])
        self.assertEqual([v['title'] for v in body['open']], ['Open'])
        self.assertEqual(body['open'][0]['results'][0], {'option': 'yes', 'count': 1})
        row = body['closed'][0]
        self.assertEqual(row['id'], closed.id)
        self.assertTrue(row['can_recalculate'] and row['can_push'] and row['can_delete'])
        self.assertEqual(row['chapter_vote_path'], reverse('create_chapter_vote', args=['FIN', closed.id]))

        # An admin who isn't a chair: delete only, and no tally on an open vote they didn't vote on.
        body = self.get(self.admin, 'FIN/votes/manage/').json()
        self.assertFalse(body['can_create'])
        self.assertIsNone(body['open'][0]['results'])
        self.assertEqual([(v['can_delete'], v['can_push'], v['can_recalculate']) for v in body['closed']],
                         [(True, False, False)])

    def test_recalculate(self):
        leg = _leg(self.c, self.chair, voting_closed=True, required_percentage='51')
        CommitteeVote.objects.create(user=self.voter, legislation=leg, vote_choice='yes')
        self.assertEqual(self.post(self.member, f'FIN/votes/{leg.id}/recalculate/').status_code, 403)
        # vote.py's recalculation is chair-only; an officer uses the chapter documents page.
        self.assertEqual(self.post(self.officer, f'FIN/votes/{leg.id}/recalculate/').status_code, 403)
        r = self.post(self.chair, f'FIN/votes/{leg.id}/recalculate/')
        self.assertEqual(r.json()['result'], 'passed')
        leg.refresh_from_db()
        self.assertTrue(leg.passed)
        self.assertEqual(leg.status, 'passed')
        log = ActivityLog.objects.get(object_type='CommitteeLegislation', object_id=leg.id)
        self.assertEqual(log.metadata['action'], 'recalculate')

    def test_recalculate_needs_a_closed_vote_and_votes(self):
        open_leg = _leg(self.c, self.chair)
        self.assertEqual(self.post(self.chair, f'FIN/votes/{open_leg.id}/recalculate/').status_code, 403)
        empty = _leg(self.c, self.chair, voting_closed=True)
        self.assertIsNone(self.post(self.chair, f'FIN/votes/{empty.id}/recalculate/').json()['result'])
        self.assertFalse(ActivityLog.objects.filter(object_type='CommitteeLegislation').exists())

    def test_push_and_unpush(self):
        leg = _leg(self.c, self.chair, voting_closed=True, passed=True, status='passed')
        for user in (self.member, self.admin, self.vp):
            self.assertEqual(self.post(user, f'FIN/votes/{leg.id}/push/').status_code, 403)
        with self.assertLogs('function_calls', 'INFO') as logs:
            self.assertTrue(self.post(self.chair, f'FIN/votes/{leg.id}/push/').json()['vote']['pushed_to_chapter'])
        self.assertIn('to chapter documents', logs.output[-1])
        r = self.post(self.chair, f'FIN/votes/{leg.id}/push/')
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_published'))
        self.assertFalse(self.post(self.chair, f'FIN/votes/{leg.id}/unpush/').json()['vote']['pushed_to_chapter'])
        self.assertEqual(self.post(self.chair, f'FIN/votes/{leg.id}/unpush/').json()['error'], 'not_published')

    def test_delete(self):
        chapter = Legislation.objects.create(title='Chapter copy', description='x', posted_by=self.chair,
                                             available_at=timezone.now())
        leg = _leg(self.c, self.chair, voting_closed=True, chapter_legislation=chapter)
        CommitteeVote.objects.create(user=self.voter, legislation=leg, vote_choice='no')
        self.assertEqual(self.post(self.member, f'FIN/votes/{leg.id}/delete/').status_code, 403)
        self.assertEqual(self.post(self.admin, f'FIN/votes/{leg.id}/delete/').json(), {'ok': True, 'title': 'Budget'})
        self.assertFalse(CommitteeLegislation.objects.exists())
        self.assertFalse(CommitteeVote.objects.exists())
        self.assertFalse(Legislation.objects.filter(pk=chapter.pk).exists())

    def test_another_committees_vote_is_not_found(self):
        other = Committee.objects.create(name='Other', code='OTH')
        leg = _leg(other, self.chair, voting_closed=True)
        self.assertEqual(self.post(self.chair, f'FIN/votes/{leg.id}/delete/').status_code, 404)
        self.assertEqual(self.post(self.chair, 'NOPE/votes/new/').status_code, 404)


class MemberToolTests(_Base):

    def test_members_screen_says_what_may_change(self):
        self.assertEqual(self.get(self.chair, 'FIN/members/').json()['manage'],
                         {'roles': True, 'members': True, 'permissions': True})
        self.assertEqual(self.get(self.member, 'FIN/members/').json()['manage'],
                         {'roles': False, 'members': False, 'permissions': False})
        self.c.is_archived = True
        self.c.save()
        self.assertFalse(self.get(self.chair, 'FIN/members/').json()['manage']['roles'])

    def test_roles_chair_vp_admin(self):
        for user in (self.chair, self.vp, self.admin):
            r = self.post(user, 'FIN/members/add/', {'user_id': self.outsider.pk, 'role_type': 'advisor'})
            self.assertEqual(r.status_code, 200, user.pk)
            self.assertTrue(self.c.advisors.filter(pk=self.outsider.pk).exists())
            self.c.advisors.remove(self.outsider)
        r = self.post(self.chair, 'FIN/members/add/', {'user_id': self.member.pk, 'role_type': 'chair'})
        self.assertEqual(r.json()['message'], f'{self.member.name} has been added as a chair.')
        self.assertTrue(self.c.chairs.filter(pk=self.member.pk).exists())

    def test_roles_refused(self):
        for user in (self.member, self.voter, self.officer, self.outsider):
            r = self.post(user, 'FIN/members/add/', {'user_id': self.outsider.pk, 'role_type': 'member'})
            self.assertEqual(r.status_code, 403, user.pk)
            self.assertEqual(self.post(user, 'FIN/members/remove/',
                                       {'user_id': self.member.pk, 'role_type': 'member'}).status_code, 403)
        self.assertFalse(self.c.members.filter(pk=self.outsider.pk).exists())
        self.assertTrue(self.c.members.filter(pk=self.member.pk).exists())

    def test_roles_validation(self):
        r = self.post(self.chair, 'FIN/members/add/', {'user_id': self.pledge.pk, 'role_type': 'member'})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Pledges cannot be added to committees.'))
        self.assertEqual(self.post(self.chair, 'FIN/members/add/',
                                   {'user_id': 'nobody', 'role_type': 'member'}).json()['error'], 'invalid_input')
        self.assertEqual(self.post(self.chair, 'FIN/members/add/',
                                   {'user_id': self.outsider.pk, 'role_type': 'owner'}).json()['error'], 'invalid_input')

    def test_removing_a_member_drops_their_vote(self):
        r = self.post(self.chair, 'FIN/members/remove/', {'user_id': self.voter.pk, 'role_type': 'member'})
        self.assertEqual(r.json()['message'], f'{self.voter.name} has been removed from members and voting members.')
        self.assertFalse(self.c.voting_members.filter(pk=self.voter.pk).exists())

    def test_designated_member_manages_members_but_not_roles(self):
        CommitteePermissions.objects.create(committee=self.c, user=self.member, can_manage_members=True)
        self.assertEqual(self.get(self.member, 'FIN/members/').json()['manage'],
                         {'roles': False, 'members': True, 'permissions': False})
        self.assertEqual(self.post(self.member, 'FIN/members/add/',
                                   {'user_id': self.outsider.pk, 'role_type': 'chair'}).status_code, 403)
        r = self.post(self.member, 'FIN/members/manage/', {'user_id': self.outsider.pk, 'action': 'add_voter'})
        self.assertEqual(r.json()['message'], f'{self.outsider.name} has been added as a voting member.')
        self.assertTrue(self.c.voting_members.filter(pk=self.outsider.pk).exists())
        r = self.post(self.member, 'FIN/members/manage/', {'user_id': self.pledge.pk, 'action': 'add_member'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post(self.member, 'FIN/members/manage/',
                                   {'user_id': self.outsider.pk, 'action': 'promote'}).status_code, 400)
        self.assertEqual(self.post(self.voter, 'FIN/members/manage/',
                                   {'user_id': self.outsider.pk, 'action': 'add_member'}).status_code, 403)
        # The designated list never offers chairs.
        self.assertEqual(self.get(self.member, 'FIN/members/candidates/?role=chair').status_code, 403)

    def test_candidates(self):
        body = self.get(self.chair, 'FIN/members/candidates/?role=chair').json()
        self.assertEqual(sorted(c['user_id'] for c in body['candidates']), ['ct-member', 'ct-voter'])
        voters = [c['user_id'] for c in self.get(self.chair, 'FIN/members/candidates/?role=voter').json()['candidates']]
        self.assertEqual(sorted(voters), ['ct-chair', 'ct-member'])
        members = [c['user_id'] for c in self.get(self.chair, 'FIN/members/candidates/?role=member').json()['candidates']]
        self.assertIn('ct-out', members)
        self.assertNotIn('ct-member', members)
        self.assertNotIn('ct-pledge', members)
        self.assertEqual(self.get(self.voter, 'FIN/members/candidates/?role=member').status_code, 403)
        self.assertEqual(self.get(self.chair, 'FIN/members/candidates/?role=owner').status_code, 400)

    def test_member_permissions(self):
        self.c.advisors.add(self.outsider)
        self.c.members.add(self.chair)   # a chair who is also a member is still not listed
        for user in (self.member, self.officer):
            self.assertEqual(self.get(user, 'FIN/member-permissions/').status_code, 403)
        body = self.get(self.vp, 'FIN/member-permissions/').json()
        self.assertEqual([d['field'] for d in body['designations']],
                         ['can_take_minutes', 'can_upload_docs', 'can_manage_members'])
        self.assertEqual([r['user_id'] for r in body['rows']], sorted(['ct-member', 'ct-voter', 'ct-out'],
                         key=lambda pk: {'ct-member': self.member, 'ct-voter': self.voter,
                                         'ct-out': self.outsider}[pk].name))
        r = self.post(self.chair, 'FIN/member-permissions/', {'user_id': self.member.pk, 'flags': {'can_upload_docs': True}})
        row = next(x for x in r.json()['rows'] if x['user_id'] == 'ct-member')
        self.assertEqual(row['flags'], {'can_take_minutes': False, 'can_upload_docs': True, 'can_manage_members': False})
        perm = CommitteePermissions.objects.get(committee=self.c, user=self.member)
        self.assertTrue(perm.can_view_docs)   # created the way the web page creates it
        for body in ({'user_id': self.chair.pk, 'flags': {'can_upload_docs': True}},
                     {'user_id': self.member.pk, 'flags': {'can_vote': True}},
                     {'user_id': self.member.pk, 'flags': {'can_upload_docs': 'yes'}},
                     {'user_id': self.member.pk}):
            self.assertEqual(self.post(self.chair, 'FIN/member-permissions/', body).json()['error'], 'invalid_input')
        self.assertEqual(self.post(self.member, 'FIN/member-permissions/',
                                   {'user_id': self.member.pk, 'flags': {'can_manage_members': True}}).status_code, 403)


class AttendanceTests(_Base):

    def test_chair_marks_today(self):
        self.assertEqual(self.get(self.member, 'FIN/attendance/').status_code, 403)
        body = self.get(self.chair, 'FIN/attendance/').json()
        self.assertEqual(body['total_members'], 2)
        self.assertEqual(body['present_count'], 0)
        r = self.post(self.chair, 'FIN/attendance/', {'present': [self.member.pk]})
        self.assertEqual((r.json()['updated'], r.json()['present_count']), (1, 1))
        att = Attendance.objects.get(committee=self.c, user=self.member, attendance_type='committee')
        self.assertEqual((att.status, att.marked_by, att.date), ('present', self.chair, timezone.localdate()))
        self.assertTrue(ActivityLog.objects.filter(object_type='Committee', object_id=self.c.id,
                                                   description__contains='1 members present').exists())
        # Unticking removes today's record; officers may mark too.
        self.post(self.officer, 'FIN/attendance/', {'present': []})
        self.assertFalse(Attendance.objects.filter(committee=self.c, attendance_type='committee').exists())

    def test_validation(self):
        self.assertEqual(self.post(self.chair, 'FIN/attendance/', {'present': 'ct-member'}).json()['error'],
                         'invalid_input')


class DocumentToolTests(_Base):

    def doc(self, **extra):
        return CommitteeDocument.objects.create(
            committee=self.c, title='Budget', uploaded_by=self.chair,
            document=SimpleUploadedFile('budget.pdf', b'%PDF-1.4 test', content_type='application/pdf'), **extra)

    def test_documents_screen_says_who_manages(self):
        body = self.get(self.chair, 'FIN/documents/').json()
        self.assertTrue(body['can_manage'])
        self.assertEqual(body['upload_path'], reverse('upload_document', args=['FIN']))
        body = self.get(self.member, 'FIN/documents/').json()
        self.assertFalse(body['can_manage'])
        self.assertIsNone(body['upload_path'])

    def test_publish(self):
        d = self.doc()
        with self.assertLogs('function_calls', 'INFO') as logs:
            r = self.post(self.chair, f'FIN/documents/{d.id}/publish/', {'published': True})
        self.assertTrue(r.json()['document']['published_to_chapter'])
        self.assertTrue(any('DOCUMENT_PUBLISH' in line for line in logs.output))
        d.refresh_from_db()
        self.assertTrue(d.published_to_chapter)
        # No body toggles, as the web button does.
        self.assertFalse(self.post(self.chair, f'FIN/documents/{d.id}/publish/').json()['document']['published_to_chapter'])
        self.assertEqual(self.post(self.chair, f'FIN/documents/{d.id}/publish/', {'published': 'yes'}).status_code, 400)

    def test_refused_publish_is_a_security_event(self):
        d = self.doc()
        with self.assertLogs('security', 'WARNING') as logs:
            self.assertEqual(self.post(self.member, f'FIN/documents/{d.id}/publish/', {'published': True}).status_code, 403)
        self.assertIn('UNAUTHORIZED_DOCUMENT_PUBLISH', logs.output[0])
        d.refresh_from_db()
        self.assertFalse(d.published_to_chapter)

    def test_delete(self):
        d = self.doc()
        with self.assertLogs('security', 'WARNING') as logs:
            self.assertEqual(self.post(self.voter, f'FIN/documents/{d.id}/delete/').status_code, 403)
        self.assertIn('UNAUTHORIZED_DOCUMENT_DELETE', logs.output[0])
        CommitteePermissions.objects.create(committee=self.c, user=self.member, can_upload_docs=True)
        name = d.document.name
        storage = d.document.storage
        self.assertEqual(self.post(self.member, f'FIN/documents/{d.id}/delete/').json(), {'ok': True, 'title': 'Budget'})
        self.assertFalse(CommitteeDocument.objects.exists())
        self.assertFalse(storage.exists(name))

    def test_other_committees_document_not_found(self):
        other = Committee.objects.create(name='Other', code='OTH')
        d = CommitteeDocument.objects.create(
            committee=other, title='X', uploaded_by=self.chair,
            document=SimpleUploadedFile('x.pdf', b'%PDF-1.4 test', content_type='application/pdf'))
        self.assertEqual(self.post(self.chair, f'FIN/documents/{d.id}/delete/').status_code, 404)


class KaiCommitteeTests(_Base):
    """Nothing here is Kai-specific, so a Kai grant opens nothing and chairs decide as anywhere else."""

    def setUp(self):
        super().setUp()
        self.kai = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.kai_chair = make_user('ct-kai-chair')
        self.grantee = make_user('ct-kai-grantee')
        self.kai.chairs.add(self.kai_chair)
        self.kai.members.add(self.grantee, self.member)
        KaiMemberPermission.objects.create(
            committee=self.kai, user=self.grantee, can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True, can_edit_open_cases=True,
            can_add_activity=True, can_close_cases=True)

    def test_a_kai_grant_is_not_a_chair(self):
        g = self.grantee
        self.assertEqual(self.get(g, 'KAI/members/').json()['manage'], {'roles': False, 'members': False,
                                                                       'permissions': False})
        self.assertEqual(self.post(g, 'KAI/members/add/', {'user_id': g.pk, 'role_type': 'chair'}).status_code, 403)
        self.assertEqual(self.post(g, 'KAI/members/manage/', {'user_id': self.outsider.pk, 'action': 'add_member'}).status_code, 403)
        self.assertEqual(self.get(g, 'KAI/member-permissions/').status_code, 403)
        self.assertEqual(self.get(g, 'KAI/attendance/').status_code, 403)
        self.assertEqual(self.get(g, 'KAI/votes/manage/').status_code, 403)
        self.assertEqual(self.get(g, 'KAI/votes/new/').status_code, 403)
        self.assertFalse(self.kai.chairs.filter(pk=g.pk).exists())

    def test_the_web_refuses_the_same_grantee(self):
        self.client.force_login(self.grantee)
        self.client.post(reverse('committee_add_member', args=['KAI']), {'user_id': self.grantee.pk, 'role_type': 'chair'})
        self.assertFalse(self.kai.chairs.filter(pk=self.grantee.pk).exists())

    def test_kai_chair_adds_a_chair_who_then_has_kai_access(self):
        from src.view.kai_reports import _get_kai_access
        self.assertFalse(_get_kai_access(self.member, self.kai)['is_full_access'])
        r = self.post(self.kai_chair, 'KAI/members/add/', {'user_id': self.member.pk, 'role_type': 'chair'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(_get_kai_access(self.member, self.kai)['is_full_access'])
        self.post(self.kai_chair, 'KAI/members/remove/', {'user_id': self.member.pk, 'role_type': 'chair'})
        self.assertFalse(_get_kai_access(self.member, self.kai)['is_full_access'])

    def test_kai_chair_designates_but_kai_flags_are_not_designations(self):
        r = self.post(self.kai_chair, 'KAI/member-permissions/',
                      {'user_id': self.grantee.pk, 'flags': {'can_view_report_list': False}})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(KaiMemberPermission.objects.get(user=self.grantee).can_view_report_list)


class WebStillWorksTests(_Base):
    """The web views now call src/committee_management; same results as before."""

    def test_web_create_vote(self):
        self.client.force_login(self.chair)
        r = self.client.post(reverse('create_committee_vote', args=['FIN']), {
            'title': 'Web vote', 'description': 'x', 'available_at': '2026-11-01T10:00',
            'vote_mode': 'plurality', 'plurality_option_1': 'A', 'plurality_option_2': 'B',
            'plurality_votes_allowed': '2'})
        self.assertRedirects(r, reverse('vote', args=['FIN']), fetch_redirect_response=False)
        leg = CommitteeLegislation.objects.get()
        self.assertEqual((leg.plurality_options, leg.plurality_votes_allowed), (['A', 'B'], 2))
        r = self.client.post(reverse('create_committee_vote', args=['FIN']), {'title': 'No date', 'description': 'x'})
        self.assertEqual(r.status_code, 200)   # re-rendered with "fill in all required fields"
        r = self.client.post(reverse('create_committee_vote', args=['FIN']), {
            'title': 'P', 'description': 'x', 'available_at': '2026-11-01T10:00', 'vote_mode': 'piecewise'})
        self.assertRedirects(r, reverse('create_committee_vote', args=['FIN']), fetch_redirect_response=False)
        self.assertEqual(CommitteeLegislation.objects.count(), 1)

    def test_web_vote_page_recalculate_and_delete(self):
        leg = _leg(self.c, self.chair, voting_closed=True)
        CommitteeVote.objects.create(user=self.voter, legislation=leg, vote_choice='no')
        self.client.force_login(self.chair)
        self.client.post(reverse('vote', args=['FIN']), {'recalculate_result': '1', 'legislation_id': leg.id})
        leg.refresh_from_db()
        self.assertEqual((leg.passed, leg.status), (False, 'draft'))
        self.assertTrue(ActivityLog.objects.filter(object_id=leg.id, object_type='CommitteeLegislation').exists())
        self.client.post(reverse('delete_committee_vote', args=['FIN', leg.id]))
        self.assertFalse(CommitteeLegislation.objects.exists())

    def test_web_members_attendance_and_documents(self):
        self.client.force_login(self.chair)
        self.client.post(reverse('committee_remove_member', args=['FIN']), {'user_id': self.voter.pk, 'role_type': 'member'})
        self.assertFalse(self.c.voting_members.filter(pk=self.voter.pk).exists())
        self.client.post(reverse('committee_attendance', args=['FIN']), {'action': 'mark_attendance', 'present': [self.member.pk]})
        self.assertTrue(Attendance.objects.filter(committee=self.c, user=self.member).exists())
        d = CommitteeDocument.objects.create(
            committee=self.c, title='Budget', uploaded_by=self.chair,
            document=SimpleUploadedFile('b.pdf', b'%PDF-1.4 test', content_type='application/pdf'))
        self.client.post(reverse('toggle_document_publish', args=['FIN', d.id]))
        d.refresh_from_db()
        self.assertTrue(d.published_to_chapter)
        self.client.post(reverse('delete_committee_document', args=['FIN', d.id]))
        self.assertFalse(CommitteeDocument.objects.exists())
