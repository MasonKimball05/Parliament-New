"""
v3.31.x — the daily digest's ACTIVITY SNAPSHOT and INFRASTRUCTURE / OPS
HEALTH sections, added 09-16-26 at Mason's request ("I want it to really
tell me what's going on") — the digest used to be almost entirely silent
unless something was flagged, which said nothing about an ordinary day.

Also covers the same request's third piece: admin-v2 links attached to the
highest-value HIGH/MEDIUM flags via flag()'s new `link=` parameter, so a
finding says where to go, not just what's wrong.

Strategy: mock `django.core.mail.send_mail` (imported locally inside
send_daily_digest, so patching the real function object is what takes
effect regardless of import timing) and inspect the composed email body —
this exercises the real function end to end rather than testing helpers in
isolation.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from src.models import (
    Announcement, Event, Legislation, LoginHistory, ParliamentUser,
    ServiceHoursSubmission, Vote,
)
from src.models.service import ServicePeriod
from src.tasks.notifications import send_daily_digest


def make_user(uid, **kwargs):
    defaults = dict(name='Test User', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    return ParliamentUser.objects.create(user_id=uid, **defaults)


def digest_body(mock_send_mail):
    mock_send_mail.assert_called_once()
    return mock_send_mail.call_args.kwargs['message']


class ActivitySnapshotCountsTests(TestCase):
    def setUp(self):
        self.user = make_user('digest-activity-1')

    @mock.patch('django.core.mail.send_mail')
    def test_counts_everything_that_happened_in_the_window(self, mock_send_mail):
        LoginHistory.objects.create(user=self.user, status='success', ip_address='203.0.113.1')
        LoginHistory.objects.create(user=self.user, status='failed', ip_address='203.0.113.2')
        LoginHistory.objects.create(user=self.user, status='blocked', ip_address='203.0.113.3')

        leg = Legislation.objects.create(
            title='Digest Test Bill', description='d', posted_by=self.user,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='test.pdf',
        )
        Vote.objects.create(user=self.user, legislation=leg, vote_choice='yes')
        Announcement.objects.create(title='Digest Announcement', content='c', posted_by=self.user, is_active=True)
        Event.objects.create(
            title='Digest Event', description='d', date_time=timezone.now() + timezone.timedelta(days=1),
            created_by=self.user, is_active=True,
        )
        period = ServicePeriod.objects.create(
            name='Digest Period', start_date=timezone.now().date() - timezone.timedelta(days=30),
            end_date=timezone.now().date() + timezone.timedelta(days=30),
            default_hours_required=Decimal('10.00'),
        )
        ServiceHoursSubmission.objects.create(
            period=period, submitted_by=self.user, hours=Decimal('3.00'), status='approved',
            service_date=timezone.now().date(), organization='Org', description='desc',
        )

        send_daily_digest()
        body = digest_body(mock_send_mail)

        self.assertIn('ACTIVITY SNAPSHOT (last 24h)', body)
        self.assertIn('Logins:              1 successful (1 distinct member(s)), 1 failed, 1 blocked', body)
        self.assertIn('Votes cast:          1', body)
        self.assertIn('Legislation submitted: 1', body)
        self.assertIn('Announcements posted: 1', body)
        self.assertIn('Events created:      1', body)
        # sqlite's DecimalField aggregation doesn't preserve trailing zeros the
        # way Postgres does (3 vs 3.00) — assert on the submission count and
        # that a total is present, not the exact decimal formatting, which is
        # a backend quirk rather than something this feature controls.
        self.assertIn('Service hours logged: 1 submission(s),', body)
        self.assertIn('hour(s) total', body)

    @mock.patch('django.core.mail.send_mail')
    def test_a_zero_activity_day_reports_zeros_not_silence(self, mock_send_mail):
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('Logins:              0 successful (0 distinct member(s)), 0 failed, 0 blocked', body)
        self.assertIn('Votes cast:          0', body)

    @mock.patch('django.core.mail.send_mail')
    def test_excludes_logins_outside_the_24h_window(self, mock_send_mail):
        old = LoginHistory.objects.create(user=self.user, status='success', ip_address='203.0.113.1')
        LoginHistory.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timezone.timedelta(hours=25))

        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('Logins:              0 successful (0 distinct member(s)), 0 failed, 0 blocked', body)

    @mock.patch('django.core.mail.send_mail')
    def test_excludes_votes_outside_the_24h_window(self, mock_send_mail):
        leg = Legislation.objects.create(
            title='Old Vote Bill', description='d', posted_by=self.user,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='test.pdf',
        )
        vote = Vote.objects.create(user=self.user, legislation=leg, vote_choice='yes')
        Vote.objects.filter(pk=vote.pk).update(cast_at=timezone.now() - timezone.timedelta(hours=25))

        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('Votes cast:          0', body)


class InfraHealthSectionTests(TestCase):
    @mock.patch('django.core.mail.send_mail')
    def test_shows_not_applicable_on_the_sqlite_test_backend(self, mock_send_mail):
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('INFRASTRUCTURE / OPS HEALTH', body)
        self.assertIn('DB connections:      n/a', body)

    @mock.patch('django.core.mail.send_mail')
    @mock.patch('src.tasks.db_health.get_connection_pressure')
    def test_reflects_postgres_connection_pressure_when_available(self, mock_pressure, mock_send_mail):
        mock_pressure.return_value = {
            'current': 80, 'max_connections': 100, 'ratio': 0.8,
            'by_state': [('idle', 60), ('active', 20)],
        }
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('DB connections:      80/100 (80%)', body)
        self.assertIn('idle=60, active=20', body)

    @mock.patch('django.core.mail.send_mail')
    def test_no_performance_samples_is_reported_plainly(self, mock_send_mail):
        # The cache-isolated test runner clears caches between tests, so the
        # performance middleware's sample buffer is empty here — this is the
        # realistic "nothing sampled yet" case, not a mock.
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('no samples retained to estimate response time', body)

    @mock.patch('django.core.mail.send_mail')
    @mock.patch('src.middleware.performance.get_performance_summary')
    def test_performance_summary_numbers_are_shown_and_labeled_as_estimates(self, mock_perf, mock_send_mail):
        mock_perf.return_value = {
            'total_requests': 1000, 'sampled_requests': 50,
            'avg_response_time_ms': 120.5, 'max_response_time_ms': 2500.0,
            'slow_requests': 3, 'avg_db_queries': 4.2, 'avg_db_time_ms': 30.0,
            'samples_last_hour': 10, 'samples_last_5min': 2, 'stored_samples': 50,
            'sampled': True, 'sample_rate': 20,
        }
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('1,000 request(s)', body)
        self.assertIn('120.5ms', body)
        self.assertIn('4.2 queries', body)
        self.assertIn('estimated from a 1-in-20 sample', body)
        self.assertIn('3 slow request(s)', body)


class LinkEnrichedFlagsTests(TestCase):
    @mock.patch('django.core.mail.send_mail')
    def test_no_password_flag_links_to_manage_users(self, mock_send_mail):
        make_user('digest-nopw', password='')
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('active user(s) have no password set', body)
        self.assertIn('/admin-v2/users/', body)

    @mock.patch('django.core.mail.send_mail')
    def test_quarantine_flag_links_to_quarantine_page(self, mock_send_mail):
        make_user('digest-quarantined', is_quarantined=True)
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertIn('currently quarantined', body)
        self.assertIn('/admin-v2/security/quarantine/', body)

    @mock.patch('django.core.mail.send_mail')
    def test_a_finding_with_no_link_renders_without_a_dangling_dash(self, mock_send_mail):
        # Negative control: most findings still pass link=None. Confirms
        # flag() doesn't append a stray separator when there's nothing to link.
        make_user('digest-plain')
        send_daily_digest()
        body = digest_body(mock_send_mail)
        self.assertNotIn(' — None', body)
