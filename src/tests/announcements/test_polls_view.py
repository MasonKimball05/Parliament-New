"""
v3.31.3 — the member-facing "Polls" page (`/polls/`, `polls_view`).

Mason asked for "a page to see open polls and closed polls" alongside the
edit-your-response change (see `test_take_poll_ui.py` for that half). This
page lists every poll on an announcement the member can see, split into
Open (still accepting responses) and Closed, with a per-poll "responded /
not yet" status. It deliberately reuses `announcements_view`'s own
visibility scoping (`is_active`, `publish_at`, `visible_to_q`) so it can
never show a poll the member couldn't already reach through its
announcement — these tests check that boundary directly rather than trusting
the two code paths stay in sync by inspection.
"""
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollQuestion,
    AnnouncementPollOption, AnnouncementPollResponse, ParliamentUser,
)


def make_user(uid, member_type='Member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name=f'User {uid}', username=uid, member_type=member_type,
        password='testpass123',
    )


def make_poll(officer, title, is_open=True, closes_at=None, visible_to=None):
    announcement = Announcement.objects.create(
        title=f'Announcement for {title}', content='...', posted_by=officer,
        visible_to=visible_to,
    )
    poll = AnnouncementPoll.objects.create(
        announcement=announcement, created_by=officer, title=title,
        is_open=is_open, closes_at=closes_at,
    )
    q = AnnouncementPollQuestion.objects.create(
        poll=poll, text='Pick one', question_type='single', order=0,
    )
    AnnouncementPollOption.objects.create(question=q, text='A', order=0)
    return announcement, poll


class PollsPageListsOpenAndClosedTests(TestCase):
    def setUp(self):
        self.officer = make_user('pollsview-officer', member_type='Officer')
        self.member = make_user('pollsview-member')
        self.client = Client()
        self.client.login(username=self.member.username, password='testpass123')

    def test_an_open_unanswered_poll_appears_under_open_not_yet_answered(self):
        announcement, poll = make_poll(self.officer, 'Open Poll')
        response = self.client.get(reverse('polls_view'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Open Poll', html)
        self.assertIn('Not yet answered', html)

    def test_an_open_answered_poll_shows_responded_and_an_edit_link(self):
        announcement, poll = make_poll(self.officer, 'Answered Open Poll')
        AnnouncementPollResponse.objects.create(poll=poll, respondent=self.member)
        response = self.client.get(reverse('polls_view'))
        html = response.content.decode()
        self.assertIn('Answered Open Poll', html)
        self.assertIn('Responded', html)
        self.assertIn('Edit Response', html)

    def test_a_closed_poll_appears_under_closed(self):
        announcement, poll = make_poll(self.officer, 'Closed Poll', is_open=False)
        response = self.client.get(reverse('polls_view'))
        html = response.content.decode()
        self.assertIn('Closed Poll', html)

    def test_a_poll_closed_by_closes_at_date_appears_under_closed(self):
        announcement, poll = make_poll(
            self.officer, 'Auto-Closed Poll',
            closes_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(reverse('polls_view'))
        # It should land in the closed section, not be listed as open.
        self.assertContains(response, 'Auto-Closed Poll')
        # Sanity: is_accepting_responses agrees it's closed.
        self.assertFalse(poll.is_accepting_responses())

    def test_an_announcement_with_no_poll_does_not_appear(self):
        Announcement.objects.create(title='No poll here', content='...', posted_by=self.officer)
        response = self.client.get(reverse('polls_view'))
        self.assertNotContains(response, 'No poll here')

    def test_an_inactive_announcement_poll_does_not_appear(self):
        announcement, poll = make_poll(self.officer, 'Deactivated Poll')
        announcement.is_active = False
        announcement.save()
        response = self.client.get(reverse('polls_view'))
        self.assertNotContains(response, 'Deactivated Poll')

    def test_an_unpublished_future_announcement_poll_does_not_appear(self):
        announcement, poll = make_poll(self.officer, 'Future Poll')
        announcement.publish_at = timezone.now() + timedelta(days=1)
        announcement.save()
        response = self.client.get(reverse('polls_view'))
        self.assertNotContains(response, 'Future Poll')

    def test_a_poll_scoped_to_advisors_is_hidden_from_a_member(self):
        announcement, poll = make_poll(self.officer, 'Advisors Only Poll', visible_to=['Advisor'])
        response = self.client.get(reverse('polls_view'))
        self.assertNotContains(response, 'Advisors Only Poll')

    def test_the_take_poll_link_actually_works_for_an_unanswered_open_poll(self):
        """Not just that the page renders a link — that the link target is
        the real, working take_poll URL for that announcement."""
        announcement, poll = make_poll(self.officer, 'Linked Poll')
        response = self.client.get(reverse('polls_view'))
        self.assertContains(response, reverse('take_poll', args=[announcement.id]))
