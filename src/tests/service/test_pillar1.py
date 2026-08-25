"""
Tests for Parliament 3.0 — Pillar 1: Async Infrastructure & Live Vote Tallies

Covers:
  - Celery app configuration (smoke tests)
  - Task functions: vote auto-open/close (chapter, committee, slating), scheduled
    announcement dispatch, session cleanup
  - vote_tally_json endpoint (authentication, tally correctness, plurality mode,
    vote-closed detection)
  - QuarantineEnforcementMiddleware (mid-session enforcement, exempt paths)
  - Admin-v2 dashboard context (lockdown_active present)
  - setup_celery_schedules management command (PeriodicTask creation, idempotency)

Run with:
    python manage.py test src.test_pillar1 --settings=ci_settings

Production notes are documented inline (# PROD:) where behaviour differs between
the test environment (SQLite in-memory, memory:// Celery broker, no Redis) and
production (PostgreSQL, Redis broker, gunicorn workers, Cloudflare in front).
"""

import json
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.contrib.auth import SESSION_KEY
from django.core import mail
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ParliamentUser, Legislation, Vote, Committee, CommitteeLegislation,
    CommitteeVote, SlatingPeriod, Announcement, UserSession,
    QuarantinedAccount, SystemLockdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(user_id, member_type='Member', **kwargs):
    """Create a ParliamentUser with sensible defaults."""
    defaults = dict(name=f'User {user_id}', username=f'user_{user_id}', member_type=member_type)
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=user_id, password='testpass123', **defaults)


def make_committee():
    return Committee.objects.create(name='Test Committee', code='TEST', is_active=True)


# ===========================================================================
# 1. CELERY CONFIGURATION SMOKE TESTS
# ===========================================================================

class CeleryConfigTests(TestCase):
    """
    Verify that the Celery app loads correctly and all expected tasks are
    registered. These are import-time checks — if they pass, the worker will
    start cleanly on prod.

    PROD: On the server, `celery -A Parliament inspect registered` should list
    all tasks below. If a task is missing, autodiscover didn't pick up src/tasks.py.
    """

    def test_celery_app_imports(self):
        """Parliament celery app is importable and named correctly."""
        from Parliament.celery import app
        self.assertEqual(app.main, 'Parliament')

    def test_init_exports_celery_app(self):
        """Parliament/__init__.py exports celery_app so @shared_task resolves."""
        import Parliament
        self.assertTrue(hasattr(Parliament, 'celery_app'))

    def test_all_tasks_registered(self):
        """All tasks in src/tasks.py are discoverable by Celery."""
        from Parliament.celery import app
        # Force autodiscover in case it hasn't run yet
        app.autodiscover_tasks(['src'])
        registered = app.tasks.keys()
        expected = [
            'tasks.send_announcement_email',
            'tasks.send_security_alert_task',
            'tasks.send_pledge_welcome_task',
            'tasks.auto_open_close_chapter_votes',
            'tasks.auto_open_close_committee_votes',
            'tasks.auto_open_close_slating_votes',
            'tasks.publish_scheduled_announcements',
            'tasks.cleanup_expired_sessions',
            'tasks.send_daily_digest',
        ]
        for task_name in expected:
            self.assertIn(task_name, registered, f'Task not registered: {task_name}')

    def test_celery_settings_applied(self):
        """Key Celery settings are present in Django settings."""
        from django.conf import settings
        self.assertTrue(hasattr(settings, 'CELERY_BROKER_URL'))
        self.assertTrue(hasattr(settings, 'CELERY_TASK_SERIALIZER'))
        self.assertEqual(getattr(settings, 'CELERY_TASK_SERIALIZER', None), 'json')


# ===========================================================================
# 2. VOTE AUTO-OPEN / CLOSE TASKS
# ===========================================================================

class AutoCloseChapterVotesTaskTests(TestCase):
    """
    Tests for tasks.auto_open_close_chapter_votes.

    PROD: This task runs every minute via Beat. The on-page-load auto-close in
    vote_view.py still exists as a belt-and-suspenders fallback — it's harmless
    because `voting_closed=True` prevents double-processing.
    """

    def setUp(self):
        self.author = make_user('cv_author', 'Chair')

    def _make_leg(self, **kwargs):
        defaults = dict(
            title='Test Bill',
            description='desc',
            posted_by=self.author,
            available_at=timezone.now() - timedelta(hours=1),
            document='test.pdf',
            vote_mode='percentage',
            required_percentage='51',
        )
        defaults.update(kwargs)
        return Legislation.objects.create(**defaults)

    def test_closes_expired_percentage_vote_that_passes(self):
        """Percentage vote with majority yes votes closes and passes."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(voting_ends_at=timezone.now() - timedelta(minutes=1))
        for _ in range(6):
            Vote.objects.create(legislation=leg, user=make_user(f'v{_}'), vote_choice='yes')
        for _ in range(4):
            Vote.objects.create(legislation=leg, user=make_user(f'n{_}'), vote_choice='no')

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)
        self.assertTrue(leg.passed)
        self.assertEqual(leg.status, 'passed')

    def test_closes_expired_percentage_vote_that_fails(self):
        """Percentage vote without majority closes and fails."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(voting_ends_at=timezone.now() - timedelta(minutes=1))
        for _ in range(4):
            Vote.objects.create(legislation=leg, user=make_user(f'yv{_}'), vote_choice='yes')
        for _ in range(6):
            Vote.objects.create(legislation=leg, user=make_user(f'nv{_}'), vote_choice='no')

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)
        self.assertFalse(leg.passed)
        self.assertEqual(leg.status, 'failed')

    def test_does_not_close_vote_before_deadline(self):
        """Legislation with a future voting_ends_at is not touched."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(voting_ends_at=timezone.now() + timedelta(hours=1))

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertFalse(leg.voting_closed)

    def test_does_not_close_vote_without_deadline(self):
        """Legislation with no voting_ends_at is never auto-closed."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(voting_ends_at=None)

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertFalse(leg.voting_closed)

    def test_already_closed_vote_not_double_processed(self):
        """A vote that is already closed is not re-processed."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(
            voting_ends_at=timezone.now() - timedelta(minutes=5),
            voting_closed=True,
            passed=True,
            status='passed',
        )
        # Change DB vote counts so re-processing would flip the result
        Vote.objects.create(legislation=leg, user=make_user('lateV'), vote_choice='no')

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        # Should still be passed — re-processing was skipped
        self.assertTrue(leg.passed)

    def test_closes_no_votes_cast(self):
        """Legislation that expires with zero votes closes without a pass result."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(voting_ends_at=timezone.now() - timedelta(minutes=1))

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)
        # passed field stays at its default (False) when there are no votes
        self.assertFalse(leg.passed)

    def test_piecewise_mode_correct_pass_logic(self):
        """Piecewise vote passes only when yes count meets required_number."""
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(
            vote_mode='piecewise',
            required_number=5,
            voting_ends_at=timezone.now() - timedelta(minutes=1),
        )
        for _ in range(5):
            Vote.objects.create(legislation=leg, user=make_user(f'pw{_}'), vote_choice='yes')

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertTrue(leg.passed)

    def test_piecewise_mode_fails_below_required(self):
        from src.tasks import auto_open_close_chapter_votes
        leg = self._make_leg(
            vote_mode='piecewise',
            required_number=5,
            voting_ends_at=timezone.now() - timedelta(minutes=1),
        )
        for _ in range(4):
            Vote.objects.create(legislation=leg, user=make_user(f'ppw{_}'), vote_choice='yes')

        auto_open_close_chapter_votes()

        leg.refresh_from_db()
        self.assertFalse(leg.passed)


class AutoCloseCommitteeVotesTaskTests(TestCase):
    """
    Tests for tasks.auto_open_close_committee_votes.

    PROD: Committee votes open manually; this task only handles auto-close.
    The fallback in committee/vote.py still runs on page load.
    """

    def setUp(self):
        self.author = make_user('cc_author', 'Chair')
        self.committee = make_committee()

    def _make_leg(self, **kwargs):
        defaults = dict(
            committee=self.committee,
            title='Committee Bill',
            description='desc',
            posted_by=self.author,
            available_at=timezone.now() - timedelta(hours=1),
            vote_mode='percentage',
            required_percentage='51',
        )
        defaults.update(kwargs)
        return CommitteeLegislation.objects.create(**defaults)

    def test_closes_expired_committee_vote(self):
        from src.tasks import auto_open_close_committee_votes
        leg = self._make_leg(voting_ends_at=timezone.now() - timedelta(minutes=2))
        voter = make_user('cv_voter', 'Member')
        CommitteeVote.objects.create(legislation=leg, user=voter, vote_choice='yes')

        auto_open_close_committee_votes()

        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)

    def test_does_not_close_future_deadline(self):
        from src.tasks import auto_open_close_committee_votes
        leg = self._make_leg(voting_ends_at=timezone.now() + timedelta(hours=1))

        auto_open_close_committee_votes()

        leg.refresh_from_db()
        self.assertFalse(leg.voting_closed)


class AutoOpenCloseSlatingVotesTaskTests(TestCase):
    """
    Tests for tasks.auto_open_close_slating_votes.

    PROD: This replaces having officers manually click "Open Voting" at exactly
    the right time during a chapter meeting.
    """

    def _make_period(self, status, **kwargs):
        return SlatingPeriod.objects.create(
            name='Test Election',
            academic_term='Fall 2026',
            status=status,
            **kwargs,
        )

    def test_opens_voting_when_time_arrives(self):
        from src.tasks import auto_open_close_slating_votes
        period = self._make_period(
            'deliberation',
            voting_open_at=timezone.now() - timedelta(minutes=1),
        )

        auto_open_close_slating_votes()

        period.refresh_from_db()
        self.assertEqual(period.status, 'voting_open')

    def test_does_not_open_early(self):
        from src.tasks import auto_open_close_slating_votes
        period = self._make_period(
            'deliberation',
            voting_open_at=timezone.now() + timedelta(hours=1),
        )

        auto_open_close_slating_votes()

        period.refresh_from_db()
        self.assertEqual(period.status, 'deliberation')

    def test_closes_voting_when_time_arrives(self):
        from src.tasks import auto_open_close_slating_votes
        period = self._make_period(
            'voting_open',
            voting_close_at=timezone.now() - timedelta(minutes=1),
        )

        auto_open_close_slating_votes()

        period.refresh_from_db()
        self.assertEqual(period.status, 'voting_closed')

    def test_does_not_close_early(self):
        from src.tasks import auto_open_close_slating_votes
        period = self._make_period(
            'voting_open',
            voting_close_at=timezone.now() + timedelta(hours=1),
        )

        auto_open_close_slating_votes()

        period.refresh_from_db()
        self.assertEqual(period.status, 'voting_open')

    def test_ignores_periods_without_timestamps(self):
        """A period with no voting_open_at / voting_close_at is never auto-transitioned."""
        from src.tasks import auto_open_close_slating_votes
        period = self._make_period('deliberation')  # no timestamps

        auto_open_close_slating_votes()

        period.refresh_from_db()
        self.assertEqual(period.status, 'deliberation')


# ===========================================================================
# 3. SCHEDULED ANNOUNCEMENT DISPATCH TASK
# ===========================================================================

class PublishScheduledAnnouncementsTaskTests(TestCase):
    """
    Tests for tasks.publish_scheduled_announcements.

    PROD: Runs every 5 minutes via Beat. Uses select_for_update(skip_locked=True)
    so if Beat fires the task twice in rapid succession only one instance processes
    each announcement. On prod with PostgreSQL this is a true row-level lock;
    in the test suite it falls back to Django's emulated lock behavior.
    """

    def setUp(self):
        self.author = make_user('ann_author', 'Officer')

    def _make_announcement(self, **kwargs):
        defaults = dict(
            title='Test Announcement',
            content='Hello chapter.',
            posted_by=self.author,
        )
        defaults.update(kwargs)
        return Announcement.objects.create(**defaults)

    @patch('src.tasks.send_announcement_email')
    def test_queues_email_for_ready_announcement(self, mock_task):
        """An announcement past publish_at with send_email_on_publish=True gets queued."""
        ann = self._make_announcement(
            publish_at=timezone.now() - timedelta(minutes=1),
            send_email_on_publish=True,
            email_sent_at=None,
            is_active=True,
        )

        from src.tasks import publish_scheduled_announcements
        publish_scheduled_announcements()

        mock_task.delay.assert_called_once_with(ann.pk)
        ann.refresh_from_db()
        # Row is claimed atomically: email_sent_at set, flag cleared
        self.assertIsNotNone(ann.email_sent_at)
        self.assertFalse(ann.send_email_on_publish)

    @patch('src.tasks.send_announcement_email')
    def test_skips_future_announcements(self, mock_task):
        """Announcements scheduled in the future are not dispatched."""
        self._make_announcement(
            publish_at=timezone.now() + timedelta(hours=1),
            send_email_on_publish=True,
            email_sent_at=None,
            is_active=True,
        )

        from src.tasks import publish_scheduled_announcements
        publish_scheduled_announcements()

        mock_task.delay.assert_not_called()

    @patch('src.tasks.send_announcement_email')
    def test_skips_already_sent_announcements(self, mock_task):
        """An announcement whose email_sent_at is already set is not re-sent."""
        self._make_announcement(
            publish_at=timezone.now() - timedelta(minutes=5),
            send_email_on_publish=False,
            email_sent_at=timezone.now() - timedelta(minutes=4),
            is_active=True,
        )

        from src.tasks import publish_scheduled_announcements
        publish_scheduled_announcements()

        mock_task.delay.assert_not_called()

    @patch('src.tasks.send_announcement_email')
    def test_skips_inactive_announcements(self, mock_task):
        """Inactive announcements are never dispatched even if past publish_at."""
        self._make_announcement(
            publish_at=timezone.now() - timedelta(minutes=1),
            send_email_on_publish=True,
            email_sent_at=None,
            is_active=False,
        )

        from src.tasks import publish_scheduled_announcements
        publish_scheduled_announcements()

        mock_task.delay.assert_not_called()

    @patch('src.tasks.send_announcement_email')
    def test_multiple_ready_announcements_all_queued(self, mock_task):
        """Multiple ready announcements all get queued in a single task run."""
        for i in range(3):
            self._make_announcement(
                title=f'Ann {i}',
                publish_at=timezone.now() - timedelta(minutes=i + 1),
                send_email_on_publish=True,
                email_sent_at=None,
                is_active=True,
            )

        from src.tasks import publish_scheduled_announcements
        publish_scheduled_announcements()

        self.assertEqual(mock_task.delay.call_count, 3)


# ===========================================================================
# 4. SESSION CLEANUP TASK
# ===========================================================================

class CleanupExpiredSessionsTaskTests(TestCase):
    """
    Tests for tasks.cleanup_expired_sessions.

    PROD: Runs at 3 AM daily. The threshold is 30 days of inactivity.
    On a server that's been running a while, the first run will likely
    delete a large number of records; subsequent runs will be fast.
    """

    def setUp(self):
        self.user = make_user('sess_user')

    def test_removes_old_sessions(self):
        """Sessions older than 30 days are deleted."""
        from src.tasks import cleanup_expired_sessions

        old_session = UserSession.objects.create(
            user=self.user,
            session_key='old_key_001',
        )
        # Manually push last_activity back past the 30-day threshold
        UserSession.objects.filter(pk=old_session.pk).update(
            last_activity=timezone.now() - timedelta(days=31)
        )

        cleanup_expired_sessions()

        self.assertFalse(UserSession.objects.filter(pk=old_session.pk).exists())

    def test_keeps_recent_sessions(self):
        """Sessions active within the last 30 days are not deleted."""
        from src.tasks import cleanup_expired_sessions

        recent = UserSession.objects.create(
            user=self.user,
            session_key='recent_key_001',
        )

        cleanup_expired_sessions()

        self.assertTrue(UserSession.objects.filter(pk=recent.pk).exists())

    def test_selective_deletion(self):
        """Only expired sessions are deleted; recent ones survive."""
        from src.tasks import cleanup_expired_sessions

        old = UserSession.objects.create(user=self.user, session_key='old_key_002')
        UserSession.objects.filter(pk=old.pk).update(
            last_activity=timezone.now() - timedelta(days=45)
        )
        recent = UserSession.objects.create(user=self.user, session_key='recent_002')

        cleanup_expired_sessions()

        self.assertFalse(UserSession.objects.filter(pk=old.pk).exists())
        self.assertTrue(UserSession.objects.filter(pk=recent.pk).exists())


# ===========================================================================
# 5. VOTE TALLY JSON ENDPOINT
# ===========================================================================

class VoteTallyJsonTests(TestCase):
    """
    Tests for GET /vote/tally/ (vote_tally_json view).

    PROD: This endpoint is hit by every tab open on the vote page every 15s.
    With Cloudflare in front, ensure the response has Cache-Control: no-store
    or Cloudflare will serve the same tally to every user regardless of who
    they are. The view is authenticated so Cloudflare should not cache it
    (cookies are present), but worth verifying after first deploy.
    """

    def setUp(self):
        self.client = Client()
        self.author = make_user('tally_author', 'Chair')
        self.other = make_user('tally_other', 'Member')
        self.tally_url = reverse('vote_tally')

    def _make_open_leg(self, author=None, **kwargs):
        defaults = dict(
            title='Open Bill',
            description='desc',
            posted_by=author or self.author,
            available_at=timezone.now() - timedelta(hours=1),
            document='test.pdf',
            vote_mode='percentage',
            required_percentage='51',
            voting_closed=False,
        )
        defaults.update(kwargs)
        return Legislation.objects.create(**defaults)

    def test_requires_authentication(self):
        """Unauthenticated request is redirected to login."""
        response = self.client.get(self.tally_url)
        self.assertIn(response.status_code, [302, 403])

    def test_returns_json(self):
        """Authenticated request returns valid JSON with a tallies key."""
        self.client.force_login(self.author)
        response = self.client.get(self.tally_url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn('tallies', data)

    def test_author_sees_own_tally(self):
        """Author gets vote counts for their own legislation."""
        leg = self._make_open_leg()
        for i in range(3):
            Vote.objects.create(legislation=leg, user=make_user(f'tv{i}'), vote_choice='yes')
        Vote.objects.create(legislation=leg, user=make_user('tvn'), vote_choice='no')

        self.client.force_login(self.author)
        response = self.client.get(self.tally_url)
        data = json.loads(response.content)

        self.assertIn(str(leg.id), data['tallies'])
        tally = data['tallies'][str(leg.id)]
        self.assertEqual(tally['yes'], 3)
        self.assertEqual(tally['no'], 1)
        self.assertEqual(tally['total'], 4)
        self.assertFalse(tally['closed'])

    def test_non_author_does_not_see_full_tally(self):
        """A non-author on the vote page does not receive vote counts."""
        leg = self._make_open_leg()
        Vote.objects.create(legislation=leg, user=make_user('tv_nonauth'), vote_choice='yes')

        self.client.force_login(self.other)
        response = self.client.get(self.tally_url)
        data = json.loads(response.content)

        # The legislation appears (for the closed-flag check) but has no vote counts
        if str(leg.id) in data['tallies']:
            tally = data['tallies'][str(leg.id)]
            self.assertNotIn('yes', tally)
            self.assertNotIn('no', tally)

    def test_plurality_tally_includes_all_options(self):
        """Plurality mode tally has a key for every option."""
        leg = self._make_open_leg(
            vote_mode='plurality',
            plurality_options=['Alice', 'Bob', 'Charlie'],
        )
        Vote.objects.create(legislation=leg, user=make_user('pv1'), vote_choice='Alice')
        Vote.objects.create(legislation=leg, user=make_user('pv2'), vote_choice='Alice')
        Vote.objects.create(legislation=leg, user=make_user('pv3'), vote_choice='Bob')

        self.client.force_login(self.author)
        response = self.client.get(self.tally_url)
        data = json.loads(response.content)
        tally = data['tallies'][str(leg.id)]

        self.assertEqual(tally['Alice'], 2)
        self.assertEqual(tally['Bob'], 1)
        self.assertEqual(tally['Charlie'], 0)
        self.assertEqual(tally['total'], 3)

    def test_recently_closed_vote_returns_closed_flag(self):
        """A vote that just closed appears with closed=True so the page reloads."""
        leg = self._make_open_leg(
            voting_closed=True,
            passed=True,
            status='passed',
            voting_ended_at=timezone.now() - timedelta(seconds=30),
        )

        self.client.force_login(self.author)
        response = self.client.get(self.tally_url)
        data = json.loads(response.content)

        self.assertIn(str(leg.id), data['tallies'])
        self.assertTrue(data['tallies'][str(leg.id)]['closed'])

    def test_old_closed_vote_not_returned(self):
        """A vote closed more than 2 minutes ago is not included in the response."""
        leg = self._make_open_leg(
            voting_closed=True,
            passed=True,
            status='passed',
            voting_ended_at=timezone.now() - timedelta(minutes=10),
        )

        self.client.force_login(self.author)
        response = self.client.get(self.tally_url)
        data = json.loads(response.content)

        # Should not appear at all since it's old and closed
        self.assertNotIn(str(leg.id), data['tallies'])

    def test_empty_response_no_legislation(self):
        """User with no open legislation gets an empty tallies dict."""
        self.client.force_login(self.author)
        response = self.client.get(self.tally_url)
        data = json.loads(response.content)
        self.assertEqual(data['tallies'], {})


# ===========================================================================
# 6. QUARANTINE ENFORCEMENT MIDDLEWARE
# ===========================================================================

class QuarantineEnforcementMiddlewareTests(TestCase):
    """
    Tests for QuarantineEnforcementMiddleware.

    This middleware closes the gap where a user quarantined mid-session (e.g.
    by InputSanitizationMiddleware after 20 attack attempts) could continue
    browsing until their session naturally expired.

    PROD: On prod the quarantine check hits the DB on every request for
    authenticated users. The field is a BooleanField with an index created by
    the existing migration so the lookup is cheap (~1ms at current scale).
    """

    def setUp(self):
        self.client = Client()
        self.user = make_user('quar_user', 'Member')

    def test_quarantined_user_is_logged_out(self):
        """A quarantined user is logged out on their next request."""
        self.user.is_quarantined = True
        self.user.save(update_fields=['is_quarantined'])

        self.client.force_login(self.user)
        # Any authenticated page triggers the middleware
        response = self.client.get(reverse('vote'))

        # Should be redirected (logged out)
        self.assertEqual(response.status_code, 302)
        # Session should no longer carry the user
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_quarantined_redirects_to_login_with_flag(self):
        """Redirect destination is /login/?quarantined=1."""
        self.user.is_quarantined = True
        self.user.save(update_fields=['is_quarantined'])

        self.client.force_login(self.user)
        response = self.client.get(reverse('vote'))

        self.assertIn('quarantined=1', response['Location'])

    def test_non_quarantined_passes_through(self):
        """Non-quarantined authenticated user is not interrupted."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('vote'))

        # Should reach the vote page (200), not be redirected by quarantine logic
        self.assertNotIn('quarantined=1', response.get('Location', ''))
        self.assertIn(response.status_code, [200, 302])  # 302 only for other reasons

    def test_unauthenticated_request_passes_through(self):
        """Middleware does nothing for unauthenticated requests."""
        response = self.client.get(reverse('vote'))
        # Unauthenticated → login redirect, not quarantine redirect
        if response.status_code == 302:
            self.assertNotIn('quarantined=1', response['Location'])

    def test_logout_path_exempt(self):
        """Quarantined users can still reach /logout/ to clear their session."""
        self.user.is_quarantined = True
        self.user.save(update_fields=['is_quarantined'])

        self.client.force_login(self.user)
        # /logout/ should not loop into a quarantine redirect
        response = self.client.get('/logout/')
        self.assertNotEqual(response.status_code, 500)

    def test_login_path_exempt(self):
        """Quarantined users can reach /login/ (they need it to see the message)."""
        self.user.is_quarantined = True
        self.user.save(update_fields=['is_quarantined'])

        self.client.force_login(self.user)
        response = self.client.get('/login/')
        # Should not cause infinite redirect loop
        self.assertNotEqual(response.status_code, 500)

    def test_quarantine_message_shown_on_login_page(self):
        """GET /login/?quarantined=1 shows an error message to the user."""
        response = self.client.get('/login/?quarantined=1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'flagged for suspicious activity', msg_prefix='Expected quarantine message on login page')


# ===========================================================================
# 7. ADMIN-V2 DASHBOARD — LOCKDOWN_ACTIVE CONTEXT
# ===========================================================================

class AdminV2DashboardContextTests(TestCase):
    """
    Tests that admin_v2_dashboard view passes lockdown_active to the template.

    Previously this variable was never passed (the banner was always hidden).

    PROD: admin-v2 is behind its own auth (ADMIN_SECRET_KEY check), so we
    force_login an admin user and bypass the custom auth for testing. The
    lockdown_active flag is used to show the red EMERGENCY LOCKDOWN ACTIVE
    banner at the top of the dashboard.
    """

    def setUp(self):
        self.client = Client()
        # ⚠️ v3.21.5 — PATCH THE ALLOWLIST, DO NOT INHERIT IT. This line used to
        # say the id "must match ALLOWED_USER_ID ('73') in admin_v2.py", which
        # stopped being true in v3.17.0: the allowlist is parsed from the
        # `ADMIN_V2_USER_IDS` environment variable, so these two tests passed
        # only on a machine whose `.env` listed 73 and were red in CI. See
        # `src/test_environment_independence.py`.
        from unittest.mock import patch as _patch
        allowlist = _patch('src.view.admin_v2.ALLOWED_USER_IDS', {'73'})
        allowlist.start()
        self.addCleanup(allowlist.stop)

        self.admin = make_user('73', 'Officer')
        self.admin.is_admin = True
        self.admin.save(update_fields=['is_admin'])
        # Seed the SystemLockdown singleton row
        SystemLockdown.objects.get_or_create(pk=1, defaults={'is_active': False})

    def _get_dashboard(self):
        self.client.force_login(self.admin)
        # Satisfy both session checks in require_admin_v2_auth decorator
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
        return self.client.get(reverse('admin_v2_dashboard'))

    def test_lockdown_active_in_context_when_inactive(self):
        """lockdown_active is False and present in context when lockdown is off."""
        SystemLockdown.objects.filter(pk=1).update(is_active=False)
        response = self._get_dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertIn('lockdown_active', response.context)
        self.assertFalse(response.context['lockdown_active'])

    def test_lockdown_active_in_context_when_active(self):
        """lockdown_active is True in context when lockdown is enabled."""
        lockdown = SystemLockdown.objects.get(pk=1)
        lockdown.is_active = True
        lockdown.save()

        response = self._get_dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertIn('lockdown_active', response.context)
        self.assertTrue(response.context['lockdown_active'])

        # Cleanup
        lockdown.is_active = False
        lockdown.save()


# ===========================================================================
# 8. SETUP_CELERY_SCHEDULES MANAGEMENT COMMAND
# ===========================================================================

class SetupCelerySchedulesCommandTests(TestCase):
    """
    Tests for python manage.py setup_celery_schedules.

    PROD: Run once immediately after deploying Celery for the first time.
    Safe to re-run (idempotent). Pass --reset after renaming task names to
    force-recreate the PeriodicTask rows with the new task name.
    """

    def _run_command(self, *args):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('setup_celery_schedules', *args, stdout=out)
        return out.getvalue()

    def test_creates_all_expected_schedules(self):
        """All default schedule names are created in the DB."""
        from django_celery_beat.models import PeriodicTask
        self._run_command()

        expected_names = [
            'Auto open/close chapter votes',
            'Auto open/close committee votes',
            'Auto open/close slating votes',
            'Publish scheduled announcements',
            'Cleanup expired user sessions',
            'Send daily site digest',
        ]
        created_names = list(PeriodicTask.objects.values_list('name', flat=True))
        for name in expected_names:
            self.assertIn(name, created_names, f'PeriodicTask not created: {name}')

    def test_idempotent_on_second_run(self):
        """Running the command twice does not create duplicates."""
        from django_celery_beat.models import PeriodicTask
        self._run_command()
        count_after_first = PeriodicTask.objects.count()
        self._run_command()
        count_after_second = PeriodicTask.objects.count()
        self.assertEqual(count_after_first, count_after_second)

    def test_vote_tasks_use_1_minute_interval(self):
        """Vote auto-open/close tasks run every 1 minute."""
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        self._run_command()

        for name in ['Auto open/close chapter votes', 'Auto open/close committee votes', 'Auto open/close slating votes']:
            task = PeriodicTask.objects.get(name=name)
            self.assertIsNotNone(task.interval)
            self.assertEqual(task.interval.every, 1)
            self.assertEqual(task.interval.period, IntervalSchedule.MINUTES)

    def test_announcement_task_uses_5_minute_interval(self):
        """Scheduled announcement task runs every 5 minutes."""
        from django_celery_beat.models import PeriodicTask, IntervalSchedule
        self._run_command()
        task = PeriodicTask.objects.get(name='Publish scheduled announcements')
        self.assertIsNotNone(task.interval)
        self.assertEqual(task.interval.every, 5)
        self.assertEqual(task.interval.period, IntervalSchedule.MINUTES)

    def test_housekeeping_tasks_use_crontab(self):
        """Housekeeping tasks use crontab schedules, not interval."""
        from django_celery_beat.models import PeriodicTask
        self._run_command()
        for name in ['Cleanup expired user sessions', 'Send daily site digest']:
            task = PeriodicTask.objects.get(name=name)
            self.assertIsNotNone(task.crontab)
            self.assertIsNone(task.interval)

    def test_all_tasks_enabled_by_default(self):
        """All created schedules start in an enabled state."""
        from django_celery_beat.models import PeriodicTask
        self._run_command()
        disabled = PeriodicTask.objects.filter(enabled=False).exclude(name='celery.backend_cleanup')
        self.assertEqual(disabled.count(), 0, f'Unexpected disabled tasks: {list(disabled.values_list("name", flat=True))}')

    def test_reset_flag_recreates_tasks(self):
        """--reset deletes and recreates existing schedules."""
        from django_celery_beat.models import PeriodicTask
        self._run_command()
        original_ids = set(PeriodicTask.objects.values_list('id', flat=True))

        self._run_command('--reset')
        new_ids = set(PeriodicTask.objects.values_list('id', flat=True))

        # IDs should all be new (rows were deleted and re-created)
        self.assertFalse(original_ids & new_ids, 'Expected all PeriodicTask rows to be recreated after --reset')


# ===========================================================================
# 9. ASYNC EMAIL TASKS (send functions called, not delivery)
# ===========================================================================

class AsyncEmailTaskTests(TestCase):
    """
    Tests that async email wrapper tasks call through to the underlying
    notification functions correctly.

    PROD: With CELERY_TASK_ALWAYS_EAGER=True (test mode), .delay() runs
    synchronously in the same process. On prod, tasks run in the worker
    process — failures appear in celery-worker.log, not in gunicorn logs.
    Make sure celery-worker.service has StandardOutput=journal.
    """

    def setUp(self):
        self.author = make_user('email_author', 'Officer')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    @patch('src.notifications.send_announcement_notification')
    def test_send_announcement_email_task_calls_notification(self, mock_notify):
        """send_announcement_email task calls send_announcement_notification."""
        from src.tasks import send_announcement_email
        ann = Announcement.objects.create(
            title='Test Ann',
            content='Content',
            posted_by=self.author,
            is_active=True,
        )
        send_announcement_email(ann.pk, self.author.pk)
        mock_notify.assert_called_once()

    @patch('src.notifications.send_announcement_notification')
    def test_send_announcement_email_handles_missing_announcement(self, mock_notify):
        """Task handles a deleted announcement gracefully (no exception)."""
        from src.tasks import send_announcement_email
        send_announcement_email(999999)  # non-existent pk
        mock_notify.assert_not_called()

    @patch('src.notifications.send_pledge_welcome_email')
    def test_send_pledge_welcome_task_calls_notification(self, mock_welcome):
        """send_pledge_welcome_task calls send_pledge_welcome_email."""
        from src.tasks import send_pledge_welcome_task
        pledge = make_user('pledge_task', 'Pledge')
        send_pledge_welcome_task(pledge.pk, 'TempPass123!')
        mock_welcome.assert_called_once_with(pledge, 'TempPass123!')
