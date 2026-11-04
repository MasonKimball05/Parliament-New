"""v4.4.4 admin-v2 chapter health: the gate, each section, and a flat query count."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.mobile.tokens import create_device
from src.models import Attendance, Event, ServicePeriod
from src.models.education import PledgeTask, PledgeTaskCompletion
from src.models.mobile import MobileDevice
from src.models.service import ServiceHoursAdjustment, ServiceHoursSubmission
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view import admin_v2_health
from src.view.admin_v2_health import chapter_health, clean_days


def ids(rows):
    return [r['user_id'] for r in rows]


class GateTests(AdminV2TestCase):

    def test_admin_v2_session_sees_the_page(self):
        r = self.client.get(reverse('admin_v2_health'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Chapter health')
        self.assertContains(r, 'aria-current="page"')  # active in the sidebar

    def test_signed_in_admin_without_the_step_up_is_sent_to_admin_v2_login(self):
        client = Client()
        client.force_login(self.admin)
        r = client.get(reverse('admin_v2_health'))
        self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False)

    def test_member_off_the_allowlist_is_refused(self):
        other = make_user('hl-other', is_admin=True)
        client = Client()
        client.force_login(other)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
        r = client.get(reverse('admin_v2_health'))
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)

    def test_anonymous_goes_to_login(self):
        r = Client().get(reverse('admin_v2_health'))
        self.assertRedirects(r, reverse('login'), fetch_redirect_response=False)

    def test_bad_days_falls_back_to_30(self):
        self.assertEqual(clean_days('abc'), 30)
        self.assertEqual(clean_days('45'), 30)
        self.assertEqual(clean_days('90'), 90)
        self.assertEqual(self.client.get(reverse('admin_v2_health') + '?days=x').status_code, 200)


class InactiveTests(AdminV2TestCase):

    def test_inactive_windows_never_and_who_is_left_out(self):
        now = timezone.now()
        recent = make_user('hl-recent', last_seen_at=now - timedelta(days=5))
        forty = make_user('hl-forty', last_seen_at=now - timedelta(days=40))
        seventy = make_user('hl-seventy', last_login=now - timedelta(days=70))
        never = make_user('hl-never')
        app_only = make_user('hl-app-only', last_seen_at=now - timedelta(days=200))
        device, _access, _ = create_device(app_only, 'password', 'ios')
        MobileDevice.objects.filter(pk=device.pk).update(last_seen_at=now - timedelta(days=1))
        # Left out: alumni, advisors, a closed account.
        make_user('hl-alum', member_status='Alumni')
        make_user('hl-advisor', member_type='Advisor')
        make_user('hl-closed', deletion_requested_at=now)

        h30 = chapter_health(30)['inactive']
        self.assertEqual(ids(h30['members']), [seventy.user_id, forty.user_id])
        self.assertIn(never.user_id, ids(h30['never']))
        for uid in ('hl-alum', 'hl-advisor', 'hl-closed', recent.user_id, app_only.user_id):
            self.assertNotIn(uid, ids(h30['members']) + ids(h30['never']))
        self.assertEqual(ids(chapter_health(60)['inactive']['members']), [seventy.user_id])
        self.assertEqual(chapter_health(90)['inactive']['n'], 0)

    def test_page_links_to_member_page(self):
        m = make_user('hl-link', last_seen_at=timezone.now() - timedelta(days=100))
        r = self.client.get(reverse('admin_v2_health') + '?days=90')
        self.assertContains(r, reverse('admin_v2_member', args=[m.user_id]))
        self.assertContains(r, 'Not seen in 90 days')


class AttendanceTests(AdminV2TestCase):

    def _meeting(self, finalized=True, **kw):
        return Event.objects.create(title='Chapter', date_time=timezone.now() - timedelta(minutes=5),
                                    created_by=self.admin, requires_attendance=True,
                                    attendance_finalized=finalized, **kw)

    def test_rate_lowest_first_and_pledges_out(self):
        good = make_user('hl-good')
        poor = make_user('hl-poor')
        pledge = make_user('hl-pledge', member_type='Pledge')
        meetings = [self._meeting() for _ in range(4)]
        for i, e in enumerate(meetings):
            Attendance.objects.create(event=e, user=good, status='present' if i else 'late')
            Attendance.objects.create(event=e, user=poor, status='absent' if i else 'present')
            Attendance.objects.create(event=e, user=pledge, status='absent')
        # Not counted: an unfinalized meeting.
        Attendance.objects.create(event=self._meeting(finalized=False), user=good, status='absent')

        a = chapter_health(30)['attendance']
        self.assertEqual(a['meetings'], 4)
        lowest = a['lowest']
        self.assertEqual(lowest[0]['user_id'], poor.user_id)
        self.assertEqual(lowest[0]['rate'], 25)
        self.assertEqual(lowest[1]['rate'], 100)
        self.assertNotIn(pledge.user_id, ids(lowest))
        # 5 present/late of 12 records (pledge absences count in the chapter rate).
        self.assertEqual(a['overall'], round(5 / 12 * 100))
        self.assertTrue(any(w['rate'] is not None for w in a['weeks']))
        self.assertTrue(all(w['week'].weekday() == 0 for w in a['weeks']))

    def test_no_meetings(self):
        a = chapter_health(30)['attendance']
        self.assertEqual(a['meetings'], 0)
        self.assertIsNone(a['overall'])
        self.assertContains(self.client.get(reverse('admin_v2_health')), 'No finalized meetings this semester')


class AppTests(AdminV2TestCase):

    def test_members_without_a_live_device(self):
        has_app = make_user('hl-has-app')
        create_device(has_app, 'password', 'ios')
        revoked = make_user('hl-revoked')
        d, _a, _ = create_device(revoked, 'password', 'android')
        MobileDevice.objects.filter(pk=d.pk).update(revoked_at=timezone.now())
        none = make_user('hl-none')
        app = chapter_health(30)['app']
        self.assertIn(revoked.user_id, ids(app['members']))
        self.assertIn(none.user_id, ids(app['members']))
        self.assertNotIn(has_app.user_id, ids(app['members']))


class PledgeTests(AdminV2TestCase):

    def test_overdue_tasks_by_pledge(self):
        today = timezone.localdate()
        behind = make_user('hl-p-behind', member_type='Pledge')
        caught_up = make_user('hl-p-ok', member_type='Pledge')
        waived = make_user('hl-p-waived', member_type='Pledge')
        late = PledgeTask.objects.create(title='Learn the creed', due_date=today - timedelta(days=3))
        PledgeTask.objects.create(title='Future task', due_date=today + timedelta(days=3))
        PledgeTask.objects.create(title='Phase 3 task', phase='3', due_date=today - timedelta(days=3))
        PledgeTask.objects.create(title='Hidden task', is_active=False, due_date=today - timedelta(days=3))
        mine = PledgeTask.objects.create(title='Only for caught up', due_date=today - timedelta(days=1))
        mine.assigned_to.set([caught_up])
        PledgeTaskCompletion.objects.create(task=late, pledge=caught_up, status='completed')
        PledgeTaskCompletion.objects.create(task=mine, pledge=caught_up, status='completed')
        PledgeTaskCompletion.objects.create(task=late, pledge=waived, status='waived')

        p = chapter_health(30)['pledges']
        self.assertEqual(p['pledges'], 3)
        self.assertEqual(ids(p['members']), [behind.user_id])
        row = p['members'][0]
        self.assertEqual(row['overdue'], 1)
        self.assertEqual(row['titles'], ['Learn the creed'])
        self.assertEqual((row['completed'], row['required']), (0, 2))


class ServiceTests(AdminV2TestCase):

    def test_below_requirement_with_adjustments_and_overrides(self):
        today = timezone.localdate()
        period = ServicePeriod.objects.create(name='Fall', start_date=today - timedelta(days=30),
                                              end_date=today + timedelta(days=30),
                                              default_hours_required=Decimal('10'))
        done = make_user('hl-s-done')
        short = make_user('hl-s-short')
        adjusted = make_user('hl-s-adj')
        exempt = make_user('hl-s-exempt')
        pledge = make_user('hl-s-pledge', member_type='Pledge')
        for who, hours, status in ((done, '10', 'approved'), (short, '4', 'approved'),
                                   (short, '6', 'pending'), (adjusted, '5', 'approved')):
            ServiceHoursSubmission.objects.create(period=period, submitted_by=who, hours=Decimal(hours),
                                                  status=status, service_date=today, organization='Org',
                                                  description='d')
        ServiceHoursAdjustment.objects.create(period=period, member=adjusted, hours=Decimal('5'),
                                              reason='makeup', adjusted_by=self.admin)
        period.member_expectations.create(member=exempt, expected_hours=Decimal('0'))
        s = chapter_health(30)['service']
        below = ids(s['members'])
        self.assertIn(short.user_id, below)
        self.assertIn(pledge.user_id, below)  # pledges owe hours
        for uid in (done.user_id, adjusted.user_id, exempt.user_id):
            self.assertNotIn(uid, below)
        row = next(r for r in s['members'] if r['user_id'] == short.user_id)
        self.assertEqual((row['hours'], row['expected'], row['percent']), (Decimal('4'), Decimal('10'), 40))

    def test_no_active_period(self):
        self.assertIsNone(chapter_health(30)['service']['period'])
        self.assertContains(self.client.get(reverse('admin_v2_health')), 'No active service period')


class QueryCountTests(AdminV2TestCase):

    def _populate(self, n, tag):
        today = timezone.localdate()
        event = Event.objects.create(title='Mtg', date_time=timezone.now() - timedelta(minutes=5),
                                     created_by=self.admin, requires_attendance=True, attendance_finalized=True)
        task = PledgeTask.objects.create(title=f'T{tag}', due_date=today - timedelta(days=1))
        for i in range(n):
            m = make_user(f'hq-{tag}-{i}', last_seen_at=timezone.now() - timedelta(days=45))
            p = make_user(f'hq-{tag}-p{i}', member_type='Pledge')
            Attendance.objects.create(event=event, user=m, status='absent')
            task.assigned_to.add(p)
            create_device(m, 'password', 'ios')

    def _count(self):
        with CaptureQueriesContext(connection) as ctx:
            chapter_health(30)
        return len(ctx.captured_queries)

    def test_flat_in_roster_size(self):
        ServicePeriod.objects.create(name='P', start_date=timezone.localdate() - timedelta(days=1),
                                     end_date=timezone.localdate() + timedelta(days=60),
                                     default_hours_required=Decimal('5'))
        self._populate(2, 'a')
        small = self._count()
        self._populate(8, 'b')
        self.assertEqual(self._count(), small)
        self.assertLessEqual(small, 15)

    def test_page_is_cached_briefly(self):
        self.client.get(reverse('admin_v2_health'))
        with mock.patch.object(admin_v2_health, 'chapter_health', wraps=admin_v2_health.chapter_health) as compute:
            self.client.get(reverse('admin_v2_health'))
            compute.assert_not_called()
            self.client.get(reverse('admin_v2_health') + '?days=60')
            compute.assert_called_once()
