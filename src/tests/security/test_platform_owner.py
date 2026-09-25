"""
09-25-26 — the platform-owner pin is one account, not "whoever holds user_id 73".

Mason: "73 is my uid. We'll need to adjust that so that it's myself alone not
every user with 73." With a second chapter, '73' is somebody else in that
chapter's database. The pin now comes from PLATFORM_OWNER_USER_ID (default
'73' for Alpha Mu) plus an optional PLATFORM_OWNER_EMAIL second factor.

Run with: python manage.py test src.tests.security.test_platform_owner
"""
import pathlib
import re
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

from src.checks_platform import platform_owner_pin
from src.permissions import is_platform_owner


def _user(uid, email='owner@example.com'):
    return mock.Mock(is_authenticated=True, user_id=uid, email=email)


class IsPlatformOwnerTests(SimpleTestCase):
    def test_default_pin_is_73(self):
        self.assertTrue(is_platform_owner(_user('73')))
        self.assertFalse(is_platform_owner(_user('74')))

    def test_anonymous_is_never_the_owner(self):
        self.assertFalse(is_platform_owner(AnonymousUser()))

    @override_settings(PLATFORM_OWNER_USER_ID='P-ABC123')
    def test_the_pin_follows_the_setting(self):
        self.assertTrue(is_platform_owner(_user('P-ABC123')))
        self.assertFalse(is_platform_owner(_user('73')))

    @override_settings(PLATFORM_OWNER_USER_ID='')
    def test_empty_setting_pins_nobody(self):
        self.assertFalse(is_platform_owner(_user('73')))
        self.assertFalse(is_platform_owner(_user('')))

    @override_settings(PLATFORM_OWNER_EMAIL='Mason@Example.com')
    def test_email_second_factor(self):
        self.assertTrue(is_platform_owner(_user('73', 'mason@example.com')))
        # Another chapter's member 73 — right id, wrong person.
        self.assertFalse(is_platform_owner(_user('73', 'someone.else@example.edu')))

    def test_template_filter(self):
        t = Template('{% if u|is_platform_owner %}yes{% else %}no{% endif %}')
        self.assertEqual(t.render(Context({'u': _user('73')})), 'yes')
        self.assertEqual(t.render(Context({'u': _user('74')})), 'no')


class PlatformOwnerCheckTests(SimpleTestCase):
    def test_alpha_mu_default_is_quiet(self):
        self.assertEqual(platform_owner_pin(None), [])

    @override_settings(CHAPTER_DOMAIN='gd.example.org', CHAPTER_IS_DEFAULT=False, PLATFORM_OWNER_USER_ID_IS_DEFAULT=True,
                       PLATFORM_OWNER_USER_ID='73', PLATFORM_OWNER_EMAIL='')
    def test_another_chapter_on_the_default_warns(self):
        self.assertEqual([w.id for w in platform_owner_pin(None)], ['src.W004'])

    @override_settings(CHAPTER_DOMAIN='gd.example.org', CHAPTER_IS_DEFAULT=False, PLATFORM_OWNER_USER_ID_IS_DEFAULT=False,
                       PLATFORM_OWNER_USER_ID='12')
    def test_an_explicit_setting_is_quiet(self):
        self.assertEqual(platform_owner_pin(None), [])


class NoLiteralOwnerIdTests(SimpleTestCase):
    """The literal must not come back — every check goes through is_platform_owner."""
    PATTERN = re.compile(r"""user_id\)?\s*==\s*['"]73['"]""")

    def test_no_user_id_equals_73_literals(self):
        base = pathlib.Path(settings.BASE_DIR)
        hits = []
        for root, glob in (('src', '*.py'), ('templates', '*.html')):
            for f in (base / root).rglob(glob):
                rel = f.relative_to(base).as_posix()
                if rel.startswith('src/tests/') or 'migrations' in rel:
                    continue
                for n, line in enumerate(f.read_text(errors='ignore').splitlines(), 1):
                    if self.PATTERN.search(line) and 'was PROTECTED' not in line:
                        hits.append(f'{rel}:{n}')
        # Docstrings/comments that quote the old code are allowed only in these files.
        allowed_prefixes = ('src/permissions.py', 'src/templatetags/platform_tags.py')
        hits = [h for h in hits if not h.startswith(allowed_prefixes)]
        self.assertEqual(hits, [], "Use is_platform_owner(user) / user|is_platform_owner, not user_id == '73'")
