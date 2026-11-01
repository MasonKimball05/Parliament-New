"""
Self-service account deletion (src/account_deletion.py).

Request → signed out everywhere, can't sign in, shown as a former member
(record only) and out of every by-status list, admins told, member emailed.
Restore within the window. Purge at 30 days erases exactly the personal data
and keeps the membership record. Rebuild after the purge.

    python manage.py test src.tests.users.test_account_deletion
"""
import shutil
import tempfile
from datetime import date, time, timedelta

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from src import account_deletion as deletion
from src.mobile.tokens import create_device
from src.models import (
    ActivityLog, AdminActionLog, Attendance, ChapterMinutes, Legislation, LoginHistory, MobileDevice,
    Notification, PageVisit, ParliamentUser, Role, Vote,
)
from src.models.kai import KaiReport
from src.models.users import RoleHistory, UserPreferences, UserSession
from src.models.webauthn import WebAuthnCredential
from src.tests.admin_v2.base import AdminV2TestCase

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f'
       b'\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND'
       b'\xaeB`\x82')
PASSWORD = 'acct-del-pass-12345!'


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid, member_type='Member', member_status='Active')
    fields.update(extra)
    user = ParliamentUser.objects.create(**fields)
    user.set_password(PASSWORD)
    user.save()
    return user


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

        self.admin = make_user('ad-admin', name='Ada Admin', is_admin=True, member_type='Officer',
                               email='admin@example.com')
        self.big = make_user('ad-big', name='Bob Big', role_number='90')
        self.member = make_user(
            'ad-mem', name='Dana Delete', preferred_name='Dee', email='dana@example.com',
            other_email='dana2@example.com', phone_number='5551234567', instagram='dana_ig',
            twitter='dana_x', linkedin='dana-li', snapchat='dana_snap', facebook='dana.fb',
            custom_socials=[{'platform': 'Mastodon', 'handle': '@dana'}], about_me='Hi, I am Dana.',
            majors=['History'], minors=['Art'], concentrations=['Medieval'], role_number='123',
            pledge_class='Fall 2023', pledge_class_greek='Omega', house='Smith', big_brother=self.big,
            graduation_year=2027, graduation_semester='Spring', onboarding_data={'pages_visited': ['/']},
            initiation_chapters=[{'school': 'Samford', 'chapter': 'Alpha Mu'}],
        )
        self.member.profile_picture = SimpleUploadedFile('dana.png', PNG, content_type='image/png')
        self.member.save()
        self.role = Role.objects.create(name='Treasurer', code='TRES')
        self.member.roles.add(self.role)
        RoleHistory.objects.create(user=self.member, role_name='Treasurer', start_semester='Fall 2025')

    def request(self, user=None, via='web'):
        return deletion.request_deletion(user or self.member, via)

    def reload(self):
        self.member = ParliamentUser.objects.get(pk=self.member.pk)
        return self.member


class RequestTests(_Base):

    def test_signs_out_everywhere_and_cannot_sign_in(self):
        device, access, _ = create_device(self.member, 'password', 'ios', device_name='Phone')
        WebAuthnCredential.objects.create(user=self.member, credential_id=b'cred-1', public_key=b'pk')
        TOTPDevice.objects.create(user=self.member, name='app', confirmed=True)
        web = Client()
        self.assertTrue(web.login(username='ad-mem', password=PASSWORD))

        when = self.request()
        self.assertEqual(when, timezone.localdate() + timedelta(days=30))
        m = self.reload()
        self.assertFalse(m.is_active)
        self.assertFalse(m.has_usable_password())
        self.assertIsNotNone(m.deletion_requested_at)
        self.assertEqual(m.deletion_requested_via, 'web')
        # Every sign-in route is gone.
        self.assertFalse(Client().login(username='ad-mem', password=PASSWORD))
        self.assertEqual(WebAuthnCredential.objects.filter(user=m).count(), 0)
        self.assertEqual(TOTPDevice.objects.filter(user=m).count(), 0)
        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)
        self.assertEqual(device.revoked_reason, 'account deleted')
        self.assertFalse(device.still_allowed(m)[0])
        # The already signed-in browser session is dead too.
        r = web.get(reverse('preferences'))
        self.assertEqual(r.status_code, 302)
        # Nothing personal erased yet (a restore can bring it all back).
        self.assertEqual(m.email, 'dana@example.com')
        self.assertEqual(m.phone_number, '5551234567')
        self.assertTrue(m.profile_picture)

    def test_mobile_token_rejected(self):
        _, access, _ = create_device(self.member, 'password', 'ios', device_name='Phone')
        api = Client()
        self.assertEqual(api.get('/api/mobile/v1/me/', HTTP_AUTHORIZATION=f'Bearer {access}').status_code, 200)
        self.request(via='app')
        self.assertEqual(api.get('/api/mobile/v1/me/', HTTP_AUTHORIZATION=f'Bearer {access}').status_code, 401)

    def test_confirmation_email_and_admin_notified(self):
        mail.outbox = []
        when = self.request()
        to_member = [e for e in mail.outbox if e.to == ['dana@example.com']]
        self.assertEqual(len(to_member), 1)
        body = to_member[0].body
        self.assertIn(f'{when:%B} {when.day}, {when.year}', body)
        self.assertIn('contact a chapter officer within 30 days', body)
        self.assertIn('closed now', body)
        to_admin = [e for e in mail.outbox if 'admin@example.com' in e.to]
        self.assertEqual(len(to_admin), 1)
        self.assertIn('Treasurer', to_admin[0].body)          # roles held, for a successor
        n = Notification.objects.get(recipient=self.admin)
        self.assertIn('Treasurer', n.message)
        self.assertEqual(n.link, reverse('admin_v2_account_deletions'))
        self.assertFalse(Notification.objects.filter(recipient=self.member).exists())
        self.assertTrue(AdminActionLog.objects.filter(action='account_deletion_requested',
                                                      target_user=self.member).exists())

    def test_superuser_refused(self):
        with self.assertRaises(deletion.DeletionRefused) as ctx:
            self.request(self.admin)
        self.assertEqual(ctx.exception.code, 'not_allowed')
        self.assertIn('another admin', ctx.exception.message)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_twice_refused(self):
        self.request()
        with self.assertRaises(deletion.DeletionRefused) as ctx:
            self.request(self.reload())
        self.assertEqual(ctx.exception.code, 'already_deleted')


class FormerMemberDisplayTests(_Base):
    """Shown like the founders: present as a record, not as a usable person."""

    def setUp(self):
        super().setUp()
        self.request()
        self.viewer = make_user('ad-view', name='Vic Viewer', member_type='Officer')
        self.web = Client()
        self.web.force_login(self.viewer)

    def test_directory_lists_record_only(self):
        r = self.web.get(reverse('member_directory'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.member, r.context['members'])
        self.assertEqual(r.context['former_members'], [])     # alumni view only, like founders
        r = self.web.get(reverse('member_directory') + '?show_alumni=1')
        self.assertEqual([m.pk for m in r.context['former_members']], ['ad-mem'])
        self.assertNotIn(self.member.pk, [m.pk for m in r.context['alumni']])
        html = r.content.decode()
        self.assertIn('Former Members (1)', html)
        self.assertIn('Former member', html)
        for secret in ('dana@example.com', '5551234567', 'dana.png'):
            self.assertNotIn(secret, html)

    def test_card_is_record_only(self):
        card = self.web.get(reverse('profile_card', args=['ad-mem'])).json()
        self.assertTrue(card['former_member'])
        self.assertEqual(card['member_status'], 'Former member')
        self.assertEqual(card['name'], 'Dee Delete')
        self.assertEqual(card['role_number'], '123')
        self.assertEqual(card['pledge_class'], 'Fall 2023')
        self.assertEqual(card['house'], 'Smith')
        self.assertEqual(card['big_brother']['user_id'], 'ad-big')
        self.assertEqual([h['role'] for h in card['role_history']], ['Treasurer'])
        for key in ('email', 'other_email', 'phone', 'profile_picture_url', 'about_me'):
            self.assertEqual(card[key], '', key)
        self.assertEqual(card['socials'], {})
        self.assertEqual(card['custom_socials'], [])
        self.assertEqual(card['academics'], {'majors': [], 'minors': [], 'concentrations': []})

    def test_house_map_keeps_family_line(self):
        from src.view.house_map import house_map_data
        houses, _, unassigned = house_map_data()
        smith = next(h for h in houses if h['code'] == 'Smith')

        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n['littles'])
        node = next(n for n in walk(smith['trees']) if n['user_id'] == 'ad-mem')
        self.assertTrue(node['former_member'])
        self.assertEqual(node['member_status'], 'Former member')
        self.assertEqual(smith['active'], 0)          # not counted as an active member
        self.assertNotIn(self.member.pk, [m.pk for m in unassigned])
        r = self.web.get(reverse('house_map'))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('dana@example.com', r.content.decode())

    def test_search_finds_record(self):
        from src.view.global_search import search_everything
        results, _ = search_everything('Dana Delete', self.viewer)
        self.assertIn('ad-mem', [u.pk for u in results.get('users', [])])
        results, _ = search_everything('123', self.viewer)
        self.assertIn('ad-mem', [u.pk for u in results.get('users', [])])
        r = self.web.get(reverse('global_search') + '?q=Dana')
        self.assertNotIn('dana@example.com', r.content.decode())


class ExcludedFromActiveTests(_Base):
    """Not an active member anywhere: rosters, quorum, audiences."""

    def setUp(self):
        super().setUp()
        self.request()

    def test_by_status_filters_leave_them_out(self):
        self.assertFalse(ParliamentUser.objects.filter(member_status='Active', pk='ad-mem').exists())
        self.assertFalse(ParliamentUser.active.filter(pk='ad-mem').exists())
        self.assertFalse(ParliamentUser.objects.filter(member_status__in=['Active', 'Alumni'],
                                                       pk='ad-mem').exists())
        # Lookups that aren't by status still find the record.
        self.assertTrue(ParliamentUser.objects.filter(pk='ad-mem').exists())
        self.assertTrue(ParliamentUser.objects.filter(name='Dana Delete').exists())
        self.assertTrue(ParliamentUser.objects.including_closed()
                        .filter(member_status='Active', pk='ad-mem').exists())
        self.assertEqual(self.role.parliamentuser_set.filter(member_status='Active').count(), 0)

    def test_notification_and_email_audiences(self):
        from src.notification_service import notify_all_active_members
        from src.notifications import announcement_email_recipients
        Notification.objects.all().delete()
        notify_all_active_members('announcement', 'Hello all')
        self.assertFalse(Notification.objects.filter(recipient=self.member).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.big).exists())
        self.assertNotIn(self.member.pk, [u.pk for u in announcement_email_recipients(None)])


class RestoreTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.member = make_user('rs-mem', name='Rita Restore', email='rita@example.com', phone_number='5550000')
        self.device, _, _ = create_device(self.member, 'password', 'ios', device_name='Phone')
        deletion.request_deletion(self.member, 'app')

    def test_pending_list_and_restore_at_members_request(self):
        r = self.client.get(reverse('admin_v2_account_deletions'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('Rita Restore', html)
        self.assertIn("Restore at the member's request", html)
        self.assertIn((timezone.localdate() + timedelta(days=30)).strftime('%b'), html)

        r = self.client.post(reverse('admin_v2_restore_account', args=['rs-mem']))
        self.assertEqual(r.status_code, 302)
        m = ParliamentUser.objects.get(pk='rs-mem')
        self.assertTrue(m.is_active)
        self.assertIsNone(m.deletion_requested_at)
        self.assertTrue(m.has_usable_password())          # so "Forgot password" works
        self.assertEqual(m.phone_number, '5550000')       # nothing was lost
        self.assertTrue(ParliamentUser.objects.filter(member_status='Active', pk='rs-mem').exists())
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.revoked_at)       # devices stay signed out
        log = AdminActionLog.objects.get(action='account_restored')
        self.assertEqual(log.actor, self.admin)
        self.assertEqual(log.target_user, m)
        # The normal password-reset flow reaches them.
        mail.outbox = []
        Client().post(reverse('password_reset'), {'email': 'rita@example.com'})
        self.assertEqual(len(mail.outbox), 1)

    def test_restore_needs_post(self):
        self.assertEqual(self.client.get(reverse('admin_v2_restore_account', args=['rs-mem'])).status_code, 405)

    def test_restore_refused_after_purge(self):
        deletion.purge_account(self.member)
        self.client.post(reverse('admin_v2_restore_account', args=['rs-mem']))
        self.assertFalse(ParliamentUser.objects.get(pk='rs-mem').is_active)


class PurgeTests(_Base):

    def setUp(self):
        super().setUp()
        leg = Legislation.objects.create(title='Bill 1', description='d', posted_by=self.big,
                                         available_at=timezone.now(), vote_mode='percentage',
                                         required_percentage='51', document='test.pdf')
        self.vote = Vote.objects.create(user=self.member, legislation=leg, vote_choice='yes')
        self.att = Attendance.objects.create(user=self.member, status='present')
        self.minutes = ChapterMinutes.objects.create(title='Week 1', date=date(2026, 10, 1), start_time=time(19),
                                                     created_by=self.member)
        LoginHistory.objects.create(user=self.member, status='success', ip_address='203.0.113.1')
        PageVisit.objects.create(user=self.member, path='/home/')
        self.log = ActivityLog.objects.create(user=self.member, action_category='vote', action_type='vote_cast',
                                              description='Voted', ip_address='203.0.113.9', user_agent='UA')
        self.picture = self.member.profile_picture.name
        self.request()
        self.reload()

    def _age(self, days):
        ParliamentUser.objects.filter(pk=self.member.pk).update(
            deletion_requested_at=timezone.now() - timedelta(days=days))

    def test_not_purged_inside_the_window(self):
        self._age(29)
        self.assertEqual(deletion.purge_due_deletions(), 0)
        self.assertEqual(self.reload().email, 'dana@example.com')

    def test_purge_erases_exactly_the_deleted_list(self):
        from django.core.files.storage import default_storage
        self._age(30)
        self.assertEqual(deletion.purge_due_deletions(), 1)
        m = self.reload()
        # Erased.
        self.assertIsNone(m.email)
        self.assertIsNone(m.other_email)
        for f in ('phone_number', 'instagram', 'twitter', 'linkedin', 'snapchat', 'facebook', 'about_me'):
            self.assertEqual(getattr(m, f), '', f)
        for f in ('custom_socials', 'majors', 'minors', 'concentrations'):
            self.assertEqual(getattr(m, f), [], f)
        self.assertEqual(m.onboarding_data, {})
        self.assertFalse(m.profile_picture)
        self.assertFalse(default_storage.exists(self.picture))
        self.assertEqual(m.username, 'deleted-ad-mem')
        self.assertIsNotNone(m.account_deleted_at)
        self.assertFalse(LoginHistory.objects.filter(user=m).exists())
        self.assertFalse(PageVisit.objects.filter(user=m).exists())
        self.assertFalse(UserPreferences.objects.filter(user=m).exists())
        self.assertFalse(MobileDevice.objects.filter(user=m).exists())
        self.assertFalse(UserSession.objects.filter(user=m).exists())
        self.log.refresh_from_db()
        self.assertEqual((self.log.ip_address, self.log.user_agent, self.log.description), (None, '', 'Voted'))
        # Kept: the membership record.
        self.assertEqual(m.name, 'Dana Delete')
        self.assertEqual(m.preferred_name, 'Dee')
        self.assertEqual(m.role_number, '123')
        self.assertEqual((m.pledge_class, m.pledge_class_greek, m.house), ('Fall 2023', 'Omega', 'Smith'))
        self.assertEqual(m.big_brother_id, 'ad-big')
        self.assertEqual((m.graduation_year, m.graduation_semester), (2027, 'Spring'))
        self.assertEqual(m.initiation_chapters, [{'school': 'Samford', 'chapter': 'Alpha Mu'}])
        self.assertEqual(m.member_status, 'Active')
        self.assertEqual(list(m.roles.all()), [self.role])
        self.assertTrue(RoleHistory.objects.filter(user=m, role_name='Treasurer').exists())
        # Participation, still by name.
        self.assertEqual(Vote.objects.get(pk=self.vote.pk).user.name, 'Dana Delete')
        self.assertEqual(Attendance.objects.get(pk=self.att.pk).user.name, 'Dana Delete')
        self.assertEqual(ChapterMinutes.objects.get(pk=self.minutes.pk).created_by.name, 'Dana Delete')
        self.assertTrue(AdminActionLog.objects.filter(action='account_purged', target_user=m).exists())

    def test_purge_is_idempotent(self):
        self._age(31)
        self.assertEqual(deletion.purge_due_deletions(), 1)
        self.assertEqual(deletion.purge_due_deletions(), 0)
        self.assertFalse(deletion.purge_account(self.reload()))     # a second run is harmless
        self.assertEqual(AdminActionLog.objects.filter(action='account_purged').count(), 1)
        self.assertEqual(self.reload().name, 'Dana Delete')

    def test_task_runs_it_and_is_scheduled(self):
        from src.management.commands.setup_celery_schedules import SCHEDULES
        from src.tasks.cleanup import purge_deleted_accounts
        self._age(30)
        self.assertEqual(purge_deleted_accounts(), 1)
        self.assertIn('tasks.purge_deleted_accounts', [s['task'] for s in SCHEDULES])

    def test_restored_account_is_never_purged(self):
        deletion.restore_account(self.member, self.admin)
        self._age(40)   # a stale timestamp can't matter: restore cleared the request
        ParliamentUser.objects.filter(pk=self.member.pk).update(deletion_requested_at=None)
        self.assertEqual(deletion.purge_due_deletions(), 0)
        self.assertEqual(self.reload().email, 'dana@example.com')


class RebuildTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.member = make_user('rb-mem', name='Rex Rebuild', email='rex@example.com', phone_number='5559999',
                                role_number='77', about_me='old bio')
        RoleHistory.objects.create(user=self.member, role_name='Secretary', start_semester='Fall 2024')

    def _purge(self):
        deletion.request_deletion(self.member, 'web')
        deletion.purge_account(ParliamentUser.objects.get(pk='rb-mem'))

    def test_rebuild_reattaches_sign_in_to_the_same_record(self):
        self._purge()
        r = self.client.get(reverse('admin_v2_account_deletions'))
        self.assertIn('Rex Rebuild', r.content.decode())
        mail.outbox = []
        r = self.client.post(reverse('admin_v2_rebuild_account', args=['rb-mem']),
                             {'email': 'Rex.New@Example.com', 'username': 'rexnew'})
        self.assertEqual(r.status_code, 302)
        m = ParliamentUser.objects.get(pk='rb-mem')
        self.assertTrue(m.is_active)
        self.assertEqual((m.email, m.username), ('rex.new@example.com', 'rexnew'))
        self.assertIsNone(m.deletion_requested_at)
        self.assertIsNone(m.account_deleted_at)
        # Same record, none of the erased data.
        self.assertEqual((m.name, m.role_number), ('Rex Rebuild', '77'))
        self.assertTrue(RoleHistory.objects.filter(user=m, role_name='Secretary').exists())
        self.assertEqual((m.phone_number, m.about_me), ('', ''))
        self.assertTrue(UserPreferences.objects.filter(user=m).exists())
        self.assertTrue(ParliamentUser.objects.filter(member_status='Active', pk='rb-mem').exists())
        # The set-password link works and then sign-in works.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['rex.new@example.com'])
        from urllib.parse import urlparse
        link = next(w for w in mail.outbox[0].body.split() if w.startswith('http'))
        path = urlparse(link).path
        self.assertTrue(path.startswith(reverse('password_reset_confirm', args=['u', 't'])[:-4]))
        c = Client()
        r = c.get(path, follow=True)
        self.assertEqual(r.status_code, 200)
        r = c.post(r.redirect_chain[-1][0] if r.redirect_chain else path,
                   {'new_password1': 'Brand-new-pass-4567!', 'new_password2': 'Brand-new-pass-4567!'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Client().login(username='rexnew', password='Brand-new-pass-4567!'))
        log = AdminActionLog.objects.get(action='account_rebuilt')
        self.assertEqual((log.actor, log.target_user), (self.admin, m))

    def test_rebuild_refused_unless_purged(self):
        for setup in (lambda: None, lambda: deletion.request_deletion(self.member, 'web')):
            setup()
            self.client.post(reverse('admin_v2_rebuild_account', args=['rb-mem']), {'email': 'x@example.com'})
            m = ParliamentUser.objects.get(pk='rb-mem')
            self.assertNotEqual(m.email, 'x@example.com')
            with self.assertRaises(deletion.DeletionRefused) as ctx:
                deletion.rebuild_account(m, self.admin, 'x@example.com')
            self.assertEqual(ctx.exception.code, 'not_purged')
        self.assertFalse(AdminActionLog.objects.filter(action='account_rebuilt').exists())

    def test_rebuild_refuses_a_taken_email(self):
        self._purge()
        make_user('rb-taken', email='taken@example.com')
        with self.assertRaises(deletion.DeletionRefused) as ctx:
            deletion.rebuild_account(ParliamentUser.objects.get(pk='rb-mem'), self.admin, 'Taken@example.com')
        self.assertEqual(ctx.exception.code, 'email_taken')
        self.assertFalse(ParliamentUser.objects.get(pk='rb-mem').is_active)

    def test_not_self_service(self):
        """The public site has no way to claim a record: rebuild is behind admin-v2."""
        self._purge()
        other = make_user('rb-other', name='Olly Other')
        c = Client()
        c.force_login(other)
        r = c.post(reverse('admin_v2_rebuild_account', args=['rb-mem']), {'email': 'olly@example.com'})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(ParliamentUser.objects.get(pk='rb-mem').email)


class WebFlowTests(_Base):

    def setUp(self):
        super().setUp()
        self.web = Client()
        self.web.force_login(self.member)
        self.url = reverse('delete_account')

    def test_linked_from_preferences(self):
        self.assertIn(self.url, self.web.get(reverse('preferences')).content.decode())

    def test_page_explains(self):
        html = self.web.get(self.url).content.decode()
        self.assertIn('What the chapter keeps', html)
        self.assertIn('within 30 days', html)
        self.assertIn('Treasurer', html)

    def test_wrong_password(self):
        r = self.web.post(self.url, {'password': 'nope', 'confirm': 'DELETE'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Incorrect password', r.content.decode())
        self.assertTrue(self.reload().is_active)

    def test_must_type_delete(self):
        r = self.web.post(self.url, {'password': PASSWORD, 'confirm': 'delete'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.reload().is_active)

    def test_deletes_and_logs_out(self):
        r = self.web.post(self.url, {'password': PASSWORD, 'confirm': 'DELETE'})
        self.assertEqual(r.status_code, 200)
        when = timezone.localdate() + timedelta(days=30)
        self.assertIn(f'{when:%B} {when.day}, {when.year}', r.content.decode())
        self.assertIsNotNone(self.reload().deletion_requested_at)
        self.assertEqual(self.web.get(reverse('preferences')).status_code, 302)

    def test_passkey_only_member_uses_the_passkey_grant(self):
        self.member.set_unusable_password()
        self.member.save()
        self.web.force_login(self.member)
        r = self.web.post(self.url, {'confirm': 'DELETE'})
        self.assertEqual(r.status_code, 400)
        session = self.web.session
        session['webauthn_reauth_grant_at'] = timezone.now().isoformat()
        session.save()
        r = self.web.post(self.url, {'confirm': 'DELETE', 'auth_method': 'passkey'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(self.reload().deletion_requested_at)

    def test_admin_refused(self):
        c = Client()
        c.force_login(self.admin)
        self.assertIn("Site administrators can't delete their own account", c.get(self.url).content.decode())
        r = c.post(self.url, {'password': PASSWORD, 'confirm': 'DELETE'})
        self.assertEqual(r.status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class KaiConfidentialityTests(_Base):
    """Deletion and purge don't touch Kai records, and nothing about them leaks."""

    def test_kai_case_untouched_and_not_disclosed(self):
        report = KaiReport.objects.create(title='Secret matter', category='behavioral', description='private',
                                          submitted_by=self.member, targeted_to=self.big)
        mail.outbox = []
        self.request()
        ParliamentUser.objects.filter(pk=self.member.pk).update(
            deletion_requested_at=timezone.now() - timedelta(days=30))
        deletion.purge_due_deletions()
        report.refresh_from_db()
        self.assertEqual(report.submitted_by_id, 'ad-mem')
        self.assertEqual(report.submitter_display_name, 'Dana Delete')
        self.assertEqual(report.description, 'private')
        for e in mail.outbox:
            self.assertNotIn('Secret matter', e.body)
        for n in Notification.objects.all():
            self.assertNotIn('Secret matter', n.message)
        for log in AdminActionLog.objects.all():
            self.assertNotIn('Secret matter', log.detail)
