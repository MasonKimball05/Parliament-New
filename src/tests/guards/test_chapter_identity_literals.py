"""
09-25-26 — multi-chapter phase 1: chapter identity has ONE home (src/chapter.py).

This counts, per file, the lines that still hard-code Alpha Mu's identity
("Alpha Mu", "Beta Theta Pi", "Samford", samford.edu, am-parliament.org,
am-coat-of-arms). The counts in KNOWN may only go DOWN:

  * a file above its count, or a new file, fails — use `get_chapter()` /
    `{% chapter "field" %}` instead of a literal;
  * a file BELOW its count also fails, with the new number — lower KNOWN in
    the same commit, so the list never hides room for new literals.

What is left in KNOWN is mostly chapter CONTENT rather than identity — the
old static C&B page (constitution_bylaws.html), song lyrics, the landing
page and archive pages, Robert's Rules annotations — which belongs in the
database per chapter (phase 2), plus the settings defaults themselves.

09-27-26 (phase 2, slice 2a): 213 lines / 29 files -> 192 / 16. The PWA
files are now rendered by src/view/pwa.py, pledge-class founding comes from
settings.CHAPTER, and the leftover comments were reworded.
09-27-26 (slice 2b): the C&B seed text moved to chapter_content/alpha_mu/cnb.py
(per-chapter content, loaded by src/chapter_content.py) -> 132 / 15.
09-27-26 (slice 2c): landing page + editor presets + Robert's Rules headings
use {% chapter %}; landing fallback text is a per-chapter template override
(chapter_content/<chapter>/templates/landing/) -> 100 / 11.
09-27-26 (slice 2c, cont.): the unrouted archive pages (Alpha Mu content) moved to
chapter_content/alpha_mu/templates/archive/ -> 91 / 7.
09-28-26 (slice 3a): the chapter's C&B PDF is chapter content
(chapter_content/<chapter>/reference_documents.json); seed_resolutions names
the chapter from config and refuses on other chapters; the unrouted static
constitution_bylaws.html moved to the archive -> 66 / 4.
09-28-26 (slice 3d): the default song lyrics moved to fraternity_content/
beta_theta_pi/songs.json (shared by every chapter) -> 11 / 3.
Files under chapter_content/ are deliberately NOT scanned: that directory is
where one chapter's own text is supposed to live.

Run with: python manage.py test src.tests.guards.test_chapter_identity_literals
"""
import pathlib
import re

from django.conf import settings
from django.test import SimpleTestCase

PATTERN = re.compile(r'Alpha Mu|Beta Theta Pi|Samford|samford\.edu|am-parliament\.org|am-coat-of-arms')
ROOTS = [('src', '*.py'), ('templates', '*.html'), ('templates', '*.txt'), ('Parliament', '*.py'),
         ('static', '*.json'), ('static', '*.js'), ('static', '*.html')]
SKIP = ('src/tests/', 'migrations', 'src/chapter.py', 'static/vendor/', '__pycache__')

KNOWN = {
    'Parliament/settings.py': 9,
    'src/models/cnb.py': 1,
    'src/view/songbook.py': 1,
}


def _counts():
    base = pathlib.Path(settings.BASE_DIR)
    out = {}
    for root, glob in ROOTS:
        for f in (base / root).rglob(glob):
            rel = f.relative_to(base).as_posix()
            if any(s in rel for s in SKIP):
                continue
            n = sum(1 for line in f.read_text(errors='ignore').splitlines() if PATTERN.search(line))
            if n:
                out[rel] = n
    return out


class ChapterIdentityLiteralTests(SimpleTestCase):
    def test_hardcoded_chapter_identity_only_goes_down(self):
        actual = _counts()
        grew = {f: (KNOWN.get(f, 0), n) for f, n in actual.items() if n > KNOWN.get(f, 0)}
        shrank = {f: (k, actual.get(f, 0)) for f, k in KNOWN.items() if actual.get(f, 0) < k}
        self.assertFalse(grew, 'New hard-coded chapter identity (file: known → now). Use '
                               'get_chapter() / {% chapter "field" %} (src/chapter.py): ' + repr(grew))
        self.assertFalse(shrank, 'Fewer literals than KNOWN says — lower these counts in KNOWN '
                                 '(file: known → now): ' + repr(shrank))
