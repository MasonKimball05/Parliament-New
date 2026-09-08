"""
v3.29.32. `manage_chapter_documents` (the officer-facing "manage all
chapter documents" page) filtered on `committee=<the Committee row
flagged is_chapter_committee=True>` — a real Committee object used
elsewhere for meeting/attendance purposes. But `upload_chapter_document`
(the ONLY way to create a document through this feature) sets
`committee=None` for the default, no-committee-selected case — a
different concept entirely, "chapter-level" rather than "belongs to the
row named Chapter." Nothing the upload form creates by default ever
matched the manage page's filter, so a freshly uploaded draft was
invisible on the one page meant to show it — reported live 09-08-26 by
Mason after uploading two test documents ("Test", "Test 2") he then
couldn't find to delete.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, CommitteeDocument, ParliamentUser


def make_user(user_id, member_type='Member', **kwargs):
    defaults = dict(name=f'User {user_id}', username=f'user_{user_id}', member_type=member_type)
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=user_id, password='testpass123', **defaults)


def _pdf(name='doc.pdf', label=b'x'):
    content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% ' + label + b'\ntrailer\n%%EOF\n'
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class ManageChapterDocumentsVisibilityTests(TestCase):
    """
    Covers the actual gap: a chapter-level document (committee=None) — the
    normal case, and what every test document Mason created looks like —
    must appear on this page, published or not.
    """

    def setUp(self):
        self.officer = make_user('mcd-officer', member_type='Officer')
        self.client = Client()
        self.client.login(username='user_mcd-officer', password='testpass123')

    def _get(self):
        return self.client.get(reverse('manage_chapter_documents'))

    def test_an_unpublished_chapter_level_draft_appears(self):
        """
        The exact bug: a document created with no committee selected (the
        default on the upload form) must show up as a draft here.
        """
        doc = CommitteeDocument.objects.create(
            title='Test', document=_pdf('test.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        response = self._get()
        self.assertContains(response, 'Test')
        self.assertIn(doc, response.context['unpublished_docs'])

    def test_a_published_chapter_level_document_appears(self):
        doc = CommitteeDocument.objects.create(
            title='Published Chapter Doc', document=_pdf('pub.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=True,
        )
        response = self._get()
        self.assertContains(response, 'Published Chapter Doc')
        published_ids = [
            d.id for _, docs in response.context['folders_with_published_docs'] for d in docs
        ] + [d.id for d in response.context['uncategorized_published_docs']]
        self.assertIn(doc.id, published_ids)

    def test_total_documents_count_includes_chapter_level_drafts(self):
        """
        Regression guard for the count itself, not just presence in a list
        — `total_documents` is what the page's own header line reports.
        """
        CommitteeDocument.objects.create(
            title='Test', document=_pdf('t1.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        CommitteeDocument.objects.create(
            title='Test 2', document=_pdf('t2.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        response = self._get()
        self.assertEqual(response.context['total_documents'], 2)

    def test_a_document_published_from_another_committee_appears(self):
        """
        Mirrors the public chapter_documents page's own definition of
        "chapter document": published_to_chapter=True regardless of which
        committee owns the row.
        """
        committee = Committee.objects.create(name='Fundraising', code='FUND', is_active=True)
        doc = CommitteeDocument.objects.create(
            title='Pushed From Fundraising', document=_pdf('pushed.pdf'), uploaded_by=self.officer,
            committee=committee, published_to_chapter=True,
        )
        response = self._get()
        self.assertContains(response, 'Pushed From Fundraising')
        # Also confirms the committee label was added to the item template.
        self.assertContains(response, 'Fundraising')

    def test_an_unpublished_draft_belonging_to_another_committee_is_excluded(self):
        """
        Deliberate boundary: an unpublished OTHER-committee document is
        that committee's own private draft, not a chapter-wide one — it
        should not appear here (or count toward total_documents) until
        it's actually published.
        """
        committee = Committee.objects.create(name='Fundraising', code='FUND2', is_active=True)
        CommitteeDocument.objects.create(
            title='Fundraising Internal Draft', document=_pdf('internal.pdf'), uploaded_by=self.officer,
            committee=committee, published_to_chapter=False,
        )
        response = self._get()
        self.assertNotContains(response, 'Fundraising Internal Draft')
        self.assertEqual(response.context['total_documents'], 0)

    def test_a_document_explicitly_tied_to_the_chapter_committee_row_still_appears(self):
        """
        Back-compat: if a document really does carry the
        is_chapter_committee=True Committee's FK (the ORIGINAL filter),
        it must still show up — the fix widens the filter, it doesn't
        narrow it.
        """
        chapter_committee = Committee.objects.create(
            name='Chapter', code='CHAPTER', is_active=True, is_chapter_committee=True,
        )
        doc = CommitteeDocument.objects.create(
            title='Tied To Chapter Committee Row', document=_pdf('tied.pdf'), uploaded_by=self.officer,
            committee=chapter_committee, published_to_chapter=False,
        )
        response = self._get()
        self.assertContains(response, 'Tied To Chapter Committee Row')
        self.assertIn(doc, response.context['unpublished_docs'])

    def test_the_view_does_not_break_when_no_chapter_committee_row_exists(self):
        """
        Control for the try/except Committee.DoesNotExist branch — the
        original code fell back to CommitteeDocument.objects.none() here,
        which is exactly the over-broad version of the same bug. It must
        now still show chapter-level and published documents.
        """
        self.assertFalse(Committee.objects.filter(is_chapter_committee=True).exists())
        doc = CommitteeDocument.objects.create(
            title='No Chapter Committee Row', document=_pdf('none.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No Chapter Committee Row')
        self.assertIn(doc, response.context['unpublished_docs'])

    def test_a_non_officer_is_forbidden(self):
        member = make_user('mcd-member')
        client = Client()
        client.login(username='user_mcd-member', password='testpass123')
        response = client.get(reverse('manage_chapter_documents'))
        self.assertEqual(response.status_code, 403)


class ManageChapterDocumentQuickActionsTests(TestCase):
    """
    Confirms the "quick actions" Mason asked for already exist end to end
    once a document is actually visible — delete, and toggling publish via
    the update action — rather than needing to be built from scratch.
    """

    def setUp(self):
        self.officer = make_user('mcd-qa-officer', member_type='Officer')
        self.client = Client()
        self.client.login(username='user_mcd-qa-officer', password='testpass123')

    def test_the_manage_page_links_to_the_per_document_manage_view(self):
        doc = CommitteeDocument.objects.create(
            title='Test', document=_pdf('link.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        response = self.client.get(reverse('manage_chapter_documents'))
        self.assertContains(response, reverse('manage_chapter_document', args=[doc.id]))

    def test_a_chapter_level_draft_can_be_deleted_from_the_per_document_view(self):
        doc = CommitteeDocument.objects.create(
            title='Test', document=_pdf('del.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        response = self.client.post(
            reverse('manage_chapter_document', args=[doc.id]), {'action': 'delete'},
        )
        self.assertRedirects(response, reverse('manage_chapter_documents'))
        self.assertFalse(CommitteeDocument.objects.filter(id=doc.id).exists())

    def test_a_chapter_level_draft_can_be_published_from_the_per_document_view(self):
        doc = CommitteeDocument.objects.create(
            title='Test', document=_pdf('pub2.pdf'), uploaded_by=self.officer,
            committee=None, published_to_chapter=False,
        )
        response = self.client.post(
            reverse('manage_chapter_document', args=[doc.id]),
            {'action': 'update', 'title': doc.title, 'description': '', 'document_type': 'general',
             'committee': '', 'chapter_folder': '', 'published_to_chapter': 'true'},
        )
        self.assertRedirects(response, reverse('manage_chapter_documents'))
        doc.refresh_from_db()
        self.assertTrue(doc.published_to_chapter)
