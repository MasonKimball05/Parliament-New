"""
v3.28.7 — AnnouncementPollEmbed: a QR code for a poll, embeddable in a
slide deck the same way v3.27.0 already did for event check-in.

Mirrors src/tests/events/test_qr_checkin_embed.py's structure and reasoning
almost exactly — same shape of problem (an unauthenticated bearer link so a
slideshow with no session can still fetch the image), same things worth
checking: reachable with NO login, a harmless placeholder rather than the
real QR when the poll can't be answered right now, 404 on a wrong or
revoked token, and revoking actually stopping the old link.

The one real difference from the event version: a poll has no rotating,
time-boxed token to protect, so `poll_qr_image` (the officer-only view)
never 404s for "nothing open right now" the way `qr_checkin_image` does —
the QR always encodes the same `take_poll` URL. What DOES vary is whether
scanning it currently does anything useful, which is what the embed
endpoint's placeholder-vs-live branch is actually gated on.
"""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollEmbed, ParliamentUser,
)


def make_officer(uid='poll-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Poll Officer', username=uid, member_type='Officer',
        password='testpass123',
    )


def make_member(uid='poll-member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Poll Member', username=uid, member_type='Member',
        password='testpass123',
    )


def make_published_poll(officer, **poll_kwargs):
    announcement = Announcement.objects.create(
        title='Chapter vote', content='...', posted_by=officer,
    )
    defaults = dict(title='A poll', is_open=True)
    defaults.update(poll_kwargs)
    poll = AnnouncementPoll.objects.create(announcement=announcement, created_by=officer, **defaults)
    return announcement, poll


class AnnouncementPollEmbedModelTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.announcement, self.poll = make_published_poll(self.officer)

    def test_get_or_create_for_creates_once(self):
        embed1 = AnnouncementPollEmbed.get_or_create_for(self.poll, created_by=self.officer)
        embed2 = AnnouncementPollEmbed.get_or_create_for(self.poll, created_by=self.officer)
        self.assertEqual(embed1.pk, embed2.pk)
        self.assertEqual(embed1.token, embed2.token)

    def test_revoke_then_get_or_create_issues_a_new_token(self):
        embed = AnnouncementPollEmbed.get_or_create_for(self.poll, created_by=self.officer)
        old_token = embed.token
        embed.revoke()

        renewed = AnnouncementPollEmbed.get_or_create_for(self.poll, created_by=self.officer)
        self.assertEqual(renewed.pk, embed.pk)  # same row, one per poll
        self.assertNotEqual(renewed.token, old_token)
        self.assertTrue(renewed.is_active())

    def test_is_active_false_after_revoke(self):
        embed = AnnouncementPollEmbed.get_or_create_for(self.poll, created_by=self.officer)
        self.assertTrue(embed.is_active())
        embed.revoke()
        self.assertFalse(embed.is_active())


class ManagePollQrViewTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.member = make_member()
        self.announcement, self.poll = make_published_poll(self.officer)
        self.client = Client()

    def test_officer_can_view_manage_page(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('manage_poll_qr', args=[self.announcement.id]))
        self.assertEqual(response.status_code, 200)

    def test_regular_member_is_blocked(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse('manage_poll_qr', args=[self.announcement.id]))
        self.assertNotEqual(response.status_code, 200)

    def test_generate_then_revoke_embed_link(self):
        self.client.force_login(self.officer)
        url = reverse('manage_poll_qr', args=[self.announcement.id])

        self.client.post(reverse('generate_poll_qr_embed_link', args=[self.announcement.id]))
        response = self.client.get(url)
        self.assertContains(response, 'Revoke this link')
        self.assertTrue(AnnouncementPollEmbed.objects.filter(poll=self.poll, revoked_at__isnull=True).exists())

        self.client.post(reverse('revoke_poll_qr_embed_link', args=[self.announcement.id]))
        response = self.client.get(url)
        self.assertContains(response, 'Get embed link')
        self.assertFalse(AnnouncementPollEmbed.objects.filter(poll=self.poll, revoked_at__isnull=True).exists())


class PollQrImageViewTests(TestCase):
    """The officer-only, login-gated SVG — always live, no window concept."""

    def setUp(self):
        self.officer = make_officer()
        self.member = make_member()
        self.announcement, self.poll = make_published_poll(self.officer)
        self.client = Client()

    def test_officer_gets_an_svg(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('poll_qr_image', args=[self.announcement.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')

    def test_it_is_live_even_when_the_poll_is_closed(self):
        """
        ⚠️ THE ONE REAL DIFFERENCE FROM THE EVENT VERSION. qr_checkin_image
        404s when no window is open, because there's genuinely nothing
        valid to encode. A poll has no equivalent: the officer-only view
        exists to preview/download the code regardless of whether it's
        currently useful to scan, so it must never 404 just because the
        poll happens to be closed right now.
        """
        self.poll.is_open = False
        self.poll.save(update_fields=['is_open'])
        self.client.force_login(self.officer)
        response = self.client.get(reverse('poll_qr_image', args=[self.announcement.id]))
        self.assertEqual(response.status_code, 200)

    def test_regular_member_is_blocked(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse('poll_qr_image', args=[self.announcement.id]))
        self.assertNotEqual(response.status_code, 200)

    def test_no_login_is_blocked(self):
        response = self.client.get(reverse('poll_qr_image', args=[self.announcement.id]))
        self.assertNotEqual(response.status_code, 200)


class PollQrEmbedImageViewTests(TestCase):
    """
    The public, unauthenticated embed endpoint. Reachable with NO login (the
    whole point), 404 on a wrong/revoked token, and a placeholder rather
    than the real QR whenever the poll can't actually be answered right now.
    """

    def setUp(self):
        self.officer = make_officer()
        self.announcement, self.poll = make_published_poll(self.officer)
        self.embed = AnnouncementPollEmbed.get_or_create_for(self.poll, created_by=self.officer)
        self.client = Client()  # deliberately anonymous throughout this class

    def _get(self):
        return self.client.get(
            reverse('poll_qr_embed_image', args=[self.announcement.id, self.embed.token])
        )

    def test_no_login_required(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')

    def test_live_qr_when_the_poll_is_open(self):
        response = self._get()
        self.assertIn(b'<svg', response.content)
        # A live code, not the placeholder — the placeholder's own copy is
        # the cheapest reliable signal without parsing QR pixel data.
        self.assertNotIn(b'not open', response.content)

    def test_placeholder_when_the_poll_is_closed(self):
        self.poll.is_open = False
        self.poll.save(update_fields=['is_open'])
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'not open', response.content)

    def test_placeholder_when_the_announcement_is_not_yet_published(self):
        self.announcement.publish_at = timezone.now() + timedelta(days=1)
        self.announcement.save(update_fields=['publish_at'])
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'not open', response.content)

    def test_wrong_token_404s(self):
        response = self.client.get(
            reverse('poll_qr_embed_image', args=[self.announcement.id, 'not-a-real-token'])
        )
        self.assertEqual(response.status_code, 404)

    def test_revoked_token_404s(self):
        self.embed.revoke()
        response = self._get()
        self.assertEqual(response.status_code, 404)

    def test_no_caching(self):
        """
        Same reasoning as event_checkin_embed_image: the same URL has to
        show something different (placeholder vs. live) minute to minute as
        the poll opens and closes, so nothing in the chain may cache it.
        """
        response = self._get()
        self.assertEqual(response['Cache-Control'], 'no-store')
