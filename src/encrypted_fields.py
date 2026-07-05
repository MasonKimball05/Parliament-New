"""
Custom encrypted fields for Django 5.x compatibility
Uses Fernet (AES-128-CBC + HMAC-SHA256) for authenticated encryption
"""
from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet
import base64


class EncryptedFieldMixin:
    """Mixin to add encryption/decryption to Django fields"""

    def __init__(self, *args, **kwargs):
        # Encrypted fields need more space for base64 encoding
        if 'max_length' in kwargs:
            # Fernet adds ~50% overhead, so increase max_length
            kwargs['max_length'] = kwargs.get('max_length', 255) * 2
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        """
        Reverse the max_length doubling done in __init__.

        Without this, every load of a migration re-doubles max_length
        (declared 90 -> instance 180 -> migration stores 180 -> state loads
        as 360 -> never equals the instance's 180), so makemigrations
        emitted the same no-op AlterField forever (see historical 0217/0222).
        Storing the *declared* value makes __init__/deconstruct a stable
        round-trip.
        """
        name, path, args, kwargs = super().deconstruct()
        if kwargs.get('max_length'):
            kwargs['max_length'] //= 2
        return name, path, args, kwargs

    def get_fernet(self):
        """Get Fernet cipher instance from settings"""
        key = getattr(settings, 'CRYPTOGRAPHY_KEY', None)
        if not key:
            raise ValueError(
                "CRYPTOGRAPHY_KEY not set in settings. "
                "Run 'python3 generate_encryption_key.py' to generate a key."
            )
        return Fernet(key)

    def get_prep_value(self, value):
        """Encrypt value before saving to database"""
        if value is None or value == '':
            return value

        # If already encrypted (starts with 'gAAAAA'), don't re-encrypt
        if isinstance(value, str) and value.startswith('gAAAAA'):
            return value

        fernet = self.get_fernet()
        # Convert to bytes, encrypt, then decode to string for storage
        encrypted = fernet.encrypt(str(value).encode('utf-8'))
        return encrypted.decode('utf-8')

    def from_db_value(self, value, expression, connection):
        """Decrypt value when reading from database"""
        if value is None or value == '':
            return value

        try:
            fernet = self.get_fernet()
            # Decrypt the value
            decrypted = fernet.decrypt(value.encode('utf-8'))
            return decrypted.decode('utf-8')
        except Exception:
            # If decryption fails, return the original value
            # This handles the case where data isn't encrypted yet
            return value

    def to_python(self, value):
        """Convert value to Python type"""
        if value is None or value == '':
            return value

        # If it's already decrypted, return it
        if not (isinstance(value, str) and value.startswith('gAAAAA')):
            return super().to_python(value)

        # Otherwise decrypt it
        try:
            fernet = self.get_fernet()
            decrypted = fernet.decrypt(value.encode('utf-8'))
            decrypted_str = decrypted.decode('utf-8')
            return super().to_python(decrypted_str)
        except Exception:
            return super().to_python(value)


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """CharField with automatic encryption/decryption"""
    pass


class EncryptedEmailField(EncryptedFieldMixin, models.EmailField):
    """EmailField with automatic encryption/decryption"""

    def to_python(self, value):
        """Convert to email, handling encryption"""
        if value is None or value == '':
            return value

        # First decrypt if needed
        if isinstance(value, str) and value.startswith('gAAAAA'):
            try:
                fernet = self.get_fernet()
                decrypted = fernet.decrypt(value.encode('utf-8'))
                value = decrypted.decode('utf-8')
            except Exception:
                pass

        # Then validate as email
        return super(EncryptedFieldMixin, self).to_python(value)


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    """TextField with automatic encryption/decryption"""

    def __init__(self, *args, **kwargs):
        # TextField doesn't have max_length, so don't modify it
        models.TextField.__init__(self, *args, **kwargs)
