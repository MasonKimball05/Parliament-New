"""
v4.4.0: the app signs out on certain bare error codes (its errors.ts
SIGNED_OUT_REASONS: ``invalid``, ``expired``, ``revoked``…). A form's 400 sent
as ``invalid`` signed people out when they made an event with a missing field,
so app-facing errors other than auth's never use those codes.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

SIGNED_OUT_REASONS = (
    'revoked', 'reused', 'invalid', 'expired', 'reauth_required', 'inactive',
    'quarantined', 'removed', 'password_changed', 'second_factor_required',
    'mobile_device_signed_out',
)
# Where those codes are meant: refreshing tokens and checking a device.
AUTH_FILES = {'tokens.py', 'views_auth.py', 'authentication.py'}
MOBILE = Path(__file__).resolve().parents[2] / 'mobile'


class NoSignedOutCodesOutsideAuthTests(SimpleTestCase):
    def test_no_view_uses_a_signed_out_reason_as_its_code(self):
        reasons = '|'.join(SIGNED_OUT_REASONS)
        pattern = re.compile(rf"(code=|'error':\s*|Error\()'({reasons})'")
        offenders = []
        for path in sorted(MOBILE.glob('*.py')):
            if path.name in AUTH_FILES:
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f'{path.name}:{n}: {line.strip()}')
        self.assertEqual(offenders, [], 'The app signs out on these codes; use e.g. invalid_input.')
