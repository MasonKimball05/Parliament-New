"""Mobile plan, Phase 1 — push URL → app screen mapping (src/mobile/deeplinks.py)."""
from django.test import TestCase

from src.mobile.deeplinks import push_data_for_url, screen_for_url
from src.models import ChatChannel, Committee


class DeepLinkTests(TestCase):
    def test_static_screens(self):
        cases = {
            '/vote/': ('votes', None),
            '/calendar/': ('calendar', None),
            '/calendar/?year=2026&month=11': ('calendar', None),
            '/notifications/': ('notifications', None),
            '/announcements/': ('announcements', None),
            '/directory/': ('directory', None),
            '/my-attendance/': ('attendance', None),
            '/service-hours/': ('service_hours', None),
            '/chapter-documents/': ('documents', None),
            '/excuses/': ('excuses', None),
            '/polls/': ('polls', None),
            '/announcements/7/poll/': ('poll', 7),
            '/announcements/7/poll/thanks/': ('web', None),
            '/home/': ('home', None),
            '/legislation/detail/12/': ('vote', 12),
            '/legislation/12/': ('vote', 12),
            '/chat/5/': ('chat', 5),
            '/chats/': ('chat', None),             # the channel list
            '/chats/?tab=committees': ('chat', None),
            '/chats/create/': ('web', None),
            '/chats/5/edit/': ('web', None),
            '/calendar/?year=2026&month=11#event-4': ('calendar', None),
            '/calendar/event/4/signups/': ('web', None),
            '/calendar/export/': ('web', None),
            '/notifications/?page=2': ('notifications', None),
            '/notifications/#unread': ('notifications', None),
            '/excuses/submit/4/': ('web', None),
            '/preferences/#api-tokens': ('web', None),
            '/legislation/detail/12/document/': ('web', None),
            '': ('home', None),
            None: ('home', None),
        }
        for url, expected in cases.items():
            self.assertEqual(screen_for_url(url), expected, url)

    def test_committee_chat_urls_resolve_to_channel_id(self):
        committee = Committee.objects.create(name='Finance', code='FIN')
        channel = ChatChannel.objects.create(
            name='Finance', channel_type='committee', access_type='committee',
            committee=committee)
        self.assertEqual(screen_for_url('/chat/committee/FIN/'), ('chat', channel.id))
        self.assertEqual(screen_for_url('/committee/FIN/chat/'), ('chat', channel.id))
        self.assertEqual(screen_for_url('/committee/NOPE/chat/'), ('web', None))

    def test_push_data_shape(self):
        self.assertEqual(push_data_for_url('/legislation/detail/3/'),
                         {'url': '/legislation/detail/3/', 'screen': 'vote', 'id': 3})
        self.assertEqual(push_data_for_url(''), {'url': '/home/', 'screen': 'home', 'id': None})
        # The query string is kept in `url` (the app reads the month from it).
        self.assertEqual(push_data_for_url('/calendar/?year=2026&month=11'),
                         {'url': '/calendar/?year=2026&month=11', 'screen': 'calendar', 'id': None})
        self.assertEqual(push_data_for_url('/chats/'), {'url': '/chats/', 'screen': 'chat', 'id': None})
