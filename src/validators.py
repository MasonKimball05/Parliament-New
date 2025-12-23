"""
Custom validators for Parliament application security
"""
import re
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
