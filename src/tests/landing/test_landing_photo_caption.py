"""
v3.45.1 (10-09-26) — a landing-page photo's caption is text, not HTML.

`_replace_photo_tags` in src/view/landing.py turns `[photo:ID]` in the three
rich-text sections into a <figure>. Those sections are printed with `|safe`
(they are cleaned with bleach when saved). The caption was put into the
<figcaption> and the `alt="..."` as typed, so it skipped that cleaning: a
caption could add any markup to the public home page, and an ordinary caption
with a quote mark in it cut its own `alt` short.

Run with: python manage.py test src.tests.landing.test_landing_photo_caption
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from src.models import LandingPageContent, LandingPagePhoto


class LandingPhotoCaptionTests(TestCase):
    def page(self, caption):
        cache.clear()
        photo = LandingPagePhoto.objects.create(image='landing_photos/test.jpg', caption=caption)
        content = LandingPageContent.get_instance()
        content.chapter_history_html = f'<p>Before</p>[photo:{photo.pk}]<p>After</p>'
        content.save()
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_markup_in_a_caption_is_shown_as_text(self):
        html = self.page('<form action="/logout/"><button>Click</button></form>')
        self.assertNotIn('<form action="/logout/">', html)
        self.assertIn('&lt;form action=&quot;/logout/&quot;&gt;', html)

    def test_a_quote_mark_does_not_end_the_alt_text(self):
        html = self.page('The "Founders" class & friends')
        self.assertIn('alt="The &quot;Founders&quot; class &amp; friends"', html)
        self.assertIn('<figcaption class="landing-photo-caption">The &quot;Founders&quot; class &amp; friends</figcaption>', html)

    def test_an_ordinary_caption_is_unchanged(self):
        html = self.page('Fall 2024 Pledge Class')
        self.assertIn('<figcaption class="landing-photo-caption">Fall 2024 Pledge Class</figcaption>', html)
        self.assertIn('alt="Fall 2024 Pledge Class"', html)

    def test_no_caption_no_figcaption(self):
        html = self.page('')
        self.assertIn('alt=""', html)
        self.assertNotIn('<figcaption', html)
