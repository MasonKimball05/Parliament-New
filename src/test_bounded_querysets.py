"""
Pages that render a whole table must have a ceiling (v3.17.5).

WHY THESE TWO
-------------
Both came out of the 07-30-26 review, and both are the *same* mistake made in
opposite directions.

`view_all_reports` was fixed in v3.17.4 for a real problem — it queried
`CommitteeDocument` six times, once per `document_type` tab — by fetching once
and partitioning in Python. Correct, except the rewrite also turned a lazy
queryset into `list(...)` with no slice, so the whole table was materialized on
every load. The v3.17.4 comment said *"the page renders every document anyway,
so there is nothing to save by filtering in SQL"*: true of the **filtering**,
not of the **fetch**. `CommitteeDocument` is append-only and grows for the life
of the chapter.

`my_polls` on the legislation-history page had the expensive half fixed in
v3.17.3 (two annotations replaced prefetching every response object) and the
ceiling left off.

THE PART WORTH REMEMBERING
--------------------------
`view_all_reports` cannot be paginated the ordinary way: its six tabs are
client-side, so every tab's rows must be in the one response. A cap is the only
shape that fits — and **a capped page must not count the capped list**, or the
badges quietly under-report how much is hidden. The totals come from a separate
GROUP BY for exactly that reason.
"""

from unittest.mock import patch

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Announcement, AnnouncementPoll, Committee, CommitteeDocument, ParliamentUser,
)
from src.models.security import QuarantinedAccount
from src.view.officer.view_all_reports import DOCUMENT_FETCH_LIMIT
from src.view.view_legislation_history import MY_POLLS_LIMIT


def make_user(uid, member_type='Officer', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('bounded-test-pass-12345!')
    user.save()
    return user


class ViewAllReportsIsBoundedTests(TestCase):

    def setUp(self):
        self.officer = make_user('vr-officer')
        self.committee = Committee.objects.create(name='Finance', code='FIN')

    def _make_documents(self, n, document_type='report'):
        CommitteeDocument.objects.bulk_create([
            CommitteeDocument(
                committee=self.committee,
                uploaded_by=self.officer,
                title=f'Doc {i}',
                document=f'committee_documents/doc{i}.pdf',
                document_type=document_type,
            )
            for i in range(n)
        ])

    def _get(self):
        client = Client()
        client.force_login(self.officer)
        return client.get(reverse('view_all_reports'))

    def test_the_page_renders(self):
        self._make_documents(3)
        self.assertEqual(self._get().status_code, 200)

    def test_the_fetch_is_capped(self):
        self._make_documents(DOCUMENT_FETCH_LIMIT + 25)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['all_documents']), DOCUMENT_FETCH_LIMIT)
        self.assertTrue(response.context['documents_truncated'])

    def test_the_badges_report_true_totals_not_the_capped_list(self):
        """
        The whole point of the separate GROUP BY. If this ever starts counting
        `len(all_documents)`, a chapter past the cap is told it has exactly
        DOCUMENT_FETCH_LIMIT documents forever.
        """
        total = DOCUMENT_FETCH_LIMIT + 25
        self._make_documents(total)
        response = self._get()
        self.assertEqual(response.context['total_documents'], total)
        self.assertEqual(response.context['type_totals']['report'], total)
        self.assertGreater(
            response.context['total_documents'],
            len(response.context['all_documents']),
        )

    def test_totals_are_per_type(self):
        self._make_documents(4, 'report')
        self._make_documents(2, 'minutes')
        self._make_documents(1, 'agenda')
        response = self._get()
        totals = response.context['type_totals']
        self.assertEqual(totals['report'], 4)
        self.assertEqual(totals['minutes'], 2)
        self.assertEqual(totals['agenda'], 1)
        self.assertEqual(response.context['total_documents'], 7)

    def test_nothing_is_truncated_below_the_cap(self):
        self._make_documents(5)
        response = self._get()
        self.assertFalse(response.context['documents_truncated'])

    def test_the_page_survives_an_empty_table(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_documents'], 0)
        self.assertFalse(response.context['documents_truncated'])


class MyPollsIsBoundedTests(TestCase):

    def test_my_polls_is_capped(self):
        user = make_user('mp-member', 'Member')
        # `AnnouncementPoll.announcement` is a OneToOne, so a poll needs its own
        # announcement.
        for i in range(MY_POLLS_LIMIT + 5):
            announcement = Announcement.objects.create(
                title=f'Poll host {i}', content='x', posted_by=user, is_active=True,
            )
            AnnouncementPoll.objects.create(
                announcement=announcement, created_by=user, title=f'Poll {i}',
            )
        client = Client()
        client.force_login(user)
        response = client.get(reverse('view_legislation_history'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['my_polls']), MY_POLLS_LIMIT)


@patch('src.view.admin_v2.ALLOWED_USER_IDS', {'q-admin'})
class ActiveQuarantinesIsCountedOnceTests(TestCase):
    """
    A template calling `queryset.count` runs a fresh `SELECT COUNT(*)` EVERY
    time — the queryset cache does not cover it, and neither does having already
    iterated the queryset.

    Dev mode caught this on `admin_v2/security_dashboard.html`, which calls
    `active_quarantines.count` at four places (card border, card number, nav
    badge, section badge) and then iterates the same queryset: **five queries
    for one small list**. `quarantine_management.html` had the same shape in
    miniature — `.count` for the heading, a truthiness test, then the loop.

    The fix is the one the 07-28-26 review already applied to the global-search
    page: materialize in the view, `|length` in the template. This test locks it
    in, because `.count` reads more naturally than `|length` and will be typed
    again.
    """

    def setUp(self):
        self.admin = make_user('q-admin', 'Officer', is_admin=True)
        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

        for i in range(6):
            victim = make_user(f'q-victim-{i}', 'Member')
            QuarantinedAccount.objects.create(
                user=victim,
                ip_address='203.0.113.7',
                reason='probe',
                quarantined_by=self.admin,
                is_auto=False,
            )

    @staticmethod
    def _quarantine_counts(queries):
        return [
            q for q in queries
            if 'COUNT(*)' in q['sql'].upper() and 'src_quarantinedaccount' in q['sql']
        ]

    def test_security_dashboard_counts_quarantines_at_most_once(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('admin_v2_security'))
        self.assertEqual(response.status_code, 200)
        offenders = self._quarantine_counts(ctx.captured_queries)
        self.assertEqual(
            offenders, [],
            f'{len(offenders)} COUNT(*) on src_quarantinedaccount — the template '
            f'is calling .count on a queryset instead of |length on a list',
        )

    def test_quarantine_management_counts_quarantines_at_most_once(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('admin_v2_quarantine'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._quarantine_counts(ctx.captured_queries), [])

    def test_the_view_hands_the_template_a_list(self):
        """`|length` on a queryset still evaluates it; the list is the fix."""
        response = self.client.get(reverse('admin_v2_security'))
        self.assertIsInstance(response.context['active_quarantines'], list)
        self.assertEqual(len(response.context['active_quarantines']), 6)

    def test_no_template_calls_count_on_active_quarantines(self):
        """Static guard — `.count` is the natural thing to type."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / 'templates'
        offenders = []
        for path in sorted(root.rglob('*.html')):
            for line_no, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), 1):
                if 'active_quarantines.count' in line:
                    offenders.append(f'{path.relative_to(root)}:{line_no}')
        self.assertEqual(offenders, [], 'use |length — the view passes a list')
