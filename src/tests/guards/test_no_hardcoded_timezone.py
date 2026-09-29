"""
Multi-chapter slice 3e (09-29-26) — code uses the chapter's TIME_ZONE setting,
never a hard-coded zone.

Three PDFs stamped their time with `ZoneInfo('America/Chicago')` + "CT", and the
calendar feed declared `X-WR-TIMEZONE: America/Chicago`, whatever TIME_ZONE a
deployment set. Use `timezone.localtime()` / `settings.TIME_ZONE`. Comments may
still name the zone; string literals in code may not.

Run with: python manage.py test src.tests.guards.test_no_hardcoded_timezone
"""
import io
import pathlib
import tokenize

from django.conf import settings
from django.test import SimpleTestCase

SRC = pathlib.Path(settings.BASE_DIR) / 'src'


class NoHardcodedTimezoneTests(SimpleTestCase):
    def test_no_zone_name_string_literals(self):
        hits = []
        for path in SRC.rglob('*.py'):
            rel = path.relative_to(SRC).as_posix()
            if rel.startswith(('tests/', 'migrations/')) or ' ' in rel:
                continue
            src = path.read_text(encoding='utf-8')
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.STRING and 'America/' in tok.string \
                        and not tok.string.lstrip('rbuRBUfF').startswith(('"""', "'''")):
                    hits.append(f'src/{rel}:{tok.start[0]}')
        self.assertEqual(hits, [], 'use timezone.localtime() or settings.TIME_ZONE')
