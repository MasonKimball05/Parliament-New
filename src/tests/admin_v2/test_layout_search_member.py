"""
v3.58.1 admin-v2: grouped sidebar, ⌘K search, member page, "Needs attention".
"""
from datetime import timedelta
from unittest import mock

from django.test import Client
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from src.admin_v2_attention import attention_items
from src.admin_v2_nav import NAV, search_pages
from src.mobile.tokens import create_device
from src.models import BugReport, LoginLockout
from src.models.mobile import MobileDevice
from src.models.users import UserSession
from src.tests.admin_v2.base import AdminV2TestCase, make_user


class LayoutTests(AdminV2TestCase):

    def test_sidebar_groups_and_active_item(self):
        r = self.client.get(reverse('admin_v2_manage_users'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for group in ('People', 'Content', 'Comms', 'Security', 'System'):
            self.assertIn(group, html)
        self.assertIn('aria-current="page">Members</a>', html)
        self.assertIn('id="admin-v2-palette"', html)
        self.assertIn('js/admin_v2/palette.js', html)
        # No inline handlers (prod CSP blocks them).
        self.assertNotIn('onclick=', html)

    def test_every_nav_item_routes_or_is_skipped(self):
        # Items whose route doesn't exist yet are skipped, never a 500: but
        # every item shipped in this release must route.
        missing = []
        for _title, items in NAV:
            for label, name, _also, _kw in items:
                try:
                    reverse(name)
                except NoReverseMatch:
                    missing.append(label)
        self.assertEqual(missing, [])


class SearchTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.alice = make_user('av2-alice', name='Alice Anderson', email='alice@example.test')

    def test_pages_by_keyword(self):
        self.assertEqual(search_pages('2fa')[0]['label'], 'Two-factor')
        labels = [p['label'] for p in search_pages('ip')]
        self.assertIn('IP block list', labels)
        self.assertEqual(search_pages(''), [])

    def test_members_by_name_username_and_number(self):
        for q in ('anderson', 'av2-alice', '#av2-alice', 'alice@example'):
            with self.subTest(q=q):
                data = self.client.get(reverse('admin_v2_search'), {'q': q}).json()
                self.assertIn(reverse('admin_v2_member', args=['av2-alice']),
                              [m['url'] for m in data['members']])

    def test_empty_query_offers_a_page_per_section(self):
        data = self.client.get(reverse('admin_v2_search')).json()
        self.assertEqual(data['members'], [])
        self.assertGreaterEqual(len(data['pages']), 5)

    def test_requires_admin_v2(self):
        c = Client()
        c.force_login(self.alice)
        r = c.get(reverse('admin_v2_search'), {'q': 'a'})
        self.assertNotEqual(r.status_code, 200)


class MemberPageTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.bob = make_user('av2-bob', name='Bob Brown')
        self.device, _access, _refresh = create_device(self.bob, 'password_totp', 'ios')
        self.other = make_user('av2-other')
        self.other_device, _a, _r = create_device(self.other, 'password', 'android')

    def test_page_shows_devices_and_actions(self):
        r = self.client.get(reverse('admin_v2_member', args=['av2-bob']))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Bob Brown')
        self.assertContains(r, '1 signed in')
        self.assertContains(r, reverse('admin_v2_member_revoke_device', args=['av2-bob', self.device.public_id]))
        self.assertNotContains(r, 'onclick=')

    def test_revoke_one_device(self):
        url = reverse('admin_v2_member_revoke_device', args=['av2-bob', self.device.public_id])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.revoked_at)
        self.assertEqual(self.device.push_token, '')

    def test_cannot_revoke_another_members_device_through_this_member(self):
        url = reverse('admin_v2_member_revoke_device', args=['av2-bob', self.other_device.public_id])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.other_device.refresh_from_db()
        self.assertIsNone(self.other_device.revoked_at)

    def test_sign_out_everywhere(self):
        UserSession.objects.create(user=self.bob, session_key='av2bobsession000000000000000000000001')
        self.client.post(reverse('admin_v2_member_sign_out', args=['av2-bob']))
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.revoked_at)
        self.assertFalse(UserSession.objects.filter(user=self.bob).exists())
        self.assertFalse(MobileDevice.objects.filter(user=self.other, revoked_at__isnull=False).exists())

    def test_users_list_links_to_member_page(self):
        r = self.client.get(reverse('admin_v2_manage_users'))
        self.assertContains(r, reverse('admin_v2_member', args=['av2-bob']))

    def test_release_lockout_can_return_to_member_page(self):
        r = self.client.post(reverse('admin_v2_release_user_lockout', args=['av2-bob']), {'return_to': 'member'})
        self.assertRedirects(r, reverse('admin_v2_member', args=['av2-bob']), fetch_redirect_response=False)


class AttentionTests(AdminV2TestCase):

    def test_only_non_zero_items_worst_first(self):
        self.assertEqual(attention_items(), [])
        BugReport.objects.create(description='y', status='new')
        LoginLockout.objects.create(username='someone', expires_at=timezone.now() + timedelta(minutes=10))
        items = attention_items()
        self.assertEqual([i['label'] for i in items], ['locked-out sign-in(s)', 'new bug report(s)'])
        self.assertEqual(items[0]['severity'], 'warning')

    def test_a_broken_source_is_skipped(self):
        with mock.patch('src.models.BugReport.objects') as broken:
            broken.filter.side_effect = RuntimeError('db')
            attention_items()  # no exception

    def test_dashboard_shows_the_strip(self):
        r = self.client.get(reverse('admin_v2_dashboard'))
        self.assertContains(r, 'Needs attention')
        self.assertContains(r, 'All clear')


class MobileAdminTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        from src.models.mobile import MobileDevice as MD
        self.carl = make_user('av2-carl', name='Carl Chen')
        self.device, _a, _r = create_device(self.carl, 'password_totp', 'ios')
        MD.objects.filter(pk=self.device.pk).update(push_token='ExponentPushToken[abc123]', app_version='0.1.0')

    def test_page_shows_counts_versions_and_setup(self):
        r = self.client.get(reverse('admin_v2_mobile'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['stats']['total'], 1)
        self.assertEqual(r.context['stats']['push'], 1)
        self.assertContains(r, 'iPhone 0.1.0')
        self.assertContains(r, 'Expo access token')
        self.assertContains(r, 'value="av2-carl"')

    def test_test_push_goes_to_live_devices(self):
        with mock.patch('src.view.admin_v2_mobile.expo.send_to_devices', return_value=1) as send:
            r = self.client.post(reverse('admin_v2_mobile_push'), {'user_id': 'av2-carl'})
        self.assertRedirects(r, reverse('admin_v2_mobile'), fetch_redirect_response=False)
        devices = send.call_args.args[0]
        self.assertEqual([d.pk for d in devices], [self.device.pk])

    def test_test_push_with_no_device_sends_nothing(self):
        make_user('av2-nobody')
        with mock.patch('src.view.admin_v2_mobile.expo.send_to_devices') as send:
            self.client.post(reverse('admin_v2_mobile_push'), {'user_id': 'av2-nobody'})
        send.assert_not_called()


class OpsTests(AdminV2TestCase):

    def test_backups_reads_folder_and_drills(self):
        import os
        import tempfile
        from pathlib import Path

        from src.models import SecurityNotificationLog

        with tempfile.TemporaryDirectory() as d:
            Path(d, 'parliament_2026-10-01_020000.dump').write_bytes(b'x' * 10)
            old = Path(d, 'parliament_2026-09-01_020000.dump')
            old.write_bytes(b'x')
            os.utime(old, (0, 0))
            Path(d, 'notes.txt').write_text('ignored')
            SecurityNotificationLog.objects.create(event_type='BACKUP_VERIFIED', severity='low', details='ok')
            with mock.patch.dict(os.environ, {'PARLIAMENT_BACKUP_DIR': d}):
                r = self.client.get(reverse('admin_v2_backups'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([f['name'] for f in r.context['files']],
                         ['parliament_2026-10-01_020000.dump', 'parliament_2026-09-01_020000.dump'])
        self.assertIsNotNone(r.context['last_ok'])
        self.assertFalse(r.context['failing'])

    def test_backups_missing_folder_is_explained(self):
        import os
        import tempfile
        missing = os.path.join(tempfile.gettempdir(), 'parliament-backups-that-do-not-exist')
        with mock.patch.dict(os.environ, {'PARLIAMENT_BACKUP_DIR': missing}):
            r = self.client.get(reverse('admin_v2_backups'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'does not exist on this server')

    def test_a_newer_failure_shows_as_failing(self):
        from src.models import SecurityNotificationLog
        SecurityNotificationLog.objects.create(event_type='BACKUP_VERIFIED', severity='low', details='ok')
        SecurityNotificationLog.objects.create(event_type='BACKUP_VERIFY_FAILED', severity='critical', details='bad')
        r = self.client.get(reverse('admin_v2_backups'))
        self.assertTrue(r.context['failing'])

    def test_maintenance_toggle_returns_to_the_page(self):
        from src.models_feature_flags import FeatureFlag
        flag = FeatureFlag.objects.create(name='maintenance_mode', display_name='Maintenance', description='x',
                                          is_enabled=False)
        r = self.client.get(reverse('admin_v2_maintenance'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Start maintenance')
        r = self.client.post(reverse('toggle_feature_flag', args=[flag.id]), {'next': 'maintenance'})
        self.assertRedirects(r, reverse('admin_v2_maintenance'), fetch_redirect_response=False)
        flag.refresh_from_db()
        self.assertTrue(flag.is_enabled)


class MemberChapterSummaryTests(AdminV2TestCase):

    def test_attendance_rate_and_excuses(self):
        from src.models import Attendance, AttendanceExcuse, Event
        dana = make_user('av2-dana', name='Dana Doe')
        events = [Event.objects.create(title=f'Meeting {i}', date_time=timezone.now() - timedelta(days=i + 1),
                                       created_by=self.admin, requires_attendance=True) for i in range(4)]
        for event, status in zip(events, ('present', 'late', 'absent', 'excused')):
            Attendance.objects.create(event=event, user=dana, status=status, attendance_type='event')
        # Older than 90 days: not counted.
        old = Event.objects.create(title='Old', date_time=timezone.now() - timedelta(days=120),
                                   created_by=self.admin, requires_attendance=True)
        Attendance.objects.create(event=old, user=dana, status='absent', attendance_type='event')
        AttendanceExcuse.objects.create(event=events[0], user=dana, reason='sick', status='pending')

        r = self.client.get(reverse('admin_v2_member', args=['av2-dana']))
        chapter = r.context['chapter']
        self.assertEqual(chapter['attendance']['total'], 4)
        self.assertEqual(chapter['attendance']['rate'], 50)
        self.assertEqual(chapter['excuses_pending'], 1)
        self.assertContains(r, '50%')


class ContentSearchTests(AdminV2TestCase):

    def test_events_announcements_legislation_by_title(self):
        from src.models import Announcement, Event, Legislation
        Event.objects.create(title='Founders Day Dinner', date_time=timezone.now(), created_by=self.admin)
        Announcement.objects.create(title='Founders Day reminder', content='x', posted_by=self.admin)
        Legislation.objects.create(title='Founders Day budget', description='x', posted_by=self.admin,
                                   available_at=timezone.now())
        data = self.client.get(reverse('admin_v2_search'), {'q': 'founders'}).json()
        kinds = sorted(c['meta'].split(' · ')[0] for c in data['content'])
        self.assertEqual(kinds, ['Announcement', 'Event', 'Legislation'])
        self.assertEqual(self.client.get(reverse('admin_v2_search'), {'q': 'f'}).json()['content'], [])
