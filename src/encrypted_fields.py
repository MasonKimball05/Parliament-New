"""
Custom encrypted fields for Django 5.x compatibility
Uses Fernet (AES-128-CBC + HMAC-SHA256) for authenticated encryption
"""
from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet
import base64


def _fernet_storage_length(plaintext_length):
    """
    Exact storage size of a Fernet token for a plaintext of the given length.

    Fernet token = base64url(version 1B + timestamp 8B + IV 16B +
    AES-CBC ciphertext (padded to next 16B) + HMAC 32B).

    ⚠ ASCII assumption (07-08-26): plaintext_length is the field's max_length
    in *characters*, but encryption pads on UTF-8 *bytes*
    (get_prep_value does str(value).encode('utf-8')). For ASCII-only data
    (IP addresses — the only current use) chars == bytes and this is exact.
    If you add an encrypted field that can hold multi-byte characters
    (names, emails with IDN, free text), a max-length value could produce a
    token wider than the column. In that case size for the UTF-8 worst case —
    pass plaintext_length * 4 here — or validate the field as ASCII-only.
    """
    ciphertext = 16 * (plaintext_length // 16 + 1)
    raw = 1 + 8 + 16 + ciphertext + 32
    return 4 * ((raw + 2) // 3)  # base64: 4 output chars per 3 input bytes


class EncryptedFieldMixin:
    """
    Mixin to add encryption/decryption to Django fields.

    max_length semantics: the DECLARED max_length is the maximum *plaintext*
    length (so Django's MaxLengthValidator validates user input correctly).
    The database column is sized separately via db_type() using the exact
    Fernet-token math above.

    History (07-05-26): the old version doubled max_length in __init__ with
    no inverse in deconstruct(), which (a) made makemigrations emit the same
    no-op AlterField forever (historical 0217/0222), and (b) was too small
    anyway — a Fernet token for a 45-char IP is ~140 chars, not 90. Prod
    columns only fit because the state re-doubling accidentally widened them.
    Now __init__/deconstruct are clean passthroughs (stable migrations) and
    the column width is computed correctly.
    """

    def db_type(self, connection):
        """Size the column for the encrypted token, not the plaintext."""
        if getattr(self, 'max_length', None):
            original = self.max_length
            try:
                self.max_length = _fernet_storage_length(original)
                return super().db_type(connection)
            finally:
                self.max_length = original
        return super().db_type(connection)

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
