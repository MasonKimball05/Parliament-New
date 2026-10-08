"""
v3.44.9 (10-08-26) — the chat page finds a message by its text element.

Found while driving the chat page in Chromium for v3.44.9:

  * The Edit and Delete buttons carry the same `data-message-id` as the
    message text and come first in the row. `querySelector('[data-message-id=…]')`
    therefore returned the Edit button. Clicking Edit opened a box holding the
    word "Edit", and saving rewrote the button instead of the message. Since
    06-06-26 (`0e5c23a`, which put the attribute on the buttons).
  * A message already on the page at load had `data-raw="{{ …|escapejs }}"`,
    so even with the right element the edit box would have shown
    `don\\u0027t`. `data-raw` is read back with `dataset`, which wants the
    plain text, HTML-escaped by the template as usual.
  * `document.getElementById('mention-list')` is null for anyone who cannot
    send (read-only channel, admin preview). The unguarded listener threw and
    the initial scroll to the newest message never ran.

Text checks, because the suite has no browser.

Run with: python manage.py test src.tests.guards.test_chat_message_lookup
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CHANNEL = Path(settings.BASE_DIR) / 'templates' / 'chat' / 'channel.html'


class ChatMessageLookupTests(SimpleTestCase):
    def setUp(self):
        self.text = CHANNEL.read_text(encoding='utf-8')

    def test_lookups_name_the_text_element(self):
        bare = re.findall(r'querySelector\(`\[data-message-id=', self.text)
        self.assertEqual(bare, [], 'Use `.message-text[data-message-id="…"]`: the buttons share the attribute.')
        self.assertEqual(len(re.findall(r'querySelector\(`\.message-text\[data-message-id=', self.text)), 4)

    def test_data_raw_is_plain_text(self):
        self.assertNotIn('data-raw="{{ msg.message|escapejs }}"', self.text)
        self.assertIn('data-raw="{{ msg.message }}"', self.text)

    def test_mention_list_listener_is_guarded(self):
        self.assertNotIn("document.getElementById('mention-list').addEventListener", self.text)
