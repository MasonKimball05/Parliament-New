"""
09-27-26 — multi-chapter phase 2, slice 2c: landing-page identity comes from
the chapter config, and the landing FALLBACK text (shown while the editor's
fields are empty) is per-chapter content.

Mechanism: settings.TEMPLATES DIRS is [CHAPTER_CONTENT_DIR/templates,
templates/], so chapter_content/<chapter>/templates/landing/_default_*.html
overrides the generic templates/landing/_default_*.html.

Pins: the original chapter still sees its own text verbatim; a chapter with no
override gets the generic text in its own name, with no trace of the original
chapter; the landing editor's reset-to-default presets are valid JS strings.

Run with: python manage.py test src.tests.landing.test_chapter_landing_content
"""
import copy

from django.conf import settings
from django.template import Context, Template
from django.test import TestCase, override_settings

OTHER = {**settings.CHAPTER, 'fraternity': 'Sigma Test', 'chapter_name': 'Gamma Delta',
         'school': 'Example State University', 'school_short': 'Example State',
         'city': 'Springfield', 'state': 'Ohio'}


def _templates_without_chapter_dir():
    t = copy.deepcopy(settings.TEMPLATES)
    t[0]['DIRS'] = [d for d in t[0]['DIRS'] if 'chapter_content' not in str(d)]
    return t


class LandingFallbackTests(TestCase):
    def test_chapter_template_dir_is_searched_first(self):
        dirs = [str(d) for d in settings.TEMPLATES[0]['DIRS']]
        self.assertEqual(dirs[0], str(settings.CHAPTER_CONTENT_DIR / 'templates'))

    def test_original_chapter_keeps_its_own_fallback_text(self):
        body = self.client.get('/').content.decode()
        self.assertIn('Refounded in 2022 on the principles of Beta Theta Pi', body)
        self.assertIn('Samford campus, Birmingham community', body)

    def test_another_chapter_gets_the_generic_text_in_its_own_name(self):
        with override_settings(CHAPTER=OTHER, TEMPLATES=_templates_without_chapter_dir()):
            body = self.client.get('/').content.decode()
        self.assertIn('The Gamma Delta chapter of Sigma Test calls Example State University in Springfield, Ohio home.', body)
        self.assertIn('<title>Sigma Test — Gamma Delta at Example State University</title>', body)
        self.assertIn('the Springfield community', body)
        for literal in ('Alpha Mu', 'Samford', 'Refounded in 2022'):
            self.assertNotIn(literal, body, literal)


class EditorPresetTests(TestCase):
    def _render(self, name, **kw):
        tpl = Template('{% filter escapejs %}{% include "landing/' + name + '" %}{% endfilter %}')
        return tpl.render(Context({}))

    def test_presets_are_single_js_strings(self):
        for name in ('_editor_default_who_we_are.html', '_editor_default_values.html'):
            out = self._render(name)
            self.assertTrue(out)
            for bad in ("'", '\n', '<'):
                self.assertNotIn(bad, out, (name, bad))

    def test_original_chapter_preset_is_verbatim(self):
        out = self._render('_editor_default_values.html')
        self.assertIn('Investing in Samford, Birmingham, and beyond.', out)

    def test_generic_preset_uses_the_chapter(self):
        with override_settings(CHAPTER=OTHER, TEMPLATES=_templates_without_chapter_dir()):
            out = self._render('_editor_default_values.html')
        self.assertIn('Investing in Example State, Springfield, and beyond.', out)


class ChapterOnlyPagesTests(TestCase):
    """09-27-26 — Mason: the archive pages (officer duties, advisors, academic
    standards, …) are chapter-specific and hidden from other chapters. They
    live only in chapter_content/alpha_mu/templates/archive/."""

    PAGES = ('academic_standards_detail', 'advisors_detail', 'committee_details',
             'kai_procedures_detail', 'officer_duties_detail', 'slating_elections_detail')

    def test_archive_pages_are_not_in_the_shared_templates(self):
        from django.conf import settings as s
        self.assertFalse((s.BASE_DIR / 'templates' / 'archive').exists())

    def test_another_chapter_cannot_load_them(self):
        from django.template import TemplateDoesNotExist
        from django.template.loader import get_template
        with override_settings(TEMPLATES=_templates_without_chapter_dir()):
            for name in self.PAGES:
                with self.assertRaises(TemplateDoesNotExist, msg=name):
                    get_template(f'archive/{name}.html')

    def test_they_are_not_routed(self):
        from django.urls import NoReverseMatch, reverse
        for name in self.PAGES:
            with self.assertRaises(NoReverseMatch, msg=name):
                reverse(name)
