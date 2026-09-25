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
C&B text (cnb_data.py, constitution_bylaws.html), song lyrics, the landing
page and archive pages, Robert's Rules annotations — which belongs in the
database per chapter (phase 2), plus static files that cannot run template
tags (manifest.json, service-worker.js, offline.html), comments, and the
settings defaults themselves.

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
    'src/management/commands/preflight.py': 2,
    'src/management/commands/seed_resolutions.py': 2,
    'src/management/commands/update_song_lyrics.py': 55,
    'src/management/data/cnb_data.py': 60,
    'src/models/cnb.py': 1,
    'src/models/landing.py': 1,
    'src/models/users.py': 1,
    'src/pledge_classes.py': 1,
    'src/utils/file_validation.py': 1,
    'src/validators.py': 1,
    'src/view/songbook.py': 1,
    'src/view/view_document.py': 5,
    'static/js/service-worker.js': 1,
    'static/manifest.json': 5,
    'static/offline.html': 1,
    'templates/archive/academic_standards_detail.html': 5,
    'templates/archive/advisors_detail.html': 2,
    'templates/archive/committee_details.html': 1,
    'templates/archive/officer_duties_detail.html': 1,
    'templates/chat/channel.html': 2,
    'templates/constitution_bylaws.html': 18,
    'templates/directory.html': 2,
    'templates/house_map.html': 2,
    'templates/landing.html': 17,
    'templates/officer/edit_landing_page.html': 3,
    'templates/profile.html': 1,
    'templates/quote_book/book.html': 1,
    'templates/roberts_rules.html': 11,
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
