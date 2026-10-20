"""
10-20-26 — `word_diff` never emits an empty <del> or <ins>.

`_TOKEN.split('')` is [''], so `word_diff(None, 'new')` used to render
`<del ...></del><ins ...>new</ins>`. Harmless on screen, but untidy markup.

Run with: python manage.py test src.tests.legislation.test_cnb_word_diff
"""
from django.test import SimpleTestCase

from src.view.cnb_history import word_diff


class WordDiffTests(SimpleTestCase):
    def test_from_nothing_is_a_lone_insert(self):
        out = word_diff(None, 'new')
        self.assertNotIn('<del', out)
        self.assertIn('>new</ins>', out)

    def test_to_nothing_is_a_lone_delete(self):
        out = word_diff('old', '')
        self.assertNotIn('<ins', out)
        self.assertIn('>old</del>', out)

    def test_no_empty_segments_anywhere(self):
        for old, new in [(None, 'new'), ('old', None), ('', 'a b'), (' a', 'a'), ('a', 'a '), ('a b', 'a c')]:
            out = word_diff(old, new)
            self.assertNotRegex(out, r'<del[^>]*></del>', (old, new))
            self.assertNotRegex(out, r'<ins[^>]*></ins>', (old, new))

    def test_a_word_swap_still_shows_both_sides_escaped(self):
        out = word_diff('a <b>', 'a <i>')
        self.assertIn('&lt;b&gt;</del>', out)
        self.assertIn('&lt;i&gt;</ins>', out)
