"""
v4.4.0 — chat safety for App Store guideline 1.2 (src/chat_safety.py): report
a message, mute a member, moderators act on reports. The web side; the app's
endpoints are src/tests/mobile/test_chat_safety.py.

    python manage.py test src.tests.chat.test_chat_safety
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src import chat_safety, chat_service
from src.context_processors import unread_chat_count
from src.models import (
    ChatChannel, ChatMessage, ChatMessageReport, ChatReadReceipt, ChatUserMute, Notification,
    ParliamentUser,
)


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid.replace('-', ''),
                  member_type='Member', member_status='Active')
    fields.update(extra)
    return ParliamentUser.objects.create(**fields)


class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.alice = make_user('cs-alice')
        self.bob = make_user('cs-bob')
        self.carol = make_user('cs-carol')
        self.dave = make_user('cs-dave')
        self.officer = make_user('cs-officer', member_type='Officer')
        self.channel = ChatChannel.objects.create(name='General', access_type='open')

    def msg(self, sender, text='hi'):
        return ChatMessage.objects.create(channel=self.channel, sender=sender, message=text)

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c

    def report(self, user, message, reason='harassment', details=''):
        return self.web(user).post(reverse('chat_report_message', args=[message.id]),
                                   {'reason': reason, 'details': details})


class ReportTests(_Base):

    def test_report_notifies_moderators_but_not_the_reported_member(self):
        bad = self.msg(self.bob, 'nasty')
        with mock.patch('src.tasks.send_push_notification') as push:
            r = self.report(self.alice, bad, details='not ok')
        self.assertEqual(r.status_code, 200, r.content)
        report = ChatMessageReport.objects.get()
        self.assertEqual((report.reporter, report.reason, report.details, report.status),
                         (self.alice, 'harassment', 'not ok', 'open'))
        note = Notification.objects.get(notification_type='chat_report')
        self.assertEqual(note.recipient, self.officer)
        self.assertEqual(note.link, reverse('chat_reports'))
        # Nothing names the reporter, and the lock screen shows no text.
        self.assertNotIn(self.alice.name, note.title + note.message)
        bodies = [c.kwargs['body'] for c in push.delay.call_args_list]
        self.assertTrue(bodies)
        self.assertFalse(any('nasty' in b for b in bodies))
        self.assertFalse(Notification.objects.filter(recipient=self.bob).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.alice).exists())

    def test_duplicate_report_is_refused(self):
        bad = self.msg(self.bob)
        self.report(self.alice, bad)
        r = self.report(self.alice, bad)
        self.assertEqual((r.status_code, r.json()['code']), (409, 'already_reported'))
        self.assertEqual(ChatMessageReport.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatMessageReport.objects.create(message=bad, reporter=self.alice, reason='spam')

    def test_own_message_and_bad_reason_refused(self):
        mine = self.msg(self.alice)
        self.assertEqual(self.report(self.alice, mine).status_code, 400)
        theirs = self.msg(self.bob)
        r = self.report(self.alice, theirs, reason='because')
        self.assertEqual((r.status_code, r.json()['code']), (400, 'invalid_input'))
        self.assertFalse(ChatMessageReport.objects.exists())

    def test_unreadable_channel_is_404(self):
        private = ChatChannel.objects.create(name='Officers', access_type='restricted')
        secret = ChatMessage.objects.create(channel=private, sender=self.bob, message='x')
        self.assertEqual(self.report(self.alice, secret).status_code, 404)

    def test_get_is_refused(self):
        bad = self.msg(self.bob)
        r = self.web(self.alice).get(reverse('chat_report_message', args=[bad.id]))
        self.assertEqual(r.status_code, 405)

    def test_three_distinct_reports_auto_hide(self):
        self.assertEqual(chat_safety.AUTO_HIDE_THRESHOLD, 3)
        bad = self.msg(self.bob, 'awful')
        self.report(self.alice, bad)
        self.report(self.carol, bad)
        bad.refresh_from_db()
        self.assertFalse(bad.hidden_pending_review)
        self.report(self.dave, bad)
        bad.refresh_from_db()
        self.assertTrue(bad.hidden_pending_review)
        # Gone from everyone's view and unread counts, the sender's included.
        for viewer in (self.officer, self.bob):
            self.assertNotIn(bad.id, [m.id for m in chat_service.messages_qs(self.channel, viewer)])
        self.assertEqual(ChatChannel.unread_map([self.channel], self.officer)[self.channel.pk], 0)
        r = self.web(self.officer).get(reverse('channel_chat', args=[self.channel.id]))
        self.assertNotContains(r, 'awful')
        # Moderators heard about the first report and about the hide.
        titles = list(Notification.objects.filter(recipient=self.officer).values_list('title', flat=True))
        self.assertEqual(len(titles), 2)
        self.assertTrue(any('hidden' in t for t in titles))


class ReviewTests(_Base):

    def setUp(self):
        super().setUp()
        self.bad = self.msg(self.bob, 'awful')
        for u in (self.alice, self.carol, self.dave):
            self.report(u, self.bad)
        self.first = ChatMessageReport.objects.get(reporter=self.alice)

    def review(self, user, action, report=None):
        return self.web(user).post(reverse('chat_review_report', args=[(report or self.first).id]),
                                   {'action': action, 'note': 'looked'})

    def test_moderator_page_shows_reporter(self):
        r = self.web(self.officer).get(reverse('chat_reports'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.alice.name)
        self.assertContains(r, 'awful')

    def test_non_moderator_cannot_review(self):
        for user in (self.alice, self.bob):
            self.assertEqual(self.web(user).get(reverse('chat_reports')).status_code, 403)
            self.assertEqual(self.review(user, 'dismiss').status_code, 403)
        self.assertEqual(ChatMessageReport.objects.filter(status='open').count(), 3)

    def test_reported_member_never_sees_reporter(self):
        # Even a moderator, about their own message.
        self.bob.member_type = 'Officer'
        self.bob.save()
        r = self.web(self.bob).get(reverse('chat_reports'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, self.alice.name)
        self.review(self.bob, 'dismiss')
        self.assertEqual(ChatMessageReport.objects.filter(status='open').count(), 3)
        self.assertEqual(chat_safety.open_report_count(self.bob), 0)

    def test_dismiss_restores_and_closes_all_reports(self):
        self.review(self.officer, 'dismiss')
        self.bad.refresh_from_db()
        self.assertFalse(self.bad.hidden_pending_review)
        self.assertFalse(self.bad.is_deleted)
        self.assertEqual(set(ChatMessageReport.objects.values_list('status', flat=True)), {'dismissed'})
        report = ChatMessageReport.objects.get(pk=self.first.pk)
        self.assertEqual((report.reviewed_by, report.action_note), (self.officer, 'looked'))
        self.assertIsNotNone(report.reviewed_at)
        # A second review is refused.
        with self.assertRaises(chat_service.ChatError):
            chat_safety.review_report(report, self.officer, 'dismiss')

    def test_delete_uses_the_delete_path(self):
        with mock.patch('src.chat_service.ws_broadcast') as ws:
            self.review(self.officer, 'delete_message')
        self.bad.refresh_from_db()
        self.assertTrue(self.bad.is_deleted)
        ws.assert_called_with(f'chat_{self.channel.id}', {'type': 'chat.delete', 'message_id': self.bad.id})
        self.assertEqual(set(ChatMessageReport.objects.values_list('status', flat=True)), {'actioned'})

    def test_officer_who_is_not_admin_or_chair_can_delete_via_review_only(self):
        with self.assertRaises(chat_service.ChatError):
            chat_service.delete_message(self.channel, self.bad, self.officer)
        chat_service.delete_message(self.channel, self.bad, self.officer, as_moderator=True)
        with self.assertRaises(chat_service.ChatError):
            chat_service.delete_message(self.channel, self.msg(self.bob), self.alice, as_moderator=True)


class MuteTests(_Base):

    def mute(self, muter, muted):
        return self.web(muter).post(reverse('chat_mute_member', args=[muted.user_id]))

    def test_mute_hides_messages_for_the_muter_only(self):
        self.msg(self.bob, 'from bob')
        self.msg(self.carol, 'from carol')
        r = self.mute(self.alice, self.bob)
        self.assertEqual(r.status_code, 200, r.content)
        page = self.web(self.alice).get(reverse('channel_chat', args=[self.channel.id]))
        self.assertNotContains(page, 'from bob')
        self.assertContains(page, 'from carol')
        poll = self.web(self.alice).get(reverse('get_channel_messages', args=[self.channel.id]))
        self.assertEqual([m['message'] for m in poll.json()['messages']], ['from carol'])
        # Nothing changes for anyone else, Bob included; nobody left the channel.
        page = self.web(self.dave).get(reverse('channel_chat', args=[self.channel.id]))
        self.assertContains(page, 'from bob')
        self.assertTrue(self.channel.has_access(self.bob))
        self.assertTrue(self.channel.can_write(self.bob))

    def test_mute_hides_unread_counts(self):
        ChatReadReceipt.objects.create(user=self.alice, channel=self.channel)
        ChatReadReceipt.objects.create(user=self.dave, channel=self.channel)
        self.msg(self.bob)
        self.msg(self.bob)
        self.msg(self.carol)
        self.mute(self.alice, self.bob)
        self.assertEqual(ChatChannel.unread_map([self.channel], self.alice)[self.channel.pk], 1)
        self.assertEqual(unread_chat_count(self.alice), 1)
        self.assertEqual(unread_chat_count(self.dave), 3)

    def test_mute_stops_push_and_mention_notifications_to_the_muter(self):
        alice = make_user('csmutealice')
        dave = make_user('csmutedave')
        for u in (alice, dave):
            ChatReadReceipt.objects.create(user=u, channel=self.channel)
        ChatReadReceipt.objects.update(last_read_at=timezone.now() - timedelta(minutes=5))
        chat_safety.mute(alice, self.bob)
        with mock.patch('src.tasks.send_push_notification') as push:
            chat_service.send_message(self.channel, self.bob, '@csmutealice @csmutedave hello')
        pushed = {c.kwargs['user_id'] for c in push.delay.call_args_list}
        self.assertIn(dave.pk, pushed)
        self.assertNotIn(alice.pk, pushed)
        self.assertFalse(Notification.objects.filter(recipient=alice).exists())
        self.assertTrue(Notification.objects.filter(recipient=dave, notification_type='chat_mention').exists())

    def test_self_mute_refused(self):
        r = self.mute(self.alice, self.alice)
        self.assertEqual((r.status_code, r.json()['code']), (400, 'self_mute'))
        self.assertFalse(ChatUserMute.objects.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChatUserMute.objects.create(muter=self.alice, muted=self.alice)

    def test_mute_is_idempotent_and_unknown_member_404(self):
        self.mute(self.alice, self.bob)
        self.mute(self.alice, self.bob)
        self.assertEqual(ChatUserMute.objects.count(), 1)
        r = self.web(self.alice).post(reverse('chat_mute_member', args=['P-NOBODY']))
        self.assertEqual(r.status_code, 404)

    def test_muted_list_and_unmute(self):
        self.msg(self.bob, 'from bob')
        self.mute(self.alice, self.bob)
        client = self.web(self.alice)
        page = client.get(reverse('chat_muted_members'))
        self.assertContains(page, self.bob.name)
        self.assertEqual(client.get(reverse('chat_unmute_member', args=[self.bob.user_id])).status_code, 405)
        r = client.post(reverse('chat_unmute_member', args=[self.bob.user_id]))
        self.assertRedirects(r, reverse('chat_muted_members'), fetch_redirect_response=False)
        self.assertFalse(ChatUserMute.objects.exists())
        page = client.get(reverse('channel_chat', args=[self.channel.id]))
        self.assertContains(page, 'from bob')

    def test_socket_skips_muted_sender(self):
        from asgiref.sync import async_to_sync
        from src.consumers import ChatConsumer
        chat_safety.mute(self.alice, self.bob)
        chat_safety.muted_ids(self.alice)  # primes the cache the consumer reads

        def deliver(user, event, handler='chat_message'):
            consumer = ChatConsumer()
            consumer.scope = {'user': user}
            consumer.send = mock.AsyncMock()
            # database_sync_to_async closes "old" connections — in a TestCase
            # that is the test's own transaction.
            with mock.patch('channels.db.close_old_connections'):
                async_to_sync(getattr(consumer, handler))(event)
            return consumer.send.await_count

        from_bob = {'message': {'id': 1, 'sender_id': self.bob.user_id, 'message': 'x'}}
        self.assertEqual(deliver(self.alice, from_bob), 0)
        self.assertEqual(deliver(self.alice, {'user_id': self.bob.pk, 'name': 'Bob'}, 'chat_typing'), 0)
        self.assertEqual(deliver(self.alice, {'message': {'id': 2, 'sender_id': self.carol.user_id}}), 1)
        chat_safety.unmute(self.alice, self.bob.pk)
        chat_safety.muted_ids(self.alice)
        self.assertEqual(deliver(self.alice, from_bob), 1)

    def test_links_to_the_pages(self):
        r = self.web(self.alice).get(reverse('chat_index'))
        self.assertContains(r, reverse('chat_muted_members'))
        self.assertNotContains(r, reverse('chat_reports'))
        r = self.web(self.officer).get(reverse('chat_index'))
        self.assertContains(r, reverse('chat_reports'))


class ConfidentialityTests(_Base):
    """A reviewer must be able to READ the channel; Kai-like channels go to their chairs."""

    def setUp(self):
        super().setUp()
        from src.models import Committee
        self.chair = make_user('cs-chair')
        self.kai = Committee.objects.create(name='Kai', code='CSKAI', is_active=True)
        self.kai.members.add(self.alice, self.bob, self.chair)
        self.kai.chairs.add(self.chair)
        self.kai_chat = ChatChannel.objects.create(name='Kai', channel_type='committee',
                                                   access_type='committee', committee=self.kai)
        self.secret = ChatMessage.objects.create(channel=self.kai_chat, sender=self.bob, message='kai secret')
        self.report(self.alice, self.secret)
        self.kai_report = ChatMessageReport.objects.get(message=self.secret)

    def test_officer_outside_the_channel_sees_and_does_nothing(self):
        self.assertFalse(Notification.objects.filter(recipient=self.officer).exists())
        page = self.web(self.officer).get(reverse('chat_reports'))
        self.assertNotContains(page, 'kai secret')
        self.assertNotContains(page, self.alice.name)
        self.assertEqual(chat_safety.open_report_count(self.officer), 0)
        r = self.web(self.officer).post(reverse('chat_review_report', args=[self.kai_report.id]),
                                        {'action': 'delete_message'})
        self.assertEqual(r.status_code, 403)
        self.secret.refresh_from_db()
        self.assertFalse(self.secret.is_deleted)
        with self.assertRaises(chat_service.ChatError):
            chat_service.delete_message(self.kai_chat, self.secret, self.officer, as_moderator=True)
        # An admin outside the committee is no different.
        admin = make_user('cs-admin', is_admin=True)
        self.assertEqual(chat_safety.open_report_count(admin), 0)
        self.assertEqual(self.web(admin).post(reverse('chat_review_report', args=[self.kai_report.id]),
                                              {'action': 'dismiss'}).status_code, 403)

    def test_the_committee_chair_reviews_instead(self):
        self.assertTrue(Notification.objects.filter(recipient=self.chair, notification_type='chat_report').exists())
        self.assertTrue(chat_safety.can_moderate_chat(self.chair))
        page = self.web(self.chair).get(reverse('chat_reports'))
        self.assertContains(page, 'kai secret')
        self.assertContains(page, self.alice.name)
        self.web(self.chair).post(reverse('chat_review_report', args=[self.kai_report.id]),
                                  {'action': 'delete_message'})
        self.secret.refresh_from_db()
        self.assertTrue(self.secret.is_deleted)
        # The chair reviews nothing in channels an officer can read.
        open_report = ChatMessageReport.objects.create(message=self.msg(self.bob), reporter=self.alice,
                                                       reason='spam')
        self.assertEqual(self.web(self.chair).post(reverse('chat_review_report', args=[open_report.id]),
                                                   {'action': 'dismiss'}).status_code, 403)

    def test_an_officer_on_the_committee_takes_over(self):
        self.kai.members.add(self.officer)
        self.assertEqual([u.pk for u in chat_safety.channel_reviewers(self.kai_chat)], [self.officer.pk])
        self.assertEqual(chat_safety.open_report_count(self.officer), 1)
        self.assertEqual(chat_safety.open_report_count(self.chair), 0)
