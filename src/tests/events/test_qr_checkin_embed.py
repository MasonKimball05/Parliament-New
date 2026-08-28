"""
v3.27.0 — EventCheckinEmbed: the unauthenticated slide-deck embed link.

qr_checkin_image (the officer-only QR endpoint) can't be pasted into Google
Slides or PowerPoint — those fetch images with no session, so a login-gated
URL just shows a broken image. This is the same shape of problem
CalendarSubscription already solved (an anonymous bearer link, token as the
only credential), applied here.

What matters most to get right: the endpoint must be reachable with NO
login at all (that's the whole point), must show a harmless placeholder
rather than the real QR when no window is open, must 404 on a wrong or
revoked token rather than falling back to "any embed for this event", and
revoking must actually stop the old link from working.
"""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Event, EventCheckinEmbed, EventCheckinWindow, ParliamentUser
from src.models_feature_flags import FeatureFlag


def make_officer(uid='embed-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Embed Officer', username=uid, member_type='Officer',
    )


def make_event(created_by, **kwargs):
    defaults = dict(
        title='Chapter Meeting', description='Weekly meeting',
        date_time=timezone.now() - timedelta(hours=1),
        requires_attendance=True, created_by=created_by,
    )
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def enable_qr_flag():
    FeatureFlag.objects.update_or_create(
        name='qr_attendance_checkin', defaults={'is_enabled': True},
    )


class EventCheckinEmbedModelTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.event = make_event(self.officer)

    def test_get_or_create_for_creates_once(self):
        embed1 = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        embed2 = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        self.assertEqual(embed1.pk, embed2.pk)
        self.assertEqual(embed1.token, embed2.token)

    def test_revoke_then_get_or_create_issues_a_new_token(self):
        embed = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        old_token = embed.token
        embed.revoke()

        renewed = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        self.assertEqual(renewed.pk, embed.pk)  # same row, one per event
        self.assertNotEqual(renewed.token, old_token)
        self.assertTrue(renewed.is_active())

    def test_is_active_false_after_revoke(self):
        embed = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        self.assertTrue(embed.is_active())
        embed.revoke()
        self.assertFalse(embed.is_active())

    def test_tokens_differ_across_events(self):
        other_event = make_event(self.officer, title='Second Event')
        embed1 = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        embed2 = EventCheckinEmbed.get_or_create_for(other_event, created_by=self.officer)
        self.assertNotEqual(embed1.token, embed2.token)


class EmbedImageEndpointTests(TestCase):
    def setUp(self):
        enable_qr_flag()
        self.officer = make_officer()
        self.event = make_event(self.officer)
        self.embed = EventCheckinEmbed.get_or_create_for(self.event, created_by=self.officer)
        self.url = reverse('event_checkin_embed_image', args=[self.event.id, self.embed.token])

    def test_reachable_with_no_login_at_all(self):
        """The entire point of this endpoint — an anonymous Client, no
        force_login anywhere in this test."""
        anon_client = Client()
        response = anon_client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_shows_placeholder_when_no_window_is_open(self):
        anon_client = Client()
        response = anon_client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        self.assertIn(b'not open yet', response.content)

    def test_shows_the_real_qr_when_a_window_is_open(self):
        EventCheckinWindow.open_for(self.event, opened_by=self.officer)
        anon_client = Client()
        response = anon_client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'not open yet', response.content)
        # A real QR render is a much larger SVG (path data) than the fixed
        # placeholder — a cheap, implementation-agnostic way to tell them apart.
        self.assertGreater(len(response.content), 1000)

    def test_reverts_to_placeholder_once_the_window_expires(self):
        window = EventCheckinWindow.open_for(self.event, opened_by=self.officer)
        anon_client = Client()
        self.assertNotIn(b'not open yet', anon_client.get(self.url).content)

        window.expires_at = timezone.now() - timedelta(seconds=1)
        window.save(update_fields=['expires_at'])

        self.assertIn(b'not open yet', anon_client.get(self.url).content)

    def test_wrong_token_404s(self):
        anon_client = Client()
        bad_url = reverse('event_checkin_embed_image', args=[self.event.id, 'not-the-real-token'])
        response = anon_client.get(bad_url)
        self.assertEqual(response.status_code, 404)

    def test_revoked_token_404s(self):
        self.embed.revoke()
        anon_client = Client()
        response = anon_client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_another_events_token_does_not_work_here(self):
        other_event = make_event(self.officer, title='Other Event')
        other_embed = EventCheckinEmbed.get_or_create_for(other_event, created_by=self.officer)
        # Using event A's id with event B's token
        mismatched_url = reverse(
            'event_checkin_embed_image', args=[self.event.id, other_embed.token],
        )
        response = Client().get(mismatched_url)
        self.assertEqual(response.status_code, 404)

    def test_404s_when_feature_flag_is_off(self):
        FeatureFlag.objects.update_or_create(
            name='qr_attendance_checkin', defaults={'is_enabled': False},
        )
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_response_is_never_cached(self):
        response = Client().get(self.url)
        self.assertEqual(response['Cache-Control'], 'no-store')


class OfficerEmbedLinkManagementTests(TestCase):
    def setUp(self):
        enable_qr_flag()
        self.officer = make_officer()
        self.event = make_event(self.officer)
        self.client = Client()
        self.client.force_login(self.officer)

    def test_generate_creates_an_embed_row(self):
        self.assertEqual(EventCheckinEmbed.objects.count(), 0)
        response = self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventCheckinEmbed.objects.filter(event=self.event).count(), 1)

    def test_generate_is_idempotent(self):
        self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        self.assertEqual(EventCheckinEmbed.objects.filter(event=self.event).count(), 1)

    def test_manage_page_shows_the_embed_url_once_generated(self):
        self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        response = self.client.get(reverse('manage_qr_checkin', args=[self.event.id]))
        embed = EventCheckinEmbed.objects.get(event=self.event)
        expected_path = reverse('event_checkin_embed_image', args=[self.event.id, embed.token])
        self.assertContains(response, expected_path)

    def test_revoke_stops_the_old_link_from_working(self):
        self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        embed = EventCheckinEmbed.objects.get(event=self.event)
        old_url = reverse('event_checkin_embed_image', args=[self.event.id, embed.token])

        self.client.post(reverse('revoke_qr_embed_link', args=[self.event.id]))

        response = Client().get(old_url)  # anonymous, like a real slide fetch
        self.assertEqual(response.status_code, 404)

    def test_non_officer_cannot_generate_a_link(self):
        member = ParliamentUser.objects.create_user(
            user_id='embed-member', name='Embed Member', username='embed-member',
            member_type='Member',
        )
        self.client.force_login(member)
        response = self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(EventCheckinEmbed.objects.count(), 0)

    def test_non_officer_cannot_revoke_a_link(self):
        self.client.post(reverse('generate_qr_embed_link', args=[self.event.id]))
        member = ParliamentUser.objects.create_user(
            user_id='embed-member2', name='Embed Member 2', username='embed-member2',
            member_type='Member',
        )
        self.client.force_login(member)
        response = self.client.post(reverse('revoke_qr_embed_link', args=[self.event.id]))
        self.assertEqual(response.status_code, 403)
        embed = EventCheckinEmbed.objects.get(event=self.event)
        self.assertTrue(embed.is_active())
