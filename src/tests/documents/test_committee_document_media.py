"""
v4.4.15 — /media/ applies a committee document's own visibility rule (pentest F03).

Before this, `committee_documents/` and `document_versions/` were in
PUBLIC_MEDIA_PREFIXES, so `/media/committee_documents/<name>` returned a
"Committee Members Only" (or Chairs/Officers/Custom) document to any member or
pledge who knew the file name. The listing hid it; the file did not.
"""
import os
import tempfile

from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import Committee, ParliamentUser
from src.models.documents import CommitteeDocument, DocumentVersion

_TMP_MEDIA = tempfile.mkdtemp(prefix='parliament-test-cdoc-media-')


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class CommitteeDocumentMediaTests(TestCase):
    def setUp(self):
        mk = ParliamentUser.objects.create_user
        self.uploader = mk('cdm1', 'Uploader', 'cdm_up', 'Member')
        self.member = mk('cdm2', 'On Committee', 'cdm_member', 'Member')
        self.outsider = mk('cdm3', 'Not On Committee', 'cdm_out', 'Member')
        self.pledge = mk('cdm4', 'Pledge', 'cdm_pledge', 'Pledge')
        self.chair = mk('cdm5', 'Chair', 'cdm_chair', 'Member')
        self.committee = Committee.objects.create(name='Media Test Committee', code='MEDIATEST')
        self.committee.members.add(self.uploader, self.member, self.chair)
        self.committee.chairs.add(self.chair)

    def _doc(self, visibility='committee_only', **kw):
        doc = CommitteeDocument(committee=self.committee, title='Doc', uploaded_by=self.uploader,
                                visibility=visibility, **kw)
        doc.document.save(f'{visibility}.txt', ContentFile(b'secret body'), save=False)
        doc.save()
        return doc

    def _get(self, user, name):
        c = Client()
        c.force_login(user)
        return c.get(reverse('serve_media', kwargs={'path': name}))

    def assertServes(self, user, name):
        resp = self._get(user, name)
        self.assertEqual(resp.status_code, 200, f'{user.username} should get {name}')
        self.assertEqual(b''.join(resp.streaming_content), b'secret body')

    def assertRefuses(self, user, name):
        self.assertEqual(self._get(user, name).status_code, 404, f'{user.username} must not get {name}')

    def test_committee_only_document(self):
        """THE regression test for F03."""
        name = self._doc('committee_only').document.name
        self.assertServes(self.member, name)
        self.assertServes(self.uploader, name)
        self.assertRefuses(self.outsider, name)
        self.assertRefuses(self.pledge, name)

    def test_chairs_only_document(self):
        name = self._doc('chairs_only').document.name
        self.assertServes(self.chair, name)
        self.assertServes(self.uploader, name)  # the uploader can always view
        self.assertRefuses(self.member, name)
        self.assertRefuses(self.outsider, name)

    def test_custom_document(self):
        doc = self._doc('custom')
        doc.custom_viewers.add(self.outsider)
        self.assertServes(self.outsider, doc.document.name)
        self.assertRefuses(self.member, doc.document.name)

    def test_all_members_and_published_documents_stay_open(self):
        self.assertServes(self.pledge, self._doc('all_members').document.name)
        self.assertServes(self.outsider, self._doc('chairs_only', published_to_chapter=True).document.name)

    def test_version_files_follow_their_document(self):
        doc = self._doc('committee_only')
        v = DocumentVersion(document=doc, version_number=1, uploaded_by=self.uploader)
        v.file.save('old.txt', ContentFile(b'secret body'), save=False)
        v.save()
        self.assertServes(self.member, v.file.name)
        self.assertRefuses(self.outsider, v.file.name)

    def test_orphan_file_is_refused(self):
        """A file with no owning row has no rule that could grant it."""
        os.makedirs(os.path.join(_TMP_MEDIA, 'committee_documents'), exist_ok=True)
        with open(os.path.join(_TMP_MEDIA, 'committee_documents', 'orphan.txt'), 'wb') as f:
            f.write(b'secret body')
        self.assertRefuses(self.member, 'committee_documents/orphan.txt')

    def test_traversal_into_the_folder_is_still_checked(self):
        name = self._doc('committee_only').document.name
        sneaky = 'legislation_docs/../' + name
        self.assertRefuses(self.outsider, sneaky)


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class MediaDeniesUnclassifiedFoldersTests(TestCase):
    """v4.4.15 (attack-surface item 7): /media/ serves only folders classified on purpose."""

    def setUp(self):
        self.member = ParliamentUser.objects.create_user('udm1', 'Member', 'udm1', 'Member')
        for rel in ('new_feature_uploads/x.txt', 'stray.txt', 'legislation_docs/ok.txt'):
            path = os.path.join(_TMP_MEDIA, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'body')

    def _get(self, name):
        c = Client()
        c.force_login(self.member)
        return c.get(reverse('serve_media', kwargs={'path': name}))

    def test_an_unclassified_folder_is_refused(self):
        self.assertEqual(self._get('new_feature_uploads/x.txt').status_code, 404)

    def test_a_file_at_the_root_is_refused(self):
        self.assertEqual(self._get('stray.txt').status_code, 404)

    def test_a_public_folder_is_still_served(self):
        self.assertEqual(self._get('legislation_docs/ok.txt').status_code, 200)
