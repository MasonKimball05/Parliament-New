"""
09-25-26 — multi-chapter phase 1: `src/chapter.py` + the `{% chapter %}` tag.

Pins: defaults reproduce Alpha Mu exactly (so main's behaviour is unchanged);
overriding settings.CHAPTER changes every surface that was migrated; the tag
works with no request (emails) and refuses unknown fields; values are escaped.

Run with: python manage.py test src.tests.infra.test_chapter_identity
"""
from django.conf import settings
from django.template import Context, Template, TemplateSyntaxError
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings

from src.chapter import get_chapter

OTHER = {**settings.CHAPTER, 'fraternity': 'Sigma Test', 'chapter_name': 'Gamma Delta',
         'school': 'Example State University', 'school_short': 'Example State',
         'site_name': 'Gamma Delta Parliament', 'domain': 'gd.example.org',
         'school_email_domain': 'example.edu', 'motto': 'Test Motto'}


class ChapterIdentityTests(SimpleTestCase):
    def test_defaults_are_alpha_mu(self):
        c = get_chapter()
        self.assertEqual(c.site_name, 'Alpha Mu Parliament')
        self.assertEqual(c.full_name, 'Alpha Mu Chapter of Beta Theta Pi')
        self.assertEqual(c.school_chapter_name, 'Beta Theta Pi - Samford Chapter')
        self.assertEqual(c.formal_name, 'The Samford Chapter, the Alpha Mu of Beta Theta Pi')

    def test_calendar_identifiers_are_unchanged_for_alpha_mu(self):
        # Subscribed calendars key events on UID — changing it duplicates every event.
        c = get_chapter()
        self.assertEqual(c.calendar_uid(42), 'event-42@am-parliament.org')
        self.assertEqual(c.calendar_prodid, '-//Parliament Chapter Calendar//am-parliament.org//')

    def test_tag_works_without_a_request(self):
        out = Template('{% chapter "site_name" %}|{% chapter "crest_url" %}').render(Context({}))
        self.assertEqual(out, 'Alpha Mu Parliament|/static/images/am-coat-of-arms.png')

    def test_unknown_field_is_an_error_not_a_blank(self):
        with self.assertRaises(TemplateSyntaxError):
            Template('{% chapter "chapter_nmae" %}').render(Context({}))

    def test_unknown_settings_key_is_an_error(self):
        with override_settings(CHAPTER={**settings.CHAPTER, 'colour': 'red'}):
            with self.assertRaises(ValueError):
                get_chapter()

    def test_values_are_escaped(self):
        with override_settings(CHAPTER={**settings.CHAPTER, 'chapter_name': '<b>X</b>'}):
            out = Template('{% chapter "chapter_name" %}').render(Context({}))
        self.assertEqual(out, '&lt;b&gt;X&lt;/b&gt;')

    @override_settings(CHAPTER=OTHER, SITE_URL='https://gd.example.org')
    def test_another_chapter_changes_the_migrated_surfaces(self):
        c = get_chapter()
        self.assertEqual(c.full_name, 'Gamma Delta Chapter of Sigma Test')
        self.assertEqual(c.calendar_uid(1), 'event-1@gd.example.org')
        html = render_to_string('emails/event_reminder.html', {'site_url': 'x'})
        self.assertIn('Gamma Delta Parliament', html)
        self.assertNotIn('Alpha Mu', html)


class ChapterConfigSystemCheckTests(SimpleTestCase):
    """09-28-26 — src.E001: a bad settings.CHAPTER is a startup error, not a
    site-wide 500 (including the 500 page) that `manage.py check` misses."""

    def _run(self):
        from src.checks_platform import chapter_config_is_valid
        return chapter_config_is_valid(None)

    def test_default_config_is_clean(self):
        self.assertEqual(self._run(), [])

    def test_lowercase_founding_semester_is_an_error(self):
        from django.conf import settings
        with override_settings(CHAPTER={**settings.CHAPTER, 'founding_semester': 'fall'}):
            errors = self._run()
        self.assertEqual([e.id for e in errors], ['src.E001'])
        self.assertIn('founding_semester', errors[0].msg)

    def test_unknown_key_is_an_error(self):
        from django.conf import settings
        with override_settings(CHAPTER={**settings.CHAPTER, 'chapter_nmae': 'Typo'}):
            errors = self._run()
        self.assertEqual([e.id for e in errors], ['src.E001'])
        self.assertIn('unknown keys', errors[0].msg)
