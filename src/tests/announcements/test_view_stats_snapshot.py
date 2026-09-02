"""
v3.28.6 — announcement view-rate stats used to divide by the CURRENT roster
instead of the audience an announcement was actually published to. Reported
live 09-02-26: an announcement `visible_to=['Pledge']` showed "5 of 0
members (500%)" once every pledge it was originally sent to had initiated —
`get_view_stats()`'s denominator was `ParliamentUser.objects.filter(
member_status='Active', member_type='Pledge').count()`, recomputed fresh on
every call, so it shrank to zero as the pledge class graduated even though
the announcement had genuinely been seen by everyone it was sent to.

Separately: the numerator (site/email views) counted ANY logged-in viewer
whose member_type happened to match `visible_to`, with no `member_status`
check at all — so an alumnus who still has a working account and browses
the site (rather than receiving the announcement by email, since alumni
aren't sent chapter email) could inflate the view count for an announcement
they were never the intended audience for.

Both are fixed by `Announcement.target_audience_snapshot` — a list of user
ids frozen the first time anything needs to know the audience after
publish — and `UserAnnouncementView.counted_in_target`, decided once per
view from `Announcement.is_in_target_audience()` rather than recomputed at
read time. See the docstrings on those for the full reasoning, including
the accepted limitation that a snapshot taken TODAY for an announcement
published in the past (the migration 0027 backfill) cannot recover history
that was never recorded.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from src.models import Announcement, ParliamentUser, UserAnnouncementView


def make_user(user_id, member_type='Member', member_status='Active'):
    return ParliamentUser.objects.create_user(
        user_id=user_id, name=f'Test {user_id}', username=user_id,
        member_type=member_type, member_status=member_status,
    )


class EnsureTargetAudienceSnapshotTests(TestCase):
    def test_unpublished_announcement_gets_no_snapshot(self):
        """
        A draft's audience isn't real until it goes out — freezing one early
        would just be freezing a guess an edit (or publish_at itself) could
        still invalidate.
        """
        make_user('p1', member_type='Pledge')
        announcement = Announcement.objects.create(
            title='Future', content='...', posted_by=make_user('officer1', 'Officer'),
            publish_at=timezone.now() + timedelta(days=1),
            visible_to=['Pledge'],
        )
        self.assertEqual(announcement.ensure_target_audience_snapshot(), [])
        announcement.refresh_from_db()
        self.assertEqual(announcement.target_audience_snapshot, [])

    def test_published_announcement_freezes_current_matching_active_users(self):
        pledge1 = make_user('p1', member_type='Pledge')
        pledge2 = make_user('p2', member_type='Pledge')
        make_user('m1', member_type='Member')  # not targeted, must not appear
        make_user('p3', member_type='Pledge', member_status='Alumni')  # not Active
        announcement = Announcement.objects.create(
            title='Pledge only', content='...', posted_by=make_user('officer1', 'Officer'),
            visible_to=['Pledge'],
        )
        snapshot = announcement.ensure_target_audience_snapshot()
        self.assertEqual(set(snapshot), {pledge1.pk, pledge2.pk})

    def test_it_is_a_noop_after_the_first_call(self):
        """
        ⚠️ THE WHOLE POINT. Calling this again after the roster changes must
        NOT re-derive the snapshot — that would just reintroduce the bug one
        layer down (a "frozen" value that keeps thawing).
        """
        pledge = make_user('p1', member_type='Pledge')
        announcement = Announcement.objects.create(
            title='Pledge only', content='...', posted_by=make_user('officer1', 'Officer'),
            visible_to=['Pledge'],
        )
        first = announcement.ensure_target_audience_snapshot()
        self.assertEqual(first, [pledge.pk])

        # The pledge initiates.
        pledge.member_type = 'Member'
        pledge.save(update_fields=['member_type'])

        second = announcement.ensure_target_audience_snapshot()
        self.assertEqual(second, first, 'snapshot must not silently re-derive on a later call')

    def test_empty_visible_to_snapshots_everyone_active(self):
        officer = make_user('officer1', 'Officer')
        m1 = make_user('m1', member_type='Member')
        m2 = make_user('m2', member_type='Advisor')
        make_user('m3', member_type='Member', member_status='Alumni')
        announcement = Announcement.objects.create(
            title='Everyone', content='...', posted_by=officer,
        )
        snapshot = announcement.ensure_target_audience_snapshot()
        # "Everyone" genuinely means everyone Active, including the officer
        # who posted it — empty visible_to isn't "everyone except staff".
        self.assertEqual(set(snapshot), {officer.pk, m1.pk, m2.pk})


class GetViewStatsReproducesTheReportedBugTests(TestCase):
    """
    The exact scenario from the live report: an announcement sent to a
    pledge class, viewed by that class, then the class initiates. Before
    this fix, the denominator would collapse toward zero as pledges
    initiated; after, it stays fixed at what it was when the audience was
    frozen.
    """

    def setUp(self):
        self.officer = make_user('officer1', 'Officer')
        self.pledge1 = make_user('p1', member_type='Pledge')
        self.pledge2 = make_user('p2', member_type='Pledge')
        self.announcement = Announcement.objects.create(
            title='PNM info session', content='...', posted_by=self.officer,
            visible_to=['Pledge'],
        )

    def _record_view(self, user):
        self.announcement.ensure_target_audience_snapshot()
        UserAnnouncementView.objects.create(
            user=user, announcement=self.announcement, view_source='site',
            counted_in_target=self.announcement.is_in_target_audience(user),
        )

    def test_view_rate_survives_the_whole_pledge_class_initiating(self):
        self._record_view(self.pledge1)
        self._record_view(self.pledge2)

        stats = self.announcement.get_view_stats()
        self.assertEqual(stats['target_audience'], 2)
        self.assertEqual(stats['total_views'], 2)
        self.assertEqual(stats['view_rate'], 100.0)

        # The whole pledge class initiates. No new pledges have joined, so
        # the OLD (buggy) live-recount would find zero current Active
        # pledges — this is precisely the "5 of 0 members (500%)" report.
        self.pledge1.member_type = 'Member'
        self.pledge1.save(update_fields=['member_type'])
        self.pledge2.member_type = 'Member'
        self.pledge2.save(update_fields=['member_type'])

        stats_after = self.announcement.get_view_stats()
        self.assertEqual(stats_after['target_audience'], 2, 'denominator must not collapse when the audience initiates')
        self.assertEqual(stats_after['total_views'], 2)
        self.assertEqual(stats_after['view_rate'], 100.0, 'must not become 200%/500%/undefined')

    def test_view_rate_never_exceeds_100_percent(self):
        """
        Control for the progress-bar-blowout side effect: with numerator and
        denominator both drawn from the same frozen set, and `unique_together
        = ('user', 'announcement')` capping one view per eligible user, the
        rate is structurally bounded — this isn't a clamp, it falls out of
        the math.
        """
        self._record_view(self.pledge1)
        self._record_view(self.pledge2)
        self.pledge1.member_type = 'Member'
        self.pledge1.save(update_fields=['member_type'])
        stats = self.announcement.get_view_stats()
        self.assertLessEqual(stats['view_rate'], 100.0)


class NumeratorExcludesOffTargetViewersTests(TestCase):
    """
    An alumnus (or anyone else who can technically load the page but wasn't
    the intended audience) must not inflate the view count.
    """

    def test_alumnus_view_is_recorded_but_not_counted(self):
        officer = make_user('officer1', 'Officer')
        alum = make_user('alum1', member_type='Member', member_status='Alumni')
        announcement = Announcement.objects.create(
            title='All members', content='...', posted_by=officer,
        )
        announcement.ensure_target_audience_snapshot()
        self.assertFalse(
            announcement.is_in_target_audience(alum),
            'an Alumni (not Active) must never be in the target audience snapshot',
        )
        UserAnnouncementView.objects.create(
            user=alum, announcement=announcement, view_source='site',
            counted_in_target=announcement.is_in_target_audience(alum),
        )
        stats = announcement.get_view_stats()
        self.assertEqual(stats['total_views'], 0, "the alumnus's view exists but must not be counted")
        self.assertEqual(announcement.views.count(), 1, 'the raw view row itself is still recorded')

    def test_live_end_to_end_through_the_real_view(self):
        """
        Same thing, through the actual `/announcements/` view rather than
        constructing the UserAnnouncementView by hand — this is what
        actually happens when an alumnus with a working login browses the
        site, per the live report.
        """
        from django.test import Client

        officer = make_user('officer1', 'Officer')
        alum = make_user('alum1', member_type='Member', member_status='Alumni', )
        alum.set_password('testpass123')
        alum.save()
        announcement = Announcement.objects.create(
            title='All members', content='...', posted_by=officer,
        )

        client = Client()
        client.force_login(alum)
        response = client.get('/announcements/')
        self.assertEqual(response.status_code, 200)

        announcement.refresh_from_db()
        view = UserAnnouncementView.objects.get(user=alum, announcement=announcement)
        self.assertFalse(view.counted_in_target)
        self.assertEqual(announcement.get_view_stats()['total_views'], 0)


class AnnotateViewStatsMatchesUnannotatedTests(TestCase):
    """
    `manage_announcements` (the list page) uses `annotate_view_stats()` to
    get these numbers in one batched query; `announcement_stats` (the detail
    page) calls `get_view_stats()` on a single fetched object, which falls
    back to per-object queries. Both paths must agree, the same way they had
    to before this change — otherwise the list and the detail page could
    show different numbers for the same announcement.
    """

    def test_batched_and_unbatched_paths_agree(self):
        officer = make_user('officer1', 'Officer')
        pledge1 = make_user('p1', member_type='Pledge')
        pledge2 = make_user('p2', member_type='Pledge')
        alum = make_user('alum1', member_type='Pledge', member_status='Alumni')
        announcement = Announcement.objects.create(
            title='Pledge only', content='...', posted_by=officer, visible_to=['Pledge'],
        )
        announcement.ensure_target_audience_snapshot()
        for u in (pledge1, alum):
            UserAnnouncementView.objects.create(
                user=u, announcement=announcement, view_source='site',
                counted_in_target=announcement.is_in_target_audience(u),
            )

        unbatched = Announcement.objects.get(pk=announcement.pk).get_view_stats()

        batched_obj = Announcement.annotate_view_stats(
            Announcement.objects.filter(pk=announcement.pk)
        ).get()
        batched = batched_obj.get_view_stats()

        self.assertEqual(unbatched, batched)
        self.assertEqual(batched['total_views'], 1, 'only the pledge view should count, not the alumnus')


class EveryCreationSiteSetsCountedInTargetTests(TestCase):
    """
    ⚠️ THE ENUMERATION. There are (as of this writing) four places a
    `UserAnnouncementView` is created: the site-view bulk_create in
    `view/announcements.py`, the notification-dismiss get_or_create in
    `notifications.py`, the notification-bell "mark viewed" get_or_create in
    `view/notifications.py`, and the email-pixel get_or_create in
    `view/officer/manage_announcements.py`. Every one of them must set
    `counted_in_target` explicitly — a future call site that forgets falls
    back to the field's `default=True`, which silently reintroduces the
    over-counting bug for whatever population reaches it.

    This is a source scan, not an import-time check, so it also catches a
    call site nobody remembered to route through a test.
    """

    #: (path relative to BASE_DIR) — every non-test, non-migration file that
    #: constructs or get-or-creates a UserAnnouncementView. Update this list
    #: when adding a new one; a call site not listed here means this test
    #: wasn't updated for it and the walk below is incomplete, not that
    #: there's nothing to check.
    KNOWN_CREATION_SITES = [
        'src/view/announcements.py',
        'src/notifications.py',
        'src/view/notifications.py',
        'src/view/officer/manage_announcements.py',
    ]

    #: Matches the constructor (`UserAnnouncementView(...)`, used inside the
    #: bulk_create list in view/announcements.py) AND the two ORM manager
    #: methods actually used elsewhere (`.objects.get_or_create(...)`,
    #: `.objects.create(...)`) — a call site using a different manager method
    #: later would need adding here too.
    _CALL_PATTERN = re.compile(r'UserAnnouncementView(?:\.objects\.(?:get_or_create|create))?\(')

    def _calls_with_spans(self, text):
        """Every `UserAnnouncementView(...)`-shaped call in `text`, as (start, end) spans covering the full balanced-paren call."""
        spans = []
        for m in self._CALL_PATTERN.finditer(text):
            depth = 1
            i = m.end()
            while depth > 0 and i < len(text):
                if text[i] == '(':
                    depth += 1
                elif text[i] == ')':
                    depth -= 1
                i += 1
            spans.append((m.start(), i))
        return spans

    def test_every_known_call_site_sets_counted_in_target(self):
        base = Path(settings.BASE_DIR)
        failures = []
        for rel in self.KNOWN_CREATION_SITES:
            text = (base / rel).read_text(encoding='utf-8')
            calls = self._calls_with_spans(text)
            if not calls:
                failures.append(f'{rel}: no UserAnnouncementView(...) call found — KNOWN_CREATION_SITES is stale')
                continue
            for start, end in calls:
                chunk = text[start:end]
                if 'counted_in_target' not in chunk:
                    line = text.count('\n', 0, start) + 1
                    failures.append(f'{rel}:{line}: UserAnnouncementView(...) does not set counted_in_target')
        self.assertEqual(failures, [], '\n' + '\n'.join(failures))

    def test_the_scan_actually_flags_a_missing_kwarg(self):
        """Control: prove the span-matching regex can fail, not just pass."""
        bad = "UserAnnouncementView(user=u, announcement=a, view_source='site')"
        calls = self._calls_with_spans(bad)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('counted_in_target', bad[calls[0][0]:calls[0][1]])

    def test_no_call_site_is_missing_from_the_enumeration(self):
        """
        Companion control: grep the whole non-test, non-migration tree for
        the constructor and make sure nothing outside KNOWN_CREATION_SITES
        calls it — otherwise the enumeration above is silently incomplete.
        """
        base = Path(settings.BASE_DIR) / 'src'
        found = set()
        for path in base.rglob('*.py'):
            rel = str(path.relative_to(Path(settings.BASE_DIR))).replace('\\', '/')
            if rel.startswith('src/tests/') or rel.startswith('src/migrations/'):
                continue
            if rel == 'src/models/announcements.py':
                # Where the class is DEFINED (`class UserAnnouncementView(models.Model):`)
                # — that line matches the same substring but isn't a call.
                continue
            text = path.read_text(encoding='utf-8')
            if self._CALL_PATTERN.search(text):
                found.add(rel)
        self.assertEqual(
            found, set(self.KNOWN_CREATION_SITES),
            'KNOWN_CREATION_SITES is out of date with the actual call sites in src/',
        )
