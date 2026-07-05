"""
Custom validators for Parliament application security
"""
import hashlib
import re
from urllib.request import urlopen, Request
from urllib.error import URLError
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


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
            with urlopen(req, timeout=3) as resp:
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
