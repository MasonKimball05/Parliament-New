"""
`/officers/excuses/` lists events soonest-first.

v3.29.3 — Mason reported the event grouping on this page as counterintuitive:
it sorted `-date_time` (descending), so the event furthest in the future sat
at the top of the review queue and the event about to happen (the one most
urgent to act on) could be buried below it. Flipped to ascending `date_time`
so the soonest event — whether a few days out or already slightly past —
leads the list.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import AttendanceExcuse, Event, ParliamentUser


def _member(user_id, name, member_type='Officer'):
    return ParliamentUser.objects.create_user(
        user_id=user_id, password='review-excuses-order-pass-12345!',
        name=name, username=user_id.lower().replace('-', '_'),
        member_type=member_type,
    )


def _event(creator, days_offset, title):
    return Event.objects.create(
        title=title, description='x',
        date_time=timezone.now() + timedelta(days=days_offset),
        created_by=creator, requires_attendance=True, allow_excuses=True,
        is_active=True,
    )


class ReviewExcusesOrderingTests(TestCase):

    def setUp(self):
        self.officer = _member('REO-OFF1', 'Officer One')
        self.member = _member('REO-MEM1', 'Member One', member_type='Member')

        self.far_event = _event(self.officer, days_offset=20, title='Far Out Event')
        self.soon_event = _event(self.officer, days_offset=1, title='Soon Event')

        AttendanceExcuse.objects.create(
            event=self.far_event, user=self.member, reason='x' * 15,
        )
        AttendanceExcuse.objects.create(
            event=self.soon_event, user=self.member, reason='y' * 15,
        )

        self.client.login(username=self.officer.username, password='review-excuses-order-pass-12345!')

    def test_soonest_event_appears_before_farther_out_event(self):
        resp = self.client.get(reverse('review_excuses'))
        body = resp.content.decode()

        soon_pos = body.index('Soon Event')
        far_pos = body.index('Far Out Event')

        self.assertLess(
            soon_pos, far_pos,
            'The event happening sooner should be listed before the one further out.'
        )
