"""
Multi-chapter slice 3e (09-29-26) — the role codes the code keys on are REGISTERED.

A chapter may rename roles, but not re-code the ones the code looks up
(src/chapter_structure.py). This guard keeps that list honest: every role-code
literal used in a lookup under src/ must be in REQUIRED_ROLE_CODES or
SELF_HEALING_ROLE_CODES, so src.W006 can tell a chapter it is missing one.

It also pins the registry against the other two lists of the same facts:
Role.DEFAULT_ROLES (what `restore_committees_and_roles` can restore) and
signals.EXEC_ROLE_CODES / apps.COMMITTEE_FLAG_DEFAULTS.

Run with: python manage.py test src.tests.guards.test_required_role_codes
"""
import pathlib
import re

from django.conf import settings
from django.test import SimpleTestCase

from src.chapter_structure import (
    REQUIRED_COMMITTEE_FLAGS, REQUIRED_ROLE_CODES, SELF_HEALING_ROLE_CODES,
)

SRC = pathlib.Path(settings.BASE_DIR) / 'src'

# A role lookup: `roles__code='X'`, `roles.filter(code='X')`, `Role.objects.*(code='X')`,
# any of them with __iexact. Committee lookups use flags, not codes.
ROLE_LOOKUP = re.compile(
    r"""(?:roles__code(?:__iexact)?|(?:roles|Role\.objects)\.\w+\(\s*code(?:__iexact)?)\s*=\s*['"]([A-Za-z]+)['"]""")
ROLE_CODE_LIST = re.compile(r"""Role\.objects\.\w+\(\s*code__in\s*=\s*\[([^\]]*)\]""")


def _role_code_literals():
    found = {}
    for path in SRC.rglob('*.py'):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(('tests/', 'migrations/')) or ' ' in rel:
            continue
        text = path.read_text(encoding='utf-8')
        # Whole-file matching: `get_or_create(\n    code='CHOIR', ...)` spans lines.
        for m in ROLE_LOOKUP.finditer(text):
            found.setdefault(m.group(1), []).append(f'src/{rel}:{text.count(chr(10), 0, m.start()) + 1}')
        for m in ROLE_CODE_LIST.finditer(text):
            n = text.count(chr(10), 0, m.start()) + 1
            for code in re.findall(r"""['"]([A-Za-z]+)['"]""", m.group(1)):
                found.setdefault(code, []).append(f'src/{rel}:{n}')
    return found


class RequiredRoleCodesTests(SimpleTestCase):
    def test_the_scan_finds_the_known_lookups(self):
        # If this drops, the regex stopped matching and the guard below is blind.
        found = _role_code_literals()
        for code in ('VPP', 'President', 'EVP', 'VPR', 'CNB', 'CHOIR', 'HIST'):
            self.assertIn(code, found)

    def test_every_role_code_lookup_is_registered(self):
        known = set(REQUIRED_ROLE_CODES) | SELF_HEALING_ROLE_CODES
        stray = {c: where for c, where in _role_code_literals().items() if c not in known}
        self.assertEqual(stray, {}, (
            'New code keys on these role codes. Add each to REQUIRED_ROLE_CODES in '
            'src/chapter_structure.py (with what breaks without it), or to '
            'SELF_HEALING_ROLE_CODES if the lookup is a get_or_create.'))

    def test_exec_role_codes_are_required(self):
        from src.signals import EXEC_ROLE_CODES
        self.assertEqual(set(EXEC_ROLE_CODES) - set(REQUIRED_ROLE_CODES), set())

    def test_required_roles_can_be_restored(self):
        from src.models import Role
        defaults = {code for _, code, _ in Role.DEFAULT_ROLES}
        self.assertEqual(set(REQUIRED_ROLE_CODES) - defaults, set(),
                         'restore_committees_and_roles could not recreate these')

    def test_committee_flags_match_the_post_migrate_defaults(self):
        from src.apps import COMMITTEE_FLAG_DEFAULTS
        for flag, (code, _) in REQUIRED_COMMITTEE_FLAGS.items():
            self.assertEqual(COMMITTEE_FLAG_DEFAULTS.get(code), flag)
