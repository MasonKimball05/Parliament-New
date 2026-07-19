"""
v3.14.2 — uploaded-filename sanitization (src/storage.py).

Filenames end up in Content-Disposition headers, X-Accel-Redirect URIs, and
hand-written links; these tests lock in that no future upload can carry
spaces, quotes, or non-ASCII into MEDIA_ROOT. Existing stored files are
deliberately untouched — only names of NEW saves are sanitized.
"""
import tempfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from src.storage import DualLocationStorage, SanitizedFileSystemStorage

_TMP_MEDIA = tempfile.mkdtemp(prefix='parliament-test-uploads-')


class GetValidNameTests(TestCase):
    """Unit coverage of the shared mixin, via both storage classes."""

    def setUp(self):
        self.storages = [SanitizedFileSystemStorage(), DualLocationStorage()]

    def assertValidName(self, raw, expected):
        for storage in self.storages:
            self.assertEqual(storage.get_valid_name(raw), expected,
                             f'{type(storage).__name__}({raw!r})')

    def test_spaces_quotes_and_accents(self):
        self.assertValidName('Résolution "Fall" 2026.pdf',
                             'resolution-fall-2026.pdf')

    def test_uppercase_extension_lowered(self):
        self.assertValidName('Chapter Minutes.PDF', 'chapter-minutes.pdf')

    def test_extension_junk_stripped(self):
        self.assertValidName('evil.p"df', 'evil.pdf')

    def test_no_extension(self):
        self.assertValidName('README', 'readme')

    def test_all_junk_stem_falls_back(self):
        self.assertValidName('???.pdf', 'file.pdf')

    def test_clean_names_pass_through(self):
        self.assertValidName('budget-fy26_v2.docx', 'budget-fy26_v2.docx')


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class DefaultStorageWiringTests(TestCase):
    """settings.STORAGES must actually point at the sanitizing class, and
    the FileField save path must land files under the sanitized name.

    NOTE: `Storage.save()` called directly does NOT sanitize — only the
    model-field path (`FileField.generate_filename` → storage
    `generate_filename` → `get_valid_name`) does. That's fine here: no view
    calls `storage.save()` with a user filename (grepped 07-19), every
    upload goes through a FileField. If that ever changes, route the name
    through `generate_filename` first.
    """

    def test_default_storage_is_sanitized(self):
        self.assertIsInstance(default_storage, SanitizedFileSystemStorage)

    def test_generate_filename_sanitizes_with_upload_to_dir(self):
        self.assertEqual(
            default_storage.generate_filename(
                'legislation_docs/Résolution "Fall" 2026.pdf'),
            'legislation_docs/resolution-fall-2026.pdf')

    def test_end_to_end_save(self):
        """Round-trip exactly like FieldFile.save: generate_filename, then
        save. Collision suffixing may add _XXXXXXX before the extension."""
        name = default_storage.save(
            default_storage.generate_filename('Résolution "Fall" 2026.pdf'),
            ContentFile(b'%PDF-1.4 x'))
        self.assertTrue(name.startswith('resolution-fall-2026'))
        self.assertTrue(name.endswith('.pdf'))
        for raw in (' ', '"', 'é'):
            self.assertNotIn(raw, name)
