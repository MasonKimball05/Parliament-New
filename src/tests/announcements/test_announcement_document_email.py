"""
Mason: "Do documents attached to announcements show in emails? If not can
you make it so it shows that there is a document attached, the name, and a
hyperlink to open it?"

They didn't. `emails/announcement_notification.html` never rendered
anything about `Announcement.linked_documents` (a M2M to `CommitteeDocument`)
— `send_announcement_notification` (the publish-triggered task path) and
`warmup_announcement_email` (the manual-send path behind the officer's
confirm-email page) both built a context dict with `announcement`/
`site_url`/`tracking_url`/`user`/the poll keys, and nothing about linked
documents, even though the in-app announcements list has always shown a
linked document as a clickable chip right on the same post
(`templates/announcements.html`).

Same shape as the poll-email fix (`test_announcement_poll_email.py`): both
real send paths independently render the same template with their own
context dict, so both are covered here — fixing one and not the other
would leave the bug alive on whichever path Mason doesn't happen to test.
Also same N+1 avoidance as the poll fetch: `linked_documents` is fetched
once per announcement (`list(announcement.linked_documents.all())`)
outside the per-recipient loop in both view functions, not once per
recipient.
"""
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from src.models import Announcement, CommitteeDocument, ParliamentUser


def make_officer(uid='doc-email-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Doc Officer', username=uid, member_type='Officer',
        password='testpass123', email='officer@example.com', member_status='Active',
    )


def make_member(uid='doc-email-member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Doc Member', username=uid, member_type='Member',
        password='testpass123', email='member@example.com', member_status='Active',
    )


def _pdf(name='handbook.pdf', label=b'original'):
    """A minimal but structurally real PDF — see
    `test_document_versioning.py`'s `_pdf()` for why a bare string payload
    isn't reliable against libmagic's content-type sniff."""
    content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% ' + label + b'\ntrailer\n%%EOF\n'
    return SimpleUploadedFile(name, content, content_type='application/pdf')


def make_document(officer, title='Chapter Handbook', filename='handbook.pdf'):
    return CommitteeDocument.objects.create(
        title=title, document=_pdf(filename), uploaded_by=officer,
        published_to_chapter=True, visibility='all_members',
    )


def make_announcement_with_document(officer, *docs):
    announcement = Announcement.objects.create(
        title='New Chapter Handbook', content='Please review the attached document.',
        posted_by=officer,
    )
    if docs:
        announcement.linked_documents.set(docs)
    return announcement


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SendAnnouncementNotificationDocumentTests(TestCase):
    """The publish-triggered send path: src.notifications.send_announcement_notification."""

    def setUp(self):
        self.officer = make_officer()
        self.member = make_member()
        mail.outbox = []

    def test_email_with_document_shows_name_and_download_link(self):
        from src.notifications import send_announcement_notification
        doc = make_document(self.officer)
        announcement = make_announcement_with_document(self.officer, doc)

        send_announcement_notification(announcement, initiated_by=self.officer)

        self.assertEqual(len(mail.outbox), 2)
        html = mail.outbox[0].alternatives[0][0]

        self.assertIn('Chapter Handbook', html)
        self.assertIn(reverse('download_chapter_document', args=[doc.id]), html)
        self.assertIn('Attached Document', html)

    def test_multiple_documents_are_all_listed(self):
        from src.notifications import send_announcement_notification
        doc1 = make_document(self.officer, title='Handbook', filename='handbook.pdf')
        doc2 = make_document(self.officer, title='Bylaws', filename='bylaws.pdf')
        announcement = make_announcement_with_document(self.officer, doc1, doc2)

        send_announcement_notification(announcement, initiated_by=self.officer)

        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Handbook', html)
        self.assertIn('Bylaws', html)
        self.assertIn(reverse('download_chapter_document', args=[doc1.id]), html)
        self.assertIn(reverse('download_chapter_document', args=[doc2.id]), html)
        # Pluralized label with more than one document.
        self.assertIn('Attached Documents', html)

    def test_announcement_without_document_renders_no_documents_section(self):
        from src.notifications import send_announcement_notification
        announcement = make_announcement_with_document(self.officer)

        send_announcement_notification(announcement, initiated_by=self.officer)

        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('class="documents-info"', html)
        self.assertNotIn('Attached Document', html)


class WarmupAnnouncementEmailDocumentTests(TestCase):
    """
    The manual-send path: `warmup_announcement_email` pre-renders each
    recipient's email into cache; `send_announcement_emails` reads that
    cache and sends it verbatim. Testing the warmup render directly is what
    actually proves the officer-triggered "Send Emails" button (not just
    the auto-send-on-publish task) also carries the linked document.
    """

    def setUp(self):
        self.officer = make_officer('doc-email-officer-2')
        self.member = make_member('doc-email-member-2')
        cache.clear()

    def test_warmup_render_includes_document_name_and_link(self):
        doc = make_document(self.officer)
        announcement = make_announcement_with_document(self.officer, doc)
        self.client.login(username=self.officer.username, password='testpass123')

        response = self.client.post(
            reverse('warmup_announcement_email', args=[announcement.id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        cache_key = f'email_warmup_{announcement.id}'
        warmup_data = cache.get(cache_key)
        self.assertIsNotNone(warmup_data)

        rendered = warmup_data['rendered_emails'][self.member.user_id]['html']
        self.assertIn('Chapter Handbook', rendered)
        self.assertIn(reverse('download_chapter_document', args=[doc.id]), rendered)
