"""
Tests for the `document_versioning` flag wiring.

Before this, `DocumentVersion` had zero writers anywhere in the codebase —
there was no "replace this document's file" action to gate, only delete-and-
re-upload-as-new. `committee_replace_document` is the first thing that
creates a DocumentVersion row, and the flag gates its own existence rather
than toggling pre-existing behaviour.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

from src.models import Committee, CommitteeDocument, ParliamentUser
from src.models.documents import DocumentVersion
from src.models_feature_flags import FeatureFlag


def make_user(user_id, member_type='Member', **kwargs):
    defaults = dict(name=f'User {user_id}', username=f'user_{user_id}', member_type=member_type)
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=user_id, password='testpass123', **defaults)


def make_committee():
    return Committee.objects.create(name='Test Committee', code='TEST', is_active=True)


def _pdf(name='original.pdf', label=b'original'):
    """
    A minimal but structurally real PDF — a bare `%PDF-1.4 <label>` string is
    not reliable: libmagic's content-type sniff (`validate_uploaded_file`)
    sometimes reads a short, structure-less payload like that as text/plain
    instead of application/pdf, non-deterministically enough that it bit two
    near-identical fixtures differently in the same test run. A real object/
    trailer/EOF skeleton sniffs consistently.
    """
    content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% ' + label + b'\ntrailer\n%%EOF\n'
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class CommitteeReplaceDocumentTests(TestCase):
    def setUp(self):
        self.committee = make_committee()
        self.chair = make_user('chair1', member_type='Officer')
        self.member = make_user('member1')
        self.committee.chairs.add(self.chair)
        self.committee.members.add(self.chair, self.member)

        self.document = CommitteeDocument.objects.create(
            committee=self.committee,
            title='Bylaws Draft',
            document=_pdf(),
            uploaded_by=self.chair,
            visibility='committee_only',
        )

        self.client = Client()

    def _enable_flag(self, enabled=True):
        FeatureFlag.objects.update_or_create(
            name='document_versioning',
            defaults={'display_name': 'Document Versioning', 'is_enabled': enabled},
        )

    def test_flag_disabled_blocks_the_route_entirely(self):
        self._enable_flag(False)
        self.client.force_login(self.chair)
        resp = self.client.post(
            reverse('committee_replace_document', args=[self.committee.code, self.document.id]),
            {'file': _pdf('v2.pdf', b'new content'), 'change_notes': 'fixed typo'},
        )
        self.assertEqual(resp.status_code, 403)
        self.document.refresh_from_db()
        self.assertEqual(self.document.version_number, 1)
        self.assertEqual(DocumentVersion.objects.filter(document=self.document).count(), 0)

    def test_replace_archives_the_old_file_and_bumps_version(self):
        self._enable_flag(True)
        self.client.force_login(self.chair)
        original_name = self.document.document.name

        resp = self.client.post(
            reverse('committee_replace_document', args=[self.committee.code, self.document.id]),
            {'file': _pdf('v2.pdf', b'new content'), 'change_notes': 'fixed typo'},
        )
        self.assertEqual(resp.status_code, 302)

        self.document.refresh_from_db()
        self.assertEqual(self.document.version_number, 2)
        self.assertEqual(self.document.uploaded_by, self.chair)

        versions = list(DocumentVersion.objects.filter(document=self.document))
        self.assertEqual(len(versions), 1)
        version = versions[0]
        self.assertEqual(version.version_number, 1)
        self.assertEqual(version.change_notes, 'fixed typo')
        # The archived version keeps pointing at the ORIGINAL file, not the
        # new one — see the comment in committee_replace_document about why
        # this is a repointing, not a copy.
        self.assertEqual(version.file.name, original_name)
        self.assertNotEqual(self.document.document.name, original_name)

    def test_non_chair_cannot_replace(self):
        self._enable_flag(True)
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse('committee_replace_document', args=[self.committee.code, self.document.id]),
            {'file': _pdf('v2.pdf', b'new content')},
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.version_number, 1)
        self.assertEqual(DocumentVersion.objects.filter(document=self.document).count(), 0)

    def test_missing_file_is_a_no_op(self):
        self._enable_flag(True)
        self.client.force_login(self.chair)
        resp = self.client.post(
            reverse('committee_replace_document', args=[self.committee.code, self.document.id]),
            {'change_notes': 'no file attached'},
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.version_number, 1)

    def test_two_replacements_keep_both_versions_in_order(self):
        self._enable_flag(True)
        self.client.force_login(self.chair)
        self.client.post(
            reverse('committee_replace_document', args=[self.committee.code, self.document.id]),
            {'file': _pdf('v2.pdf', b'v2 content'), 'change_notes': 'first replace'},
        )
        self.client.post(
            reverse('committee_replace_document', args=[self.committee.code, self.document.id]),
            {'file': _pdf('v3.pdf', b'v3 content'), 'change_notes': 'second replace'},
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.version_number, 3)
        versions = list(DocumentVersion.objects.filter(document=self.document).order_by('version_number'))
        self.assertEqual([v.version_number for v in versions], [1, 2])
        self.assertEqual([v.change_notes for v in versions], ['first replace', 'second replace'])


class DownloadCommitteeDocumentVersionTests(TestCase):
    def setUp(self):
        self.committee = make_committee()
        self.chair = make_user('chair2', member_type='Officer')
        self.outsider = make_user('outsider1')
        self.committee.chairs.add(self.chair)
        self.committee.members.add(self.chair)

        self.document = CommitteeDocument.objects.create(
            committee=self.committee,
            title='Policy',
            document=_pdf(),
            uploaded_by=self.chair,
            visibility='committee_only',
        )
        self.version = DocumentVersion.objects.create(
            document=self.document,
            version_number=1,
            file=_pdf('archived.pdf', b'archived content'),
            uploaded_by=self.chair,
        )
        self.client = Client()

    def _enable_flag(self, enabled=True):
        FeatureFlag.objects.update_or_create(
            name='document_versioning',
            defaults={'display_name': 'Document Versioning', 'is_enabled': enabled},
        )

    def test_flag_disabled_blocks_download(self):
        self._enable_flag(False)
        self.client.force_login(self.chair)
        resp = self.client.get(reverse(
            'download_committee_document_version',
            args=[self.committee.code, self.document.id, self.version.id],
        ))
        self.assertEqual(resp.status_code, 403)

    def test_non_member_cannot_download(self):
        self._enable_flag(True)
        self.client.force_login(self.outsider)
        resp = self.client.get(reverse(
            'download_committee_document_version',
            args=[self.committee.code, self.document.id, self.version.id],
        ))
        self.assertEqual(resp.status_code, 403)

    def test_committee_member_can_download(self):
        self._enable_flag(True)
        self.client.force_login(self.chair)
        resp = self.client.get(reverse(
            'download_committee_document_version',
            args=[self.committee.code, self.document.id, self.version.id],
        ))
        self.assertEqual(resp.status_code, 200)
