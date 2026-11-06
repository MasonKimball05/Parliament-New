"""
11-06-26: committee documents (and officer reports), chapter bills and bill
drafts checked a file's type with `magic.from_buffer(first 2 KB)`, which can't
name a Word file the way Office writes it (the zip's first entry isn't
[Content_Types].xml, so libmagic says "zip"). Real .docx uploads were refused.
They now use the shared validator (src/utils/file_validation.py), which reads
the container, as the Kai form has since v4.4.2.

    python manage.py test src.tests.security.test_office_document_uploads
"""
import io
import zipfile

import magic
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from src.forms.documents import CommitteeDocumentForm
from src.forms.legislation import LegislationDraftForm, LegislationForm

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
    'wordprocessingml.document.main+xml"/></Types>')


def office_docx():
    """A .docx laid out as Word saves it: other parts first, [Content_Types].xml later."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        # Stored, not compressed: real Office parts push the rest past 2 KB.
        z.writestr('_rels/.rels', '<Relationships/>' + ' ' * 3000, compress_type=zipfile.ZIP_STORED)
        z.writestr('docProps/app.xml', '<Properties/>' + ' ' * 3000, compress_type=zipfile.ZIP_STORED)
        z.writestr('word/document.xml', '<w:document/>')
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
    return buf.getvalue()


def fake_docx():
    """A .docx that is really a plain zip of something else."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('payload.sh', 'echo hi')
    return buf.getvalue()


class OfficeDocxTests(SimpleTestCase):

    def check(self, form_cls, data, ok):
        form = form_cls()
        form.cleaned_data = {'document': SimpleUploadedFile('bill.docx', data)}
        if ok:
            self.assertTrue(form.clean_document())
        else:
            from django import forms
            with self.assertRaises(forms.ValidationError):
                form.clean_document()

    def test_libmagic_alone_cant_name_it(self):
        self.assertNotIn('wordprocessingml', magic.from_buffer(office_docx()[:2048], mime=True))

    def test_a_real_word_file_is_accepted_everywhere(self):
        for form_cls in (CommitteeDocumentForm, LegislationForm, LegislationDraftForm):
            with self.subTest(form=form_cls.__name__):
                self.check(form_cls, office_docx(), ok=True)

    def test_a_zip_pretending_to_be_word_is_refused(self):
        for form_cls in (CommitteeDocumentForm, LegislationForm, LegislationDraftForm):
            with self.subTest(form=form_cls.__name__):
                self.check(form_cls, fake_docx(), ok=False)
