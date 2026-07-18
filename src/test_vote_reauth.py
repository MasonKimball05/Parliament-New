"""
Smoke tests for v3.13.3 — passkey vote re-auth + vote-page reliability fixes.
Covers: password path, passkey grant (fresh / one-shot / expired), explicit
errors for no-selection and stale attendance, vote password rate limiting,
and the legacy Attendance.present update_fields sync.
"""
from datetime import timedelta
from unittest import skipUnless

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from src.models import ParliamentUser, Legislation, Vote, Attendance
from src.view.webauthn import _SESSION_REAUTH_GRANT_AT


class VoteReauthSmokeTests(TestCase):
    def setUp(self):
        cache.clear()  # rate-limit counters persist across tests otherwise
        self.client = Client()
        self.voter = ParliamentUser.objects.create_user(
            user_id='v1', name='Voter One', username='v1', member_type='Member')
        self.voter.set_password('testpass')
        self.voter.save()
        self.client.force_login(self.voter)
        Attendance.objects.create(user=self.voter, status='present')
        self.leg = Legislation.objects.create(
            title='Smoke Test Leg', description='D', posted_by=self.voter,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='test.pdf')
        self.url = reverse('vote')

    def _post_vote(self, leg=None, **extra):
        data = {'action': 'cast_vote',
                'legislation_id': (leg or self.leg).id,
                'vote_choice': 'yes'}
        data.update(extra)
        return self.client.post(self.url, data, follow=True)

    def _grant(self, age_seconds=0):
        session = self.client.session
        session[_SESSION_REAUTH_GRANT_AT] = (
            timezone.now() - timedelta(seconds=age_seconds)).isoformat()
        session.save()

    def test_password_path_still_works(self):
        self._post_vote(password='testpass')
        self.assertTrue(Vote.objects.filter(
            user=self.voter, legislation=self.leg, vote_choice='yes').exists())

    def test_wrong_password_no_vote(self):
        resp = self._post_vote(password='wrong')
        self.assertFalse(Vote.objects.exists())
        self.assertContains(resp, 'Incorrect password')

    def test_fresh_passkey_grant_allows_vote(self):
        self._grant()
        self._post_vote(auth_method='passkey')
        self.assertTrue(Vote.objects.filter(
            user=self.voter, legislation=self.leg).exists())

    def test_passkey_grant_is_one_shot(self):
        self._grant()
        self._post_vote(auth_method='passkey')
        leg2 = Legislation.objects.create(
            title='Second Leg', description='D', posted_by=self.voter,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='test.pdf')
        resp = self._post_vote(leg=leg2, auth_method='passkey')
        self.assertFalse(Vote.objects.filter(legislation=leg2).exists())
        self.assertContains(resp, 'Passkey confirmation expired or missing')

    def test_expired_passkey_grant_rejected(self):
        self._grant(age_seconds=600)
        resp = self._post_vote(auth_method='passkey')
        self.assertFalse(Vote.objects.exists())
        self.assertContains(resp, 'Passkey confirmation expired or missing')

    def test_no_selection_gets_error_not_silence(self):
        resp = self.client.post(self.url, {
            'action': 'cast_vote',
            'legislation_id': self.leg.id,
            'password': 'testpass',
        }, follow=True)
        self.assertFalse(Vote.objects.exists())
        self.assertContains(resp, 'select a vote option')

    def test_stale_attendance_gets_error_not_silence(self):
        Attendance.objects.all().delete()
        resp = self._post_vote(password='testpass')
        self.assertFalse(Vote.objects.exists())
        self.assertContains(resp, 'NOT counted')

    def test_vote_password_rate_limit(self):
        for _ in range(10):
            self._post_vote(password='wrong')
        resp = self._post_vote(password='testpass')  # correct, but limited
        self.assertFalse(Vote.objects.exists())
        self.assertContains(resp, 'Too many incorrect password attempts')


class AttendancePresentSyncTests(TestCase):
    def test_update_or_create_syncs_legacy_present(self):
        """update_or_create saves with update_fields — present must still sync."""
        user = ParliamentUser.objects.create_user(
            user_id='a1', name='Att User', username='a1', member_type='Member')
        now = timezone.now()
        att, _ = Attendance.objects.update_or_create(
            user=user, date=now.date(), attendance_type='committee',
            committee=None, defaults={'status': 'absent', 'created_at': now})
        self.assertFalse(att.present)
        att2, created = Attendance.objects.update_or_create(
            user=user, date=now.date(), attendance_type='committee',
            committee=None, defaults={'status': 'present', 'created_at': now})
        self.assertFalse(created)
        att2.refresh_from_db()
        self.assertTrue(att2.present, 'legacy present bool did not sync on update_or_create')


class QuickAttendanceTests(TestCase):
    """v3.13.3 — quick-attendance panel endpoint returns JSON and heals dupes."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.officer = ParliamentUser.objects.create_user(
            user_id='off1', name='Officer One', username='off1', member_type='Officer')
        self.officer.set_password('testpass')
        self.officer.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='m1', name='Member One', username='m1', member_type='Member')
        self.client.force_login(self.officer)
        self.url = reverse('vote')

    def _mark(self, status, target=None):
        return self.client.post(self.url, {
            'action': 'mark_attendance_quick',
            'target_user_id': (target or self.member).user_id,
            'attendance_status': status,
        })

    def test_officer_mark_returns_json_and_saves(self):
        resp = self._mark('present')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        att = Attendance.objects.get(user=self.member)
        self.assertEqual(att.status, 'present')
        self.assertTrue(att.present)  # legacy bool synced (update_fields fix)
        # Update path (update_or_create → save(update_fields=...))
        resp = self._mark('late')
        self.assertTrue(resp.json()['ok'])
        att.refresh_from_db()
        self.assertEqual(att.status, 'late')

    def test_non_officer_gets_403_json(self):
        self.client.force_login(self.member)
        resp = self._mark('present', target=self.member)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()['ok'])

    def test_invalid_status_gets_400(self):
        resp = self._mark('sortof-here')
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_rows_are_healed(self):
        now = timezone.now()
        for status in ('absent', 'present'):
            Attendance.objects.create(
                user=self.member, date=now.date(), attendance_type='committee',
                committee=None, status=status, created_at=now)
        resp = self._mark('late')
        self.assertEqual(resp.status_code, 200)
        rows = Attendance.objects.filter(
            user=self.member, date=now.date(), attendance_type='committee', committee=None)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().status, 'late')


class LegislationStatusIntegrationTests(TestCase):
    """
    v3.13.3 — fallout from the duplicate Legislation.status field definition
    (every legislation was born status='draft', which wasn't a valid choice;
    status='active' integration queries never matched; end_vote marked failed
    votes 'removed' which the history page hides entirely; reopen never put
    legislation back on the vote page).
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.officer = ParliamentUser.objects.create_user(
            user_id='off2', name='Off Two', username='off2', member_type='Officer')
        self.officer.set_password('testpass')
        self.officer.save()
        self.client.force_login(self.officer)
        Attendance.objects.create(user=self.officer, status='present')
        self.leg = Legislation.objects.create(
            title='Status Integration Leg', description='D', posted_by=self.officer,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='test.pdf')

    def test_new_legislation_is_draft_and_visible_on_vote_page(self):
        self.assertEqual(self.leg.status, 'draft')
        resp = self.client.get(reverse('vote'))
        self.assertContains(resp, 'Status Integration Leg')

    @skipUnless(connection.vendor == 'postgresql',
                'home view uses ArrayField __contains (Event.visible_to) — postgres only')
    def test_open_legislation_shows_in_home_pending_votes(self):
        resp = self.client.get(reverse('home'))
        self.assertContains(resp, 'Status Integration Leg')

    def test_open_legislation_queryset_matches_home_semantics(self):
        """sqlite-safe twin of the home-page test: the open-legislation filter
        (voting open, available, not closed-status) must match the new leg."""
        now = timezone.now()
        from django.db.models import Q
        qs = Legislation.objects.filter(
            voting_closed=False, available_at__lte=now,
        ).filter(
            Q(voting_starts_at__lte=now) | Q(voting_starts_at__isnull=True)
        ).exclude(status__in=['pending', 'tabled', 'passed', 'failed', 'removed'])
        self.assertIn(self.leg, qs)

    def test_failed_vote_marked_failed_and_listed_in_history(self):
        resp = self.client.post(reverse('end_vote', args=[self.leg.id]))
        self.assertEqual(resp.status_code, 200)
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.status, 'failed')  # was 'removed' pre-v3.13.3
        self.assertFalse(self.leg.passed)
        hist = self.client.get(reverse('view_legislation_history') + '?status=failed')
        self.assertContains(hist, 'Status Integration Leg')

    def test_passed_vote_sets_status_and_passed_bool(self):
        voter = ParliamentUser.objects.create_user(
            user_id='v9', name='Voter Nine', username='v9', member_type='Member')
        Vote.objects.create(user=voter, legislation=self.leg, vote_choice='yes')
        self.client.post(reverse('end_vote', args=[self.leg.id]))
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.status, 'passed')
        self.assertTrue(self.leg.passed)

    def test_reopened_legislation_returns_to_vote_page(self):
        self.client.post(reverse('end_vote', args=[self.leg.id]))
        resp = self.client.post(reverse('reopen_legislation', args=[self.leg.id]))
        self.leg.refresh_from_db()
        self.assertFalse(self.leg.voting_closed)
        self.assertEqual(self.leg.status, 'draft')
        self.assertIsNone(self.leg.voting_ended_at)
        page = self.client.get(reverse('vote'))
        self.assertContains(page, 'Status Integration Leg')


class LegislationPageCrossoverTests(TestCase):
    """
    v3.13.3 — crossover checks between the vote page, the Legislation page
    (passed_legislation), and My Work (view_legislation_history).
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.officer = ParliamentUser.objects.create_user(
            user_id='off3', name='Off Three', username='off3', member_type='Officer')
        self.officer.set_password('testpass')
        self.officer.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='m3', name='Member Three', username='m3', member_type='Member')
        self.member.set_password('testpass')
        self.member.save()
        self.client.force_login(self.officer)
        # Open legislation with voting_starts_at=NULL (the common case:
        # vote-page uploads and committee pushes don't set it)
        self.leg = Legislation.objects.create(
            title='Crossover Open Leg', description='D', posted_by=self.officer,
            available_at=timezone.now() - timedelta(hours=1),
            vote_mode='percentage', required_percentage='51', document='test.pdf')

    def test_detail_view_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('passed_legislation_detail', kwargs={'pk': self.leg.pk}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_active_tab_includes_null_voting_starts_at(self):
        resp = self.client.get(reverse('passed_legislation') + '?status=active')
        self.assertContains(resp, 'Crossover Open Leg')

    def test_pending_tab_hides_scheduled_from_non_authors(self):
        Legislation.objects.create(
            title='Scheduled Secret Leg', description='D', posted_by=self.officer,
            available_at=timezone.now() + timedelta(days=1),
            vote_mode='percentage', required_percentage='51', document='test.pdf')
        # Author sees it in pending
        resp = self.client.get(reverse('passed_legislation') + '?status=pending')
        self.assertContains(resp, 'Scheduled Secret Leg')
        # Another member does not (vote page promises "not yet visible to others")
        self.client.force_login(self.member)
        resp = self.client.get(reverse('passed_legislation') + '?status=pending')
        self.assertNotContains(resp, 'Scheduled Secret Leg')

    def test_my_work_shows_open_then_failed_after_end_vote(self):
        resp = self.client.get(reverse('view_legislation_history'))
        self.assertContains(resp, 'Crossover Open Leg')
        self.client.post(reverse('end_vote', args=[self.leg.id]))
        resp = self.client.get(reverse('view_legislation_history') + '?status=failed')
        self.assertContains(resp, 'Crossover Open Leg')

    def test_failed_vote_shows_on_legislation_page_failed_tab(self):
        Vote.objects.create(user=self.member, legislation=self.leg, vote_choice='no')
        self.client.post(reverse('end_vote', args=[self.leg.id]))
        resp = self.client.get(reverse('passed_legislation') + '?status=failed')
        self.assertContains(resp, 'Crossover Open Leg')


class VoteOpenTimingTests(TestCase):
    """
    v3.13.3 — legislation "not opening on its own": votable_ids in the tally
    poll (drives the page auto-reload) and timezone-correct scheduling through
    the real upload POST.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.chair = ParliamentUser.objects.create_user(
            user_id='ch1', name='Chair One', username='ch1', member_type='Chair')
        self.chair.set_password('testpass')
        self.chair.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='m4', name='Member Four', username='m4', member_type='Member')
        self.member.set_password('testpass')
        self.member.save()
        self.client.force_login(self.chair)

    def _tally_ids(self, as_user=None):
        if as_user:
            self.client.force_login(as_user)
        resp = self.client.get(reverse('vote_tally'))
        return resp.json().get('votable_ids', [])

    def test_open_legislation_in_votable_ids(self):
        leg = Legislation.objects.create(
            title='Open Now', description='D', posted_by=self.chair,
            available_at=timezone.now() - timedelta(minutes=1),
            vote_mode='percentage', required_percentage='51', document='t.pdf')
        self.assertIn(leg.id, self._tally_ids())
        self.assertIn(leg.id, self._tally_ids(as_user=self.member))

    def test_scheduled_legislation_not_votable_until_start(self):
        leg = Legislation.objects.create(
            title='Opens Later', description='D', posted_by=self.chair,
            available_at=timezone.now() - timedelta(minutes=1),
            voting_starts_at=timezone.now() + timedelta(hours=1),
            vote_mode='percentage', required_percentage='51', document='t.pdf')
        self.assertNotIn(leg.id, self._tally_ids())
        # Flip the start time into the past — now it must be votable
        leg.voting_starts_at = timezone.now() - timedelta(seconds=1)
        leg.save(update_fields=['voting_starts_at'])
        self.assertIn(leg.id, self._tally_ids())

    def test_unavailable_legislation_hidden_from_members_but_not_author(self):
        leg = Legislation.objects.create(
            title='Not Yet Available', description='D', posted_by=self.chair,
            available_at=timezone.now() + timedelta(hours=1),
            vote_mode='percentage', required_percentage='51', document='t.pdf')
        # Author sees the card (scheduled banner) but it is not votable
        self.assertNotIn(leg.id, self._tally_ids())
        self.assertNotIn(leg.id, self._tally_ids(as_user=self.member))

    def test_upload_with_past_available_at_is_immediately_votable(self):
        """End-to-end: the datetime-local string a browser submits (naive,
        local wall clock) must open the vote immediately when set in the past."""
        local_now = timezone.localtime()
        stamp = (local_now - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('vote'), {
            'title': 'Timing Upload Leg',
            'description': 'D',
            'available_at': stamp,
            'vote_mode': 'plurality',
            'plurality_option_1': 'Alpha',
            'plurality_option_2': 'Beta',
            'required_percentage': '51',
        }, follow=True)
        leg = Legislation.objects.get(title='Timing Upload Leg')
        self.assertTrue(leg.is_available(), 'available_at parsed into the future — timezone skew')
        self.assertTrue(leg.voting_has_started())
        self.assertIn(leg.id, self._tally_ids(as_user=self.member))

    def test_upload_with_future_available_at_stays_scheduled(self):
        local_now = timezone.localtime()
        stamp = (local_now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('vote'), {
            'title': 'Future Upload Leg',
            'description': 'D',
            'available_at': stamp,
            'vote_mode': 'plurality',
            'plurality_option_1': 'Alpha',
            'plurality_option_2': 'Beta',
            'required_percentage': '51',
        }, follow=True)
        leg = Legislation.objects.get(title='Future Upload Leg')
        self.assertFalse(leg.is_available())
        # ...and the stored instant must be ~2h out, not skewed by a UTC offset
        delta = (leg.available_at - timezone.now()).total_seconds()
        self.assertTrue(3600 < delta < 10800,
                        f'available_at skewed: {delta/3600:.1f}h out instead of ~2h')


class OpenNowTests(TestCase):
    """v3.13.3 — 'Now' buttons: instantly reveal / open scheduled legislation."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.chair = ParliamentUser.objects.create_user(
            user_id='ch2', name='Chair Two', username='ch2', member_type='Chair')
        self.chair.set_password('testpass')
        self.chair.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='m5', name='Member Five', username='m5', member_type='Member')
        self.member.set_password('testpass')
        self.member.save()
        self.client.force_login(self.chair)
        self.leg = Legislation.objects.create(
            title='Scheduled For Later', description='D', posted_by=self.chair,
            available_at=timezone.now() + timedelta(hours=2),
            voting_starts_at=timezone.now() + timedelta(hours=3),
            vote_mode='percentage', required_percentage='51', document='t.pdf')
        self.url = reverse('open_legislation_now', args=[self.leg.id])

    def test_open_now_makes_immediately_votable(self):
        resp = self.client.post(self.url, {'mode': 'open'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.is_available())
        self.assertTrue(self.leg.voting_has_started())
        # Members see it in the poll (drives their page auto-reload)
        self.client.force_login(self.member)
        ids = self.client.get(reverse('vote_tally')).json()['votable_ids']
        self.assertIn(self.leg.id, ids)

    def test_show_now_reveals_but_keeps_voting_scheduled(self):
        self.client.post(self.url, {'mode': 'show'})
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.is_available())
        self.assertFalse(self.leg.voting_has_started())  # scheduled start kept
        self.assertGreater(self.leg.voting_starts_at, timezone.now())

    def test_non_author_forbidden(self):
        self.client.force_login(self.member)
        resp = self.client.post(self.url, {'mode': 'open'})
        self.assertEqual(resp.status_code, 403)
        self.leg.refresh_from_db()
        self.assertFalse(self.leg.is_available())

    def test_closed_vote_rejected(self):
        self.leg.voting_closed = True
        self.leg.save(update_fields=['voting_closed'])
        before = self.leg.available_at
        self.client.post(self.url, {'mode': 'open'}, follow=True)
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.available_at, before)


class UploadSchedulingRenderTests(TestCase):
    """
    v3.13.3 — reproduce Mason's report: upload with a FUTURE available_at and
    no voting-starts time must render as scheduled (no vote form), not open.
    Also: document optional with a detailed (20+ char) description.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.chair = ParliamentUser.objects.create_user(
            user_id='ch3', name='Chair Three', username='ch3', member_type='Chair')
        self.chair.set_password('testpass')
        self.chair.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='m6', name='Member Six', username='m6', member_type='Member')
        self.client.force_login(self.chair)
        Attendance.objects.create(user=self.chair, status='present')

    def _upload(self, minutes_ahead, title, extra=None):
        stamp = (timezone.localtime() + timedelta(minutes=minutes_ahead)).strftime('%Y-%m-%dT%H:%M')
        data = {
            'title': title,
            'description': 'A sufficiently detailed description of this legislation item.',
            'available_at': stamp,
            'vote_mode': 'percentage',
            'required_percentage': '51',
        }
        if extra:
            data.update(extra)
        return self.client.post(reverse('vote'), data, follow=True)

    def test_future_upload_renders_scheduled_not_open(self):
        resp = self._upload(45, 'Future Sched Leg')
        leg = Legislation.objects.get(title='Future Sched Leg')
        self.assertFalse(leg.is_available())
        self.assertFalse(leg.voting_has_started())
        # Author sees the card with the Scheduled banner, and NO vote form
        page = self.client.get(reverse('vote'))
        self.assertContains(page, 'Future Sched Leg')
        self.assertContains(page, 'Scheduled for')
        content = page.content.decode()
        # The vote form for this leg (hidden legislation_id input) must not exist
        self.assertNotIn(f'name="legislation_id" value="{leg.id}"', content)
        # And the success message must say when it opens, not "now"
        self.assertContains(resp, 'voting opens')
        self.assertNotContains(resp, 'visible now')
        # Members can't see it at all
        self.client.force_login(self.member)
        page = self.client.get(reverse('vote'))
        self.assertNotContains(page, 'Future Sched Leg')

    def test_past_upload_renders_open_with_vote_form(self):
        self._upload(-5, 'Past Open Leg')
        leg = Legislation.objects.get(title='Past Open Leg')
        self.assertTrue(leg.voting_has_started())
        page = self.client.get(reverse('vote'))
        self.assertIn(f'name="legislation_id" value="{leg.id}"',
                      page.content.decode())

    def test_document_optional_with_detailed_description(self):
        # 20+ char description, percentage mode, NO document — must save
        resp = self._upload(-1, 'No Doc Detailed Leg')
        self.assertTrue(Legislation.objects.filter(title='No Doc Detailed Leg').exists())

    def test_document_required_with_short_description(self):
        stamp = timezone.localtime().strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post(reverse('vote'), {
            'title': 'Short Desc Leg',
            'description': 'Too short',
            'available_at': stamp,
            'vote_mode': 'percentage',
            'required_percentage': '51',
        }, follow=True)
        self.assertFalse(Legislation.objects.filter(title='Short Desc Leg').exists())
        self.assertContains(resp, 'NOT saved')


class ManualVotingOpenTests(TestCase):
    """
    v3.13.3 — separate voting-start mode: voting_mode_choice=separate with a
    blank Voting Starts means voting waits for "Open Voting Now" instead of
    opening with availability.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.chair = ParliamentUser.objects.create_user(
            user_id='ch4', name='Chair Four', username='ch4', member_type='Chair')
        self.chair.set_password('testpass')
        self.chair.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='m7', name='Member Seven', username='m7', member_type='Member')
        self.client.force_login(self.chair)
        Attendance.objects.create(user=self.chair, status='present')

    def _upload(self, mode_choice, voting_starts=''):
        stamp = (timezone.localtime() - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M')
        return self.client.post(reverse('vote'), {
            'title': 'Manual Mode Leg',
            'description': 'A sufficiently detailed description of this item.',
            'available_at': stamp,
            'voting_starts_at': voting_starts,
            'voting_mode_choice': mode_choice,
            'vote_mode': 'percentage',
            'required_percentage': '51',
        }, follow=True)

    def test_separate_blank_start_waits_for_manual_open(self):
        resp = self._upload('separate')
        leg = Legislation.objects.get(title='Manual Mode Leg')
        self.assertTrue(leg.voting_manual_open)
        self.assertTrue(leg.is_available())
        self.assertFalse(leg.voting_has_started())
        self.assertContains(resp, 'voting opens when you open it')
        # Members see the document but no vote form, and it's not votable
        self.client.force_login(self.member)
        page = self.client.get(reverse('vote'))
        self.assertContains(page, 'Manual Mode Leg')
        self.assertNotIn(f'name="legislation_id" value="{leg.id}"', page.content.decode())
        ids = self.client.get(reverse('vote_tally')).json()['votable_ids']
        self.assertNotIn(leg.id, ids)

    def test_open_voting_now_opens_manual_mode(self):
        self._upload('separate')
        leg = Legislation.objects.get(title='Manual Mode Leg')
        self.client.post(reverse('open_legislation_now', args=[leg.id]), {'mode': 'open'})
        leg.refresh_from_db()
        self.assertTrue(leg.voting_has_started())
        self.assertIsNotNone(leg.voting_starts_at)

    def test_unified_default_unchanged(self):
        self._upload('unified')
        leg = Legislation.objects.get(title='Manual Mode Leg')
        self.assertFalse(leg.voting_manual_open)
        self.assertTrue(leg.voting_has_started())

    def test_separate_with_scheduled_start_is_not_manual(self):
        future = (timezone.localtime() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        self._upload('separate', voting_starts=future)
        leg = Legislation.objects.get(title='Manual Mode Leg')
        self.assertFalse(leg.voting_manual_open)
        self.assertFalse(leg.voting_has_started())

    def test_manual_item_counts_as_pending_not_active_on_legislation_page(self):
        self._upload('separate')
        leg = Legislation.objects.get(title='Manual Mode Leg')
        active = self.client.get(reverse('passed_legislation') + '?status=active')
        self.assertNotContains(active, 'Manual Mode Leg')
        pending = self.client.get(reverse('passed_legislation') + '?status=pending')
        self.assertContains(pending, 'Manual Mode Leg')


class AutoCloseTaskTests(TestCase):
    """v3.14.0 — deadline auto-close: result parity with end_vote."""

    def setUp(self):
        cache.clear()
        self.chair = ParliamentUser.objects.create_user(
            user_id='ch5', name='Chair Five', username='ch5', member_type='Chair')

    def _leg(self, **kw):
        defaults = dict(
            title='Deadline Leg', description='D', posted_by=self.chair,
            available_at=timezone.now() - timedelta(hours=2),
            voting_ends_at=timezone.now() - timedelta(minutes=5),
            vote_mode='percentage', required_percentage='51', document='t.pdf')
        defaults.update(kw)
        return Legislation.objects.create(**defaults)

    def test_failed_percentage_close(self):
        from src.tasks.votes import auto_open_close_chapter_votes
        from src.models import ActivityLog
        leg = self._leg()
        voter = ParliamentUser.objects.create_user(
            user_id='v10', name='V Ten', username='v10', member_type='Member')
        Vote.objects.create(user=voter, legislation=leg, vote_choice='no')
        auto_open_close_chapter_votes()
        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)
        self.assertFalse(leg.passed)
        self.assertEqual(leg.status, 'failed')
        self.assertTrue(ActivityLog.objects.filter(
            action_type='vote_ended', object_id=leg.id).exists())

    def test_plurality_gets_result_and_tie_fails(self):
        """Old task computed total from yes+no — always 0 for plurality."""
        from src.tasks.votes import auto_open_close_chapter_votes
        win = self._leg(title='Plur Win', vote_mode='plurality',
                        plurality_options=['A', 'B'], document=None)
        tie = self._leg(title='Plur Tie', vote_mode='plurality',
                        plurality_options=['A', 'B'], document=None)
        voters = [ParliamentUser.objects.create_user(
            user_id=f'pv{i}', name=f'PV {i}', username=f'pv{i}',
            member_type='Member') for i in range(3)]
        Vote.objects.create(user=voters[0], legislation=win, vote_choice='A')
        Vote.objects.create(user=voters[1], legislation=win, vote_choice='A')
        Vote.objects.create(user=voters[2], legislation=win, vote_choice='B')
        Vote.objects.create(user=voters[0], legislation=tie, vote_choice='A')
        Vote.objects.create(user=voters[1], legislation=tie, vote_choice='B')
        auto_open_close_chapter_votes()
        win.refresh_from_db(); tie.refresh_from_db()
        self.assertTrue(win.passed)
        self.assertEqual(win.status, 'passed')
        self.assertFalse(tie.passed)
        self.assertEqual(tie.status, 'failed')


class VoteReceiptTests(TestCase):
    """v3.14.0 — tamper-evident receipts."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.voter = ParliamentUser.objects.create_user(
            user_id='v11', name='V Eleven', username='v11', member_type='Member')
        self.voter.set_password('testpass')
        self.voter.save()
        self.client.force_login(self.voter)
        Attendance.objects.create(user=self.voter, status='present')
        self.leg = Legislation.objects.create(
            title='Receipt Leg', description='D', posted_by=self.voter,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='t.pdf', anonymous_vote=True)

    def test_roundtrip_and_tamper_detection(self):
        from src.utils.vote_receipts import make_receipt, verify_receipt
        vote = Vote.objects.create(user=self.voter, legislation=self.leg, vote_choice='yes')
        token = make_receipt(self.voter, self.leg, [vote])
        result = verify_receipt(token)
        self.assertTrue(result['valid'] and result['intact'])
        self.assertEqual(result['ballots'], 1)
        # Choice altered → digest mismatch
        vote.vote_choice = 'no'
        vote.save(update_fields=['vote_choice'])
        result = verify_receipt(token)
        self.assertTrue(result['valid'])
        self.assertFalse(result['intact'])
        # Ballot deleted → missing
        vote.delete()
        result = verify_receipt(token)
        self.assertEqual(result['missing'], 1)
        # Garbage token
        self.assertFalse(verify_receipt('garbage.token.here')['valid'])

    def test_receipt_on_personal_tab_and_view_verifies(self):
        resp = self.client.post(reverse('vote'), {
            'action': 'cast_vote', 'legislation_id': self.leg.id,
            'vote_choice': 'yes', 'password': 'testpass'}, follow=True)
        self.assertContains(resp, 'My Ballots')  # pointer in flash message
        # Receipt lives on the Legislation page's Personal tab
        page = self.client.get(reverse('passed_legislation') + '?status=personal')
        self.assertContains(page, 'Receipt Leg')
        self.assertContains(page, 'Show receipt')
        self.assertContains(page, 'visible only to you')  # anonymous note
        import re as _re
        m = _re.search(r'js-receipt-token[^>]*>([^<]+)</code>', page.content.decode())
        self.assertIsNotNone(m)
        token = m.group(1).strip()
        verify = self.client.post(reverse('verify_vote_receipt'), {'receipt': token})
        self.assertContains(verify, 'Receipt verified')
        self.assertContains(verify, 'belongs to your account')

    def test_expired_receipt_rejected_and_hidden_from_personal_tab(self):
        from src.utils.vote_receipts import make_receipt, verify_receipt
        vote = Vote.objects.create(user=self.voter, legislation=self.leg, vote_choice='yes')
        old = timezone.now() - timedelta(days=100)
        Vote.objects.filter(pk=vote.pk).update(cast_at=old)  # bypass auto_now_add
        vote.refresh_from_db()
        # Verification refuses tokens older than the 3-month window
        token = make_receipt(self.voter, self.leg, [vote], cast_at=vote.cast_at)
        result = verify_receipt(token)
        self.assertFalse(result['valid'])
        self.assertIn('expired', result['reason'])
        # Personal tab shows the expired notice instead of a token
        page = self.client.get(reverse('passed_legislation') + '?status=personal')
        self.assertContains(page, 'Receipt expired')
        self.assertNotContains(page, 'Show receipt')

    def test_expiry_notice_task_notifies_crossing_ballots(self):
        from src.tasks.votes import notify_expired_vote_receipts
        from src.models import Notification
        vote = Vote.objects.create(user=self.voter, legislation=self.leg, vote_choice='yes')
        Vote.objects.filter(pk=vote.pk).update(
            cast_at=timezone.now() - timedelta(days=90, hours=6))
        notify_expired_vote_receipts()
        self.assertTrue(Notification.objects.filter(
            recipient=self.voter, title__icontains='receipt').exists())


class TurnoutDisplayTests(TestCase):
    """v3.14.0 — turnout panel for chairs/officers."""

    def test_turnout_counts_and_non_voters(self):
        cache.clear()
        client = Client()
        officer = ParliamentUser.objects.create_user(
            user_id='off4', name='Officer Four', username='off4', member_type='Officer')
        officer.set_password('testpass'); officer.save()
        slacker = ParliamentUser.objects.create_user(
            user_id='m8', name='Slacker Member', username='m8', member_type='Member')
        voter = ParliamentUser.objects.create_user(
            user_id='m9', name='Diligent Member', username='m9', member_type='Member')
        for u in (slacker, voter):
            Attendance.objects.create(user=u, status='present')
        leg = Legislation.objects.create(
            title='Turnout Leg', description='D', posted_by=officer,
            available_at=timezone.now() - timedelta(minutes=5),
            vote_mode='percentage', required_percentage='51', document='t.pdf')
        Vote.objects.create(user=voter, legislation=leg, vote_choice='yes')
        client.force_login(officer)
        page = client.get(reverse('vote'))
        self.assertContains(page, 'Turnout: 1 of 2 present')
        self.assertContains(page, 'Slacker Member')


class VoteEventBroadcastTests(TestCase):
    def test_broadcast_never_raises(self):
        from src.utils.vote_events import broadcast_vote_event
        broadcast_vote_event('opened', 12345)  # must be a silent no-op at worst


class VoteResultPageTests(TestCase):
    """v3.14.0 — restyled results page renders for all modes incl. anonymous."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.chair = ParliamentUser.objects.create_user(
            user_id='ch6', name='Chair Six', username='ch6', member_type='Chair')
        self.chair.set_password('testpass'); self.chair.save()
        self.client.force_login(self.chair)
        self.voter = ParliamentUser.objects.create_user(
            user_id='v12', name='V Twelve', username='v12', member_type='Member')

    def _end(self, leg):
        return self.client.post(reverse('end_vote', args=[leg.id]))

    def test_anonymous_percentage_result_renders_with_chart_data(self):
        leg = Legislation.objects.create(
            title='Anon Result Leg', description='D', posted_by=self.chair,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='t.pdf', anonymous_vote=True)
        Vote.objects.create(user=self.voter, legislation=leg, vote_choice='yes')
        resp = self._end(leg)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Vote Passed')
        self.assertContains(resp, 'vote-chart-breakdown')  # chart data island present
        self.assertNotContains(resp, 'V Twelve')  # anonymous: no voter names

    def test_piecewise_result_has_banner(self):
        leg = Legislation.objects.create(
            title='Piecewise Result Leg', description='D', posted_by=self.chair,
            available_at=timezone.now(), vote_mode='piecewise',
            required_number=2, document='t.pdf')
        Vote.objects.create(user=self.voter, legislation=leg, vote_choice='yes')
        resp = self._end(leg)
        self.assertContains(resp, 'Did Not Pass')
        self.assertContains(resp, 'Required 2 yes votes')

    def test_plurality_result_renders(self):
        leg = Legislation.objects.create(
            title='Plur Result Leg', description='D', posted_by=self.chair,
            available_at=timezone.now(), vote_mode='plurality',
            plurality_options=['Alpha', 'Beta'])
        Vote.objects.create(user=self.voter, legislation=leg, vote_choice='Alpha')
        resp = self._end(leg)
        self.assertContains(resp, 'Winner: Alpha')


class ServerResolvedNowTests(TestCase):
    """v3.14.0 — the Now button's is_now flag beats a skewed device clock."""

    def test_skewed_future_stamp_with_is_now_flag_opens_immediately(self):
        cache.clear()
        client = Client()
        chair = ParliamentUser.objects.create_user(
            user_id='ch7', name='Chair Seven', username='ch7', member_type='Chair')
        chair.set_password('testpass'); chair.save()
        client.force_login(chair)
        # Device clock an hour fast: the filled text is in the future, but the
        # flag says "now" — server time must win
        skewed = (timezone.localtime() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        client.post(reverse('vote'), {
            'title': 'Skewed Clock Leg',
            'description': 'A sufficiently detailed description of this item.',
            'available_at': skewed,
            'available_at_is_now': '1',
            'vote_mode': 'plurality',
            'plurality_option_1': 'A', 'plurality_option_2': 'B',
            'required_percentage': '51',
        })
        leg = Legislation.objects.get(title='Skewed Clock Leg')
        self.assertTrue(leg.is_available())
        self.assertTrue(leg.voting_has_started())


class AlreadyVotedStateTests(TestCase):
    """v3.14.0 — the ballot form is replaced by a confirmation once voted."""

    def test_form_hidden_after_voting(self):
        cache.clear()
        client = Client()
        voter = ParliamentUser.objects.create_user(
            user_id='v13', name='V Thirteen', username='v13', member_type='Member')
        voter.set_password('testpass'); voter.save()
        client.force_login(voter)
        Attendance.objects.create(user=voter, status='present')
        leg = Legislation.objects.create(
            title='Voted State Leg', description='D', posted_by=voter,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='t.pdf')
        # Before voting: ballot form present
        page = client.get(reverse('vote'))
        self.assertIn(f'name="legislation_id" value="{leg.id}"', page.content.decode())
        # Vote, then: confirmation instead of the form
        client.post(reverse('vote'), {
            'action': 'cast_vote', 'legislation_id': leg.id,
            'vote_choice': 'yes', 'password': 'testpass'})
        page = client.get(reverse('vote'))
        content = page.content.decode()
        self.assertNotIn(f'name="legislation_id" value="{leg.id}"', content)
        self.assertContains(page, 'Your vote has been recorded')
        self.assertContains(page, 'View your ballot')


class SplitEndpointTests(TestCase):
    """v3.14.1 — vote_view POST multiplex split into dedicated endpoints.

    The classes above still POST to reverse('vote'), which now exercises the
    legacy dispatcher (kept so tabs opened before the deploy aren't silently
    dropped) — do NOT "modernize" them, that coverage is intentional. This
    class hits the new endpoints directly, which is what vote.html now does.
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.officer = ParliamentUser.objects.create_user(
            user_id='se1', name='Split Officer', username='se1',
            member_type='Officer')
        self.officer.set_password('testpass')
        self.officer.save()
        self.member = ParliamentUser.objects.create_user(
            user_id='se2', name='Split Member', username='se2',
            member_type='Member')
        self.member.set_password('testpass')
        self.member.save()
        self.client.force_login(self.officer)
        Attendance.objects.create(user=self.officer, status='present')
        self.leg = Legislation.objects.create(
            title='Split Endpoint Leg', description='D',
            posted_by=self.officer, available_at=timezone.now(),
            vote_mode='percentage', required_percentage='51',
            document='test.pdf')

    def test_cast_vote_endpoint_records_vote(self):
        resp = self.client.post(reverse('cast_vote'), {
            'action': 'cast_vote',
            'legislation_id': self.leg.id,
            'vote_choice': 'yes',
            'password': 'testpass',
        }, follow=True)
        self.assertTrue(Vote.objects.filter(
            user=self.officer, legislation=self.leg,
            vote_choice='yes').exists())
        self.assertRedirects(resp, reverse('vote'))

    def test_cast_vote_endpoint_rejects_get(self):
        self.assertEqual(self.client.get(reverse('cast_vote')).status_code, 405)

    def test_upload_endpoint_creates_legislation(self):
        stamp = timezone.localtime().strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('upload_chapter_legislation'), {
            'title': 'Split Upload Leg',
            'description': 'A sufficiently detailed description of this item.',
            'available_at': stamp,
            'available_at_is_now': '1',
            'vote_mode': 'percentage',
            'required_percentage': '51',
        })
        self.assertTrue(
            Legislation.objects.filter(title='Split Upload Leg').exists())

    def test_upload_endpoint_rejects_non_officer(self):
        self.client.force_login(self.member)
        resp = self.client.post(reverse('upload_chapter_legislation'), {
            'title': 'Sneaky Leg',
            'description': 'A sufficiently detailed description of this item.',
            'available_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        }, follow=True)
        self.assertFalse(Legislation.objects.filter(title='Sneaky Leg').exists())
        self.assertContains(resp, 'Only chairs and officers')

    def test_attendance_endpoint_marks_and_returns_json(self):
        resp = self.client.post(reverse('mark_attendance_quick'), {
            'target_user_id': self.member.user_id,
            'attendance_status': 'present',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertTrue(Attendance.objects.filter(
            user=self.member, status='present').exists())

    def test_attendance_endpoint_403_for_members(self):
        self.client.force_login(self.member)
        resp = self.client.post(reverse('mark_attendance_quick'), {
            'target_user_id': self.officer.user_id,
            'attendance_status': 'present',
        })
        self.assertEqual(resp.status_code, 403)

    def test_legacy_unrecognized_post_gets_error_not_silence(self):
        """A POST to /vote/ matching no legacy branch must explain itself
        (the v3.13.3 no-silent-drops rule), not quietly re-render."""
        resp = self.client.post(reverse('vote'), {'bogus': '1'}, follow=True)
        self.assertContains(resp, 'page may be out of date')
        self.assertFalse(Vote.objects.exists())
