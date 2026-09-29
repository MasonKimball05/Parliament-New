"""
09-28-26 — multi-chapter: a chapter may override CONTENT partials only.

settings.TEMPLATES DIRS is [CHAPTER_CONTENT_DIR/templates, templates/, …], so a
file in chapter_content/<chapter>/templates/ with the same name as an app
template silently REPLACES it for that deployment. That is the point for
content partials (landing/_default_*.html). For anything else it is a fork
nobody will notice: a chapter's copy of base.html would stop receiving CSP,
2FA or security fixes, and a copy of two_factor/verify.html would be a
security page maintained by nobody.

So: any chapter template that shadows a template from templates/ or an
installed app must match OVERRIDABLE. Files that shadow nothing (e.g. the
archive pages, which exist only in the chapter's directory) are fine.

To make a new partial overridable, add its pattern here in the same commit
that adds the generic version under templates/, and keep it CONTENT (text,
no logic, no scripts).

Run with: python manage.py test src.tests.guards.test_chapter_template_overrides
"""
import fnmatch
import pathlib

from django.conf import settings
from django.template.utils import get_app_template_dirs
from django.test import SimpleTestCase

OVERRIDABLE = (
    'landing/_default_*.html',
    'landing/_editor_default_*.html',
)


def _app_template_names():
    names = set()
    roots = [pathlib.Path(settings.BASE_DIR) / 'templates'] + [pathlib.Path(d) for d in get_app_template_dirs('templates')]
    for root in roots:
        if root.is_dir():
            names.update(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())
    return names


def shadowing_violations(content_root=None):
    content_root = pathlib.Path(content_root or pathlib.Path(settings.BASE_DIR) / 'chapter_content')
    app_names = _app_template_names()
    bad = []
    for tdir in sorted(content_root.glob('*/templates')):
        for f in sorted(tdir.rglob('*')):
            if not f.is_file():
                continue
            name = f.relative_to(tdir).as_posix()
            if name in app_names and not any(fnmatch.fnmatch(name, pat) for pat in OVERRIDABLE):
                bad.append(f'{tdir.parent.name}: {name}')
    return bad


class ChapterTemplateOverridesTests(SimpleTestCase):
    def test_chapters_only_override_content_partials(self):
        self.assertEqual(
            shadowing_violations(), [],
            'A chapter_content template shadows an app template that is not a content '
            'partial. Remove it, or (for a content partial) add it to OVERRIDABLE.')

    def test_the_check_catches_a_shadowed_page(self):
        # Control: a chapter directory that overrides base.html must be caught,
        # and one overriding an allowed partial must not.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            t = pathlib.Path(tmp) / 'demo' / 'templates'
            (t / 'landing').mkdir(parents=True)
            (t / 'base.html').write_text('x')
            (t / 'landing' / '_default_values.html').write_text('x')
            self.assertEqual(shadowing_violations(tmp), ['demo: base.html'])

    def test_the_allowed_overrides_really_exist_upstream(self):
        # A pattern with no generic template behind it would be a new page, not
        # an override; keep the list honest.
        names = _app_template_names()
        for pat in OVERRIDABLE:
            with self.subTest(pat=pat):
                self.assertTrue(any(fnmatch.fnmatch(n, pat) for n in names), pat)
