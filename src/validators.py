"""
Custom validators for Parliament application security
"""
import hashlib
import re
from urllib.request import urlopen, Request
from urllib.error import URLError
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


# ---------------------------------------------------------------------------
# Known-weak passwords — a small, hand-curated list of passwords that satisfy
# CustomPasswordValidator's complexity rule (upper + lower + digit + symbol,
# 9+ chars) and may not appear in Django's bundled CommonPasswordValidator
# list (that list is bare dictionary words / bare common passwords, not
# "complexity-rule-passing" variants like "Password123!"), yet are exactly
# the kind of thing someone types to satisfy a complexity prompt with the
# least possible thought. Raised directly by Mason after testing one of
# these against a real password-set form.
#
# This is deliberately LOCAL and network-independent — the one existing line
# of defense against this exact shape of password is PwnedPasswordValidator
# below, which calls the Have I Been Pwned API and FAILS OPEN if that API is
# unreachable (by design, so a third-party outage never blocks someone from
# setting a password at all). That's the right call for an external
# dependency, but it means today there is no local backstop: if the HIBP
# request times out, gets rate-limited, or the outbound request is ever
# blocked at the network level, "Password123!" and everything like it sails
# straight through with nothing left to catch it. This list is that
# backstop — small on purpose (every entry costs a `.lower()` set lookup,
# not a network round trip, so there's no real cost to keeping it tight and
# no reason to bloat it into a second copy of Django's own list).
#
# Kept in ALL_CAPS-free, human-typed casing here for readability; the
# validator below normalizes both sides to lowercase before comparing, so a
# submitted "PASSWORD123!" or "password123!" is caught exactly the same way
# "Password123!" is — casing alone isn't a real defense against a password
# that's already this guessable.
COMMON_WEAK_PASSWORDS = [
    'Password123!',
    'Password1!',
    'Password1234!',
    "Passw0rd!",
    "Passw0rd123!",
    'Welcome123!',
    'Welcome1!',
    'Qwerty123!',
    'Qwerty1!',
    'Changeme123!',
    'Changeme1!',
    'Letmein123!',
    'Iloveyou123!',
    'Football123!',
    'Sunshine123!',
    'Admin123!',
    'Chapter123!',
    'Chapter1234!',
    # App/org-specific guesses — see /Users/masonkimball/Documents/Claude/Resources/CLAUDE.md
    # for why "Beta Theta Pi" and "Parliament" are the two names an attacker
    # (or a bored member) would try first for this specific app.
    'BetaThetaPi1!',
    'BetaThetaPi123!',
    'Parliament1!',
    'Parliament123!',
]

# Precomputed lowercase set for O(1) lookups — built once at import time,
# not per-validation-call.
_COMMON_WEAK_PASSWORDS_LOWER = frozenset(p.lower() for p in COMMON_WEAK_PASSWORDS)


class CustomPasswordValidator:
    """
    Validates that a password meets complexity requirements:
    - Minimum length of 9 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one number
    - At least one special symbol (!@#$%^&*()_+-=[]{}|;:,.<>?/)
    """

    def __init__(self, min_length=9):
        self.min_length = min_length

    def validate(self, password, user=None):
        errors = []

        # Check minimum length
        if len(password) < self.min_length:
            errors.append(f"Password must be at least {self.min_length} characters long.")

        # Check for uppercase letter
        if not re.search(r'[A-Z]', password):
            errors.append("Password must contain at least one uppercase letter.")

        # Check for lowercase letter
        if not re.search(r'[a-z]', password):
            errors.append("Password must contain at least one lowercase letter.")

        # Check for digit
        if not re.search(r'\d', password):
            errors.append("Password must contain at least one number.")

        # Check for special character
        if not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/]', password):
            errors.append("Password must contain at least one special symbol (!@#$%^&* etc.).")

        if errors:
            raise ValidationError(errors, code='password_too_weak')

    def get_help_text(self):
        return _(
            f"Your password must be at least {self.min_length} characters long and contain "
            "at least one uppercase letter, one lowercase letter, one number, and one special symbol."
        )


class KnownWeakPasswordValidator:
    """
    Rejects passwords that exactly match (case-insensitively) an entry in
    COMMON_WEAK_PASSWORDS. Purely local — no network call, no dependency on
    Django's bundled common-password corpus — so it cannot fail open the way
    PwnedPasswordValidator can when the HIBP API is unreachable.

    This is intentionally a small, exact-match list rather than a fuzzy or
    pattern-based check: the goal is a fast, always-on backstop for the
    specific "complexity-rule-passing but trivially guessable" passwords in
    COMMON_WEAK_PASSWORDS, not a second CommonPasswordValidator.
    """

    def validate(self, password, user=None):
        if password.lower() in _COMMON_WEAK_PASSWORDS_LOWER:
            raise ValidationError(
                _(
                    'This password is a commonly used one that shows up in nearly every '
                    'weak-password list — it meets the length and complexity rules but '
                    'offers no real protection. Please choose something less predictable.'
                ),
                code='password_known_weak',
            )

    def get_help_text(self):
        return _('Your password must not be a commonly used weak password (e.g. "Password123!").')


class PwnedPasswordValidator:
    """
    Rejects passwords found in the Have I Been Pwned database using the
    k-anonymity range API. The full password is never transmitted — only
    the first 5 characters of its SHA-1 hash.

    Fails open: if the API is unreachable (network error, timeout), the
    password is accepted so users aren't blocked by a third-party outage.
    """

    def validate(self, password, user=None):
        # SHA-1 is mandated by the HIBP k-anonymity protocol — it's a lookup
        # key here, not a security hash (we never store or trust it).
        # usedforsecurity=False documents that and satisfies bandit B324.
        sha1 = hashlib.sha1(
            password.encode('utf-8'), usedforsecurity=False
        ).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]
        try:
            req = Request(
                f'https://api.pwnedpasswords.com/range/{prefix}',
                headers={'User-Agent': 'Parliament-App-PasswordCheck'},
            )
            with urlopen(req, timeout=3) as resp:  # nosec B310  # fixed literal https:// URL, no user-controlled scheme
                body = resp.read().decode('utf-8')
            for line in body.splitlines():
                parts = line.split(':')
                if len(parts) == 2 and parts[0] == suffix:
                    count = int(parts[1])
                    if count > 0:
                        raise ValidationError(
                            _(
                                f'This password has appeared in {count:,} known data breach'
                                f'{"es" if count != 1 else ""}. Please choose a different password.'
                            ),
                            code='password_pwned',
                        )
        except ValidationError:
            raise
        except (URLError, OSError, Exception):
            # API unavailable — fail open so users aren't blocked
            pass

    def get_help_text(self):
        return _('Your password must not appear in known data breaches.')
