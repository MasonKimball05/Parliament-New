"""
Batch 6 — a committee's settings in the app (views_committee_settings): guest
chat permissions, the committee chat's icon and colour, recruitment
permissions, and the officers' Manage Committees page with create, edit and
delete.

Who may do each is the web view's own rule (chairs, the VP and admins for
guests; chairs and admins — not the VP — for the chat's look; chairs and
admins on a recruitment committee, under the committee_home toggle; officers,
chairs and admins for Manage Committees, admins to create), and the work and
its logs are the web's, through the helpers both now call.

    python manage.py test src.tests.mobile.test_committee_settings_batch6
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    ActivityLog, ChatChannel, ChatChannelPermission, Committee, CommitteeDocument, RecruitmentMemberPermission, Role,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, toggle

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], REQUIRE_2FA_FOR_ADMINS=False,
                   REQUIRE_2FA_FOR_OFFICERS=False)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.chair = make_user('cs-chair')
        self.member = make_user('cs-member')
        self.outsider = make_user('cs-out')
        self.alum = make_user('cs-alum', member_status='Alumni')
        self.admin = make_user('cs-admin', is_admin=True)
        self.officer = make_user('cs-officer', member_type='Officer')
        self.vp = make_user('cs-vp')
        self.role = Role.objects.create(name='Test VP', code='TESTVP')
        self.vp.roles.add(self.role)
        self.c = Committee.objects.create(name='Finance', code='FIN', role=self.role)
        self.c.chairs.add(self.chair)
        self.c.members.add(self.member)
        self.channel, _ = ChatChannel.objects.get_or_create(
            committee=self.c, channel_type='committee',
            defaults={'name': 'Finance', 'access_type': 'committee', 'created_by': self.chair})
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

    def web(self, user):
        client = Client()
        client.force_login(user)
        return client


class ChatGuestTests(_Base):

    def add(self, user, **body):
        data = {'user_ids': [self.outsider.user_id], 'can_read': True, 'can_write': False,
                'can_delete': False, 'can_edit': False}
        data.update(body)
        return self.post(user, 'committees/FIN/chat-permissions/', data)

    def test_who_may_manage_guests(self):
        for user in (self.chair, self.vp, self.admin):
            self.assertEqual(self.get(user, 'committees/FIN/chat-permissions/').status_code, 200, user.pk)
        for user in (self.member, self.outsider, self.officer):
            r = self.get(user, 'committees/FIN/chat-permissions/')
            self.assertEqual(r.status_code, 403, user.pk)
            self.assertEqual(r.json()['error'], 'no_access')
            self.assertEqual(self.add(user).status_code, 403)
        self.assertFalse(ChatChannelPermission.objects.exists())

    def test_no_channel_is_404(self):
        self.channel.delete()
        self.assertEqual(self.get(self.chair, 'committees/FIN/chat-permissions/').status_code, 404)

    def test_list_matches_the_web_page(self):
        ChatChannelPermission.objects.create(channel=self.channel, user=self.alum, can_read=True)
        body = self.get(self.chair, 'committees/FIN/chat-permissions/').json()
        ctx = self.web(self.chair).get('/committee/FIN/chat/permissions/').context
        self.assertEqual([g['user_id'] for g in body['guests']],
                         [p.user.user_id for p in ctx['guest_permissions']])
        offered = {c['user_id'] for c in body['candidates']}
        web_offered = {u.user_id for u in ctx['available_users']} - ctx['already_added_ids']
        self.assertEqual(offered, web_offered)
        self.assertNotIn(self.member.user_id, offered)
        self.assertNotIn(self.alum.user_id, offered)
        self.assertEqual(body['channel']['id'], self.channel.id)

    def test_add_update_remove(self):
        later = (timezone.now() + timedelta(days=3)).isoformat()
        r = self.add(self.chair, user_ids=[self.outsider.user_id, self.member.user_id, 'nobody'], expires_at=later)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['added'], [self.outsider.name])
        self.assertEqual(sorted(r.json()['skipped']), sorted([self.member.user_id, 'nobody']))
        perm = ChatChannelPermission.objects.get(channel=self.channel, user=self.outsider)
        self.assertTrue(perm.can_read)
        self.assertIsNotNone(perm.expires_at)

        r = self.post(self.vp, f'committees/FIN/chat-permissions/{self.outsider.user_id}/',
                      {'can_read': True, 'can_write': True, 'can_delete': False, 'can_edit': True})
        self.assertEqual(r.status_code, 200)
        perm.refresh_from_db()
        self.assertTrue(perm.can_write and perm.can_edit)
        self.assertIsNone(perm.expires_at)   # empty clears it, as on the web

        r = self.post(self.admin, 'committees/FIN/chat-permissions/remove/', {'user_ids': [self.outsider.user_id]})
        self.assertEqual(r.json()['removed'], 1)
        self.assertFalse(ChatChannelPermission.objects.exists())

    def test_validation(self):
        for body in ({'can_read': False, 'can_write': False}, {'user_ids': []}, {'user_ids': 'x'},
                     {'can_read': 'yes'}, {'expires_at': '2001-01-01T00:00:00'}, {'expires_at': 'soon'},
                     {'expires_at': 5}):
            r = self.add(self.chair, **body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input')
        self.assertFalse(ChatChannelPermission.objects.exists())
        r = self.post(self.chair, f'committees/FIN/chat-permissions/{self.outsider.user_id}/',
                      {'can_read': True, 'can_write': False, 'can_delete': False, 'can_edit': False})
        self.assertEqual(r.status_code, 404)

    def test_web_actions_still_work(self):
        web = self.web(self.chair)
        r = web.post('/api/committee/FIN/chat/permissions/add/', {'user_id': self.outsider.user_id, 'can_read': 'true'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['can_read'])
        r = web.post('/api/committee/FIN/chat/permissions/add/', {'user_id': self.member.user_id, 'can_read': 'true'})
        self.assertEqual(r.status_code, 400)
        r = web.post(f'/api/committee/FIN/chat/permissions/{self.outsider.user_id}/update/',
                     {'can_write': 'true', 'expires_at': '2001-01-01T00:00'})
        self.assertEqual(r.json()['error'], 'Expiry date must be in the future')
        r = web.post(f'/api/committee/FIN/chat/permissions/{self.outsider.user_id}/remove/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ChatChannelPermission.objects.exists())
        r = self.web(self.member).post('/api/committee/FIN/chat/permissions/add/',
                                       {'user_id': self.outsider.user_id, 'can_read': 'true'})
        self.assertEqual(r.status_code, 403)


class ChatSettingsTests(_Base):

    def test_chairs_and_admins_only_not_the_vp(self):
        for user in (self.chair, self.admin):
            self.assertEqual(self.get(user, 'committees/FIN/chat-settings/').status_code, 200)
        for user in (self.vp, self.member, self.outsider):
            r = self.post(user, 'committees/FIN/chat-settings/', {'icon': '🎉', 'color': '#EF4444'})
            self.assertEqual(r.status_code, 403, user.pk)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.color, '#003DA5')

    def test_save_and_validate(self):
        r = self.post(self.chair, 'committees/FIN/chat-settings/', {'icon': '🎉', 'color': '#EF4444'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['channel']['icon'], '🎉')
        self.channel.refresh_from_db()
        self.assertEqual((self.channel.icon, self.channel.color), ('🎉', '#EF4444'))
        for body in ({'icon': '', 'color': '#EF4444'}, {'icon': 'x' * 11, 'color': '#EF4444'},
                     {'icon': '🎉', 'color': 'red'}, {'icon': 3, 'color': '#EF4444'}):
            r = self.post(self.chair, 'committees/FIN/chat-settings/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input')
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.color, '#EF4444')

    def test_web_refuses_bad_values_instead_of_failing(self):
        r = self.web(self.chair).post('/committee/FIN/chat/settings/', {'icon': 'x' * 11, 'color': '#EF4444'})
        self.assertEqual(r.status_code, 302)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.icon, '💬')
        self.web(self.chair).post('/committee/FIN/chat/settings/', {'icon': '🔥', 'color': '#10B981'})
        self.channel.refresh_from_db()
        self.assertEqual((self.channel.icon, self.channel.color), ('🔥', '#10B981'))

    def test_dashboard_links_it_for_chairs(self):
        flag('chats', True)
        flag('committee_system', True)
        titles = lambda u: [l['title'] for l in self.get(u, 'committees/FIN/').json()['web_links']]  # noqa: E731
        self.assertIn('Chat settings', titles(self.chair))
        self.assertIn('Chat permissions', titles(self.vp))
        self.assertNotIn('Chat settings', titles(self.vp))


class RecruitmentPermissionTests(_Base):

    def setUp(self):
        super().setUp()
        self.r = Committee.objects.create(name='Recruitment', code='RCT', is_recruitment_committee=True)
        self.r.chairs.add(self.chair)
        self.r.members.add(self.member)

    def test_who_may(self):
        self.assertEqual(self.get(self.chair, 'committees/FIN/recruitment-permissions/').status_code, 404)
        for user in (self.chair, self.admin):
            self.assertEqual(self.get(user, 'committees/RCT/recruitment-permissions/').status_code, 200)
        for user in (self.member, self.outsider, self.officer):
            self.assertEqual(self.get(user, 'committees/RCT/recruitment-permissions/').status_code, 403)
            self.assertEqual(self.post(user, 'committees/RCT/recruitment-permissions/reset/').status_code, 403)

    def test_page_toggle(self):
        toggle('committee_home', False)
        r = self.get(self.chair, 'committees/RCT/recruitment-permissions/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'page_disabled')

    def test_rows_match_the_web_page(self):
        RecruitmentMemberPermission.objects.create(committee=self.r, user=self.member, can_view_private=True)
        body = self.get(self.chair, 'committees/RCT/recruitment-permissions/').json()
        ctx = self.web(self.chair).get('/committee/RCT/recruitment-permissions/').context
        web_rows = [(row['member'].user_id, row['is_chair'], [row[f] for f in ctx['perm_fields']])
                    for row in ctx['member_rows']]
        app_rows = [(row['user_id'], row['is_chair'], [row['flags'][f['field']] for f in body['fields']])
                    for row in body['rows']]
        self.assertEqual(app_rows, web_rows)

    def test_update_and_reset(self):
        r = self.post(self.chair, 'committees/RCT/recruitment-permissions/',
                      {'user_id': self.member.user_id, 'flags': {'can_take_attendance': True}})
        self.assertEqual(r.status_code, 200)
        perm = RecruitmentMemberPermission.objects.get(committee=self.r, user=self.member)
        self.assertTrue(perm.can_take_attendance)
        self.assertFalse(perm.can_manage_events)
        self.assertEqual(perm.granted_by, self.chair)
        # A field left out keeps its value.
        self.post(self.chair, 'committees/RCT/recruitment-permissions/',
                  {'user_id': self.member.user_id, 'flags': {'can_manage_events': True}})
        perm.refresh_from_db()
        self.assertTrue(perm.can_take_attendance and perm.can_manage_events)

        for body in ({'user_id': self.chair.user_id, 'flags': {'can_manage_events': False}},
                     {'user_id': 'nobody', 'flags': {'can_manage_events': True}},
                     {'user_id': self.member.user_id, 'flags': {'is_admin': True}},
                     {'user_id': self.member.user_id, 'flags': {'can_manage_events': 'yes'}}):
            r = self.post(self.chair, 'committees/RCT/recruitment-permissions/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input')

        r = self.post(self.admin, 'committees/RCT/recruitment-permissions/reset/')
        self.assertEqual(r.json()['deleted'], 1)
        self.assertFalse(RecruitmentMemberPermission.objects.exists())

    def test_web_update_still_works(self):
        r = self.web(self.chair).post(f'/api/committee/RCT/recruitment-permissions/{self.member.user_id}/update/',
                                      {'can_view_private': 'true'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(RecruitmentMemberPermission.objects.get(user=self.member).can_view_private)


class ManageCommitteesTests(_Base):

    def setUp(self):
        super().setUp()
        flag('committee_system', True)

    def test_who_may_list(self):
        for user in (self.officer, self.admin):
            r = self.get(user, 'officer/committees/')
            self.assertEqual(r.status_code, 200, user.pk)
        for user in (self.member, self.chair, self.vp):   # chair of a committee isn't member_type Chair
            self.assertEqual(self.get(user, 'officer/committees/').status_code, 403, user.pk)
        chair_type = make_user('cs-chairtype', member_type='Chair')
        self.assertEqual(self.get(chair_type, 'officer/committees/').status_code, 200)
        flag('committee_system', False)
        r = self.get(self.officer, 'officer/committees/')
        self.assertEqual(r.json()['error'], 'feature_disabled')

    def test_list_matches_the_web_page(self):
        body = self.get(self.officer, 'officer/committees/').json()
        ctx = self.web(self.officer).get('/committees/manage/').context
        self.assertEqual([(c['id'], c['member_count'], c['chair_count']) for c in body['committees']],
                         [(d['committee'].id, d['member_count'], d['chair_count']) for d in ctx['committees_data']])
        self.assertEqual(body['total'], ctx['total_committees'])
        self.assertFalse(body['can_create'])
        self.assertTrue(self.get(self.admin, 'officer/committees/').json()['can_create'])

    def test_only_admins_create(self):
        body = {'name': 'Risk', 'code': 'risk', 'role_id': self.role.id, 'member_ids': [self.member.user_id],
                'chair_ids': [self.chair.user_id], 'is_ad_hoc': True}
        self.assertEqual(self.post(self.officer, 'officer/committees/new/', body).status_code, 403)
        self.assertEqual(self.get(self.officer, 'officer/committees/new/').status_code, 403)
        self.assertEqual(self.get(self.admin, 'officer/committees/new/').status_code, 200)
        r = self.post(self.admin, 'officer/committees/new/', body)
        self.assertEqual(r.status_code, 201, r.content)
        c = Committee.objects.get(code='RISK')
        self.assertEqual(c.role, self.role)
        self.assertTrue(c.is_ad_hoc and c.is_active)
        self.assertEqual(list(c.members.all()), [self.member])
        self.assertEqual(list(c.chairs.all()), [self.chair])
        r = self.post(self.admin, 'officer/committees/new/', {'name': 'Risk 2', 'code': 'RISK'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        for bad in ({'name': '', 'code': 'X'}, {'name': 'Y', 'code': ''}, {'name': 'Y', 'code': 'Y', 'role_id': 'x'},
                    {'name': 'Y', 'code': 'Y', 'member_ids': 'x'}):
            self.assertEqual(self.post(self.admin, 'officer/committees/new/', bad).status_code, 400, bad)

    def test_edit_and_archive_log_like_the_web(self):
        r = self.get(self.officer, f'officer/committees/{self.c.id}/')
        web = self.web(self.officer).get(f'/committees/{self.c.id}/').json()['committee']
        self.assertEqual(r.json()['committee'], web)
        r = self.post(self.officer, f'officer/committees/{self.c.id}/',
                      {'name': 'Finance & Budget', 'code': 'fin', 'is_archived': True, 'role_id': None})
        self.assertEqual(r.status_code, 200, r.content)
        self.c.refresh_from_db()
        self.assertEqual(self.c.name, 'Finance & Budget')
        self.assertTrue(self.c.is_archived)
        self.assertIsNone(self.c.role)
        log = ActivityLog.objects.filter(metadata__action='edit_committee').get()
        self.assertEqual(log.user, self.officer)
        Committee.objects.create(name='Social', code='SOC')
        for bad in ({'name': 'Social'}, {'code': 'soc'}, {'name': ''}, {'is_active': 'no'},
                    {'ad_hoc_expiration': 'tomorrow'}, {'member_ids': []}):
            r = self.post(self.officer, f'officer/committees/{self.c.id}/', bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.json()['error'], 'invalid_input')
        self.assertEqual(self.post(self.member, f'officer/committees/{self.c.id}/', {'name': 'X'}).status_code, 403)

    def test_web_edit_refuses_a_taken_name(self):
        Committee.objects.create(name='Social', code='SOC')
        r = self.web(self.officer).post(f'/committees/{self.c.id}/', {'name': 'Social'},
                                        content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['success'])

    def test_delete(self):
        CommitteeDocument.objects.create(committee=self.c, title='Doc', uploaded_by=self.chair, document='x.pdf')
        r = self.post(self.officer, f'officer/committees/{self.c.id}/delete/')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Committee.objects.filter(id=self.c.id).exists())
        empty = Committee.objects.create(name='Temp', code='TMP')
        self.assertEqual(self.post(self.member, f'officer/committees/{empty.id}/delete/').status_code, 403)
        r = self.post(self.officer, f'officer/committees/{empty.id}/delete/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Committee.objects.filter(id=empty.id).exists())
        self.assertTrue(ActivityLog.objects.filter(metadata__action='delete_committee').exists())

    def test_officer_hub_offers_it_with_the_flag(self):
        self.assertTrue(self.get(self.officer, 'officer/').json()['can_manage_committees'])
        flag('committee_system', False)
        self.assertFalse(self.get(self.officer, 'officer/').json()['can_manage_committees'])
