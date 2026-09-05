"""
v3.29.11 — `review_excuses` was rewritten to fix an N+1 (one
`AttendanceExcuse.objects.filter(event=event)` query built per event in the
loop, plus `.exists()` and three `.filter(status=...).count()` calls on top
of it — reported live via the dev-mode query monitor as 33x the status-count
shape, 11x the exists shape, and 11x the full select). The fix batches this
into one prefetch plus one grouped conditional aggregate
(`src/tests/guards/test_query_budgets.py::ReviewExcusesQueryBudgetTests`
covers the query-count side of it).

This file proves the rewrite didn't change what officers actually see:
per-event pending/approved/denied counts, the status filter narrowing which
excuses render under each event (while the counts stay the whole-event
breakdown regardless of filter — this was already true of the pre-fix code
and is easy to get wrong when moving the filtering from SQL into Python),
and events with no excuse matching the active filter being hidden.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import AttendanceExcuse, Event, ParliamentUser


def _officer(uid='REC-OFFICER'):
    return ParliamentUser.objects.create_user(
        user_id=uid, password='review-excuses-counts-pass-12345!',
        name='Officer', username=uid.lower().replace('-', '_'),
        member_type='Officer',
    )


def _member(uid, name):
    return ParliamentUser.objects.create_user(
        user_id=uid, password='review-excuses-counts-pass-12345!',
        name=name, username=uid.lower().replace('-', '_'), member_type='Member',
    )


class ReviewExcusesCountsAndFilteringTests(TestCase):
    def setUp(self):
        self.officer = _officer()
        self.alice = _member('REC-M1', 'Alice')
        self.bob = _member('REC-M2', 'Bob')
        self.carol = _member('REC-M3', 'Carol')

        self.event = Event.objects.create(
            title='Mixed Status Event', description='d',
            date_time=timezone.now() + timedelta(days=3),
            created_by=self.officer, requires_attendance=True, allow_excuses=True,
            is_active=True,
        )
        self.pending = AttendanceExcuse.objects.create(
            event=self.event, user=self.alice, reason='a' * 15, status='pending',
        )
        self.approved = AttendanceExcuse.objects.create(
            event=self.event, user=self.bob, reason='b' * 15, status='approved',
        )
        self.denied = AttendanceExcuse.objects.create(
            event=self.event, user=self.carol, reason='c' * 15, status='denied',
        )

        # A second event with only an approved excuse — used to prove the
        # status filter hides events with no matching excuse.
        self.other_event = Event.objects.create(
            title='Only Approved Event', description='d',
            date_time=timezone.now() + timedelta(days=5),
            created_by=self.officer, requires_attendance=True, allow_excuses=True,
            is_active=True,
        )
        AttendanceExcuse.objects.create(
            event=self.other_event, user=self.alice, reason='d' * 15, status='approved',
        )

        self.client.login(username=self.officer.username, password='review-excuses-counts-pass-12345!')

    def _get(self, **params):
        return self.client.get(reverse('review_excuses'), params)

    def test_counts_reflect_the_whole_event_regardless_of_status_filter(self):
        events_by_id = {
            e['event'].id: e for e in self._get(status='approved').context['events_with_excuses']
        }
        counts = events_by_id[self.event.id]
        self.assertEqual(counts['pending_count'], 1)
        self.assertEqual(counts['approved_count'], 1)
        self.assertEqual(counts['denied_count'], 1)
        self.assertEqual(counts['total_count'], 3)

    def test_status_filter_narrows_the_rendered_excuses_only(self):
        response = self._get(status='approved')
        events_by_id = {e['event'].id: e for e in response.context['events_with_excuses']}

        mixed = events_by_id[self.event.id]
        self.assertEqual([e.id for e in mixed['excuses']], [self.approved.id])
        # Counts are unaffected by the filter — still the full breakdown.
        self.assertEqual(mixed['pending_count'], 1)

    def test_event_with_no_excuse_matching_the_filter_is_hidden(self):
        """
        `other_event` only has an approved excuse, so filtering to 'denied'
        must drop it from the page entirely — not show it with an empty
        excuse list.
        """
        response = self._get(status='denied')
        event_ids = {e['event'].id for e in response.context['events_with_excuses']}
        self.assertIn(self.event.id, event_ids)
        self.assertNotIn(self.other_event.id, event_ids)

    def test_all_filter_shows_every_event_with_full_excuse_lists(self):
        response = self._get(status='all')
        events_by_id = {e['event'].id: e for e in response.context['events_with_excuses']}

        self.assertEqual(len(events_by_id[self.event.id]['excuses']), 3)
        self.assertEqual(len(events_by_id[self.other_event.id]['excuses']), 1)

    def test_excuses_are_ordered_newest_submission_first(self):
        """The prefetch orders by `-submitted_at` — same as the pre-fix
        per-event queryset did — so this must still hold after the rewrite."""
        response = self._get(status='all')
        events_by_id = {e['event'].id: e for e in response.context['events_with_excuses']}
        excuse_ids = [e.id for e in events_by_id[self.event.id]['excuses']]
        # Created in order pending, approved, denied — newest (denied) first.
        self.assertEqual(excuse_ids, [self.denied.id, self.approved.id, self.pending.id])
