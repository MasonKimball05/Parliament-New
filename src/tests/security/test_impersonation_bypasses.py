"""
v3.21.3 — what "log in as" bypasses, and what it deliberately does not.

⚠️ THE BUG. `Enforce2FAMiddleware` exempted impersonation sessions;
`ForcePasswordChangeMiddleware` did not. So an admin using **Log in as** on a
user with `force_password_change` set was bounced to a change-password screen
for an account whose password he does not know — unable to proceed and unable
to help, which is the entire point of the feature.

Two decisions had been made about the same session flag, in two files, and only
one of them was written down. That is the ninth instance of the shape CLAUDE.md
tracks: *a rule stated correctly, a helper written to enforce it, then one call
site left outside the helper.*

The response is not "add the second check". It is:

1. `src/impersonation.py` — one function, and the **reasoning written next to
   it**, including what is deliberately NOT bypassed;
2. `test_no_module_reads_the_session_key_directly` below, so a fourth
   independent decision cannot be made silently.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase, SimpleTestCase
from django.urls import reverse

from src.impersonation import SESSION_ORIGINAL_ID, SESSION_ORIGINAL_NAME
from src.models import ParliamentUser


def make_user(uid, name, **kwargs):
    user = ParliamentUser.objects.create(
        user_id=uid, username=uid, name=name,
        member_status='Active', **kwargs
    )
    user.set_password('impersonation-test-pass-12345!')
    user.save()
    return user


class ImpersonationBypassesAccountSetupTests(TestCase):
    """
    The behaviour, asserted through the middleware stack rather than by calling
    the helper — the bug was never in the helper, it was in which middleware
    consulted it.
    """

    def setUp(self):
        self.admin = make_user('9001', 'The Admin', member_type='Officer', is_admin=True)
        self.target = make_user('9002', 'A Member', member_type='Member')
        self.target.force_password_change = True
        self.target.save(update_fields=['force_password_change'])

        self.client = Client()

    def _impersonate(self):
        """
        Log in as the target with the impersonation marker set, which is the
        state `login_as_view` leaves behind.
        """
        self.client.force_login(self.target)
        session = self.client.session
        session[SESSION_ORIGINAL_ID] = self.admin.user_id
        session[SESSION_ORIGINAL_NAME] = self.admin.name
        session.save()

    def test_the_control_a_real_user_is_still_forced_to_change_it(self):
        """
        ⚠️ FIRST, because everything below is only meaningful if the
        requirement still applies to the person it is for. A bypass that
        accidentally disabled the rule for everyone would pass every other test
        in this class.
        """
        self.client.force_login(self.target)
        response = self.client.get(reverse('home'))
        self.assertRedirects(
            response, reverse('forced_password_change'), fetch_redirect_response=False,
        )

    def test_an_impersonating_admin_is_not_forced_to_change_it(self):
        self._impersonate()
        response = self.client.get(reverse('home'))
        self.assertNotEqual(response.status_code, 302, 'Impersonation was redirected away from home.')

    def test_the_users_flag_is_untouched_by_impersonation(self):
        """
        The bypass must not *resolve* the requirement. The user still has to
        change his password the next time he logs in himself — an admin walking
        past the screen is not the user having dealt with it.
        """
        self._impersonate()
        self.client.get(reverse('home'))
        self.target.refresh_from_db()
        self.assertTrue(self.target.force_password_change)

    def test_it_still_applies_once_impersonation_ends(self):
        """Clearing the marker restores the requirement in the same session."""
        self._impersonate()
        session = self.client.session
        del session[SESSION_ORIGINAL_ID]
        session.save()

        response = self.client.get(reverse('home'))
        self.assertRedirects(
            response, reverse('forced_password_change'), fetch_redirect_response=False,
        )


class TheHelperIsToleranTests(SimpleTestCase):
    """
    `is_impersonating` runs in middleware on every request, including ones
    where the session machinery has not produced a normal object.
    """

    def test_it_survives_a_request_with_no_session(self):
        from src.impersonation import is_impersonating

        class Bare:
            pass

        self.assertFalse(is_impersonating(Bare()))

    def test_it_survives_a_session_that_is_not_a_mapping(self):
        from src.impersonation import is_impersonating

        class Odd:
            session = object()

        self.assertFalse(is_impersonating(Odd()))


class OnlyTheHelperKnowsTheSessionKeyTests(SimpleTestCase):
    """
    ⚠️ THE ENUMERATION, and the actual response to the bug.

    Every raw read of the session key is an independent decision about what
    impersonation bypasses. Two such decisions existed, they disagreed, and the
    disagreement was invisible because neither file mentioned the other. One
    function means the next person changing the policy changes it once, and the
    docstring next to it says what the policy is.
    """

    #: The module that owns the key, plus the test that asserts this rule.
    #: `login_as_view` re-exports the constants but does not read the session
    #: by name; it is allowed to import them.
    ALLOWED = {'src/impersonation.py', 'src/tests/security/test_impersonation_bypasses.py'}

    def _source_files(self):
        root = Path(settings.BASE_DIR) / 'src'
        return sorted(p for p in root.rglob('*.py') if 'migrations' not in p.parts)

    def test_the_key_is_defined_once(self):
        """The control — a scan for a string nobody uses proves nothing."""
        self.assertEqual(SESSION_ORIGINAL_ID, '_impersonating_original_user_id')

    def test_no_module_reads_the_session_key_directly(self):
        # Assembled so this module does not match its own rule — the same
        # technique as `test_nosec_hygiene` and `test_csrf_token_source`, for
        # the same reason.
        literal = "'_impersonating_original" + "_user_id'"
        offenders = []
        for path in self._source_files():
            relative = str(path.relative_to(settings.BASE_DIR))
            if relative in self.ALLOWED:
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.split('\n'), start=1):
                if literal in line or literal.replace("'", '"') in line:
                    offenders.append(f'{relative}:{lineno}')

        self.assertEqual(
            offenders, [],
            'These read the impersonation session key by name. Each such read '
            'is a separate decision about what impersonation bypasses, and two '
            'of them silently disagreeing is the bug this file exists for:\n  '
            + '\n  '.join(offenders)
            + '\n\nUse src.impersonation.is_impersonating() and record the '
              'decision in that module\'s docstring.',
        )


class RestrictionsAreNotBypassedTests(TestCase):
    """
    ⚠️ THE OTHER HALF OF THE POLICY, AND THE MORE IMPORTANT ONE.

    Quarantine, emergency lockdown and maintenance mode are not setup steps a
    user failed to finish — they are decisions somebody made about an account or
    the whole site, usually during an incident. **A bypass that also disables
    the controls you reach for in an incident works against you exactly when it
    matters.**

    Asserted structurally, because the failure mode is somebody adding the
    exemption to one of these later "for consistency" without revisiting the
    argument.
    """

    RESTRICTION_MIDDLEWARE = (
        ('src/middleware/security.py', 'QuarantineEnforcementMiddleware'),
        ('src/middleware/lockdown.py', 'EmergencyLockdownMiddleware'),
        ('src/middleware/maintenance.py', 'MaintenanceModeMiddleware'),
    )

    def test_none_of_them_consults_impersonation(self):
        offenders = []
        for relative, class_name in self.RESTRICTION_MIDDLEWARE:
            path = Path(settings.BASE_DIR) / relative
            if not path.exists():
                continue
            text = path.read_text(encoding='utf-8')
            start = text.find(f'class {class_name}')
            if start == -1:
                continue
            # Up to the next top-level class, or end of file.
            end = text.find('\nclass ', start + 1)
            body = text[start:end if end != -1 else len(text)]
            if re.search(r'is_impersonating\s*\(', body):
                offenders.append(f'{relative}: {class_name}')

        self.assertEqual(
            offenders, [],
            'A restriction middleware now exempts impersonation. That may be '
            'right, but it is a security decision, not a consistency fix — '
            'make it deliberately and rewrite the policy in '
            'src/impersonation.py before removing this assertion:\n  '
            + '\n  '.join(offenders),
        )
