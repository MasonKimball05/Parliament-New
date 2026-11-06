from django import forms
from django.conf import settings

from src.models import CommitteeDocument


class CommitteeDocumentForm(forms.ModelForm):
    class Meta:
        model = CommitteeDocument
        fields = ['committee', 'title', 'document', 'description', 'document_type', 'meeting_date', 'published_to_chapter']
        widgets = {
            'committee': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter document title'
            }),
            'document': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'accept': '.pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.txt,.md,.rtf,.csv,.log,.json,.xml'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter document description (optional)',
                'rows': 4
            }),
            'document_type': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'meeting_date': forms.DateInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'date'
            }),
            'published_to_chapter': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            })
        }
        labels = {
            'committee': 'Committee',
            'title': 'Document Title',
            'document': 'Upload Document',
            'description': 'Description',
            'document_type': 'Document Type',
            'meeting_date': 'Meeting Date',
            'published_to_chapter': 'Publish to Chapter'
        }
        help_texts = {
            'committee': 'Select the committee this document belongs to',
            'description': 'Optional: Provide additional details about this document',
            'document_type': 'Select the type of document you are uploading',
            'meeting_date': 'For minutes and agendas, specify the meeting date',
            'published_to_chapter': 'Check to make this document visible to all chapter members'
        }

    def clean_document(self):
        """Validate uploaded committee documents for security"""
        file = self.cleaned_data.get('document')
        if file:
            # Allowed extensions
            allowed_extensions = (
                '.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt',
                '.txt', '.md', '.rtf', '.csv', '.log', '.json', '.xml',
            )
            if not file.name.lower().endswith(allowed_extensions):
                raise forms.ValidationError(
                    f'Only these file types are allowed: {", ".join(allowed_extensions)}'
                )

            # Check file size (20 MB max)
            if file.size > 20 * 1024 * 1024:
                raise forms.ValidationError('File size must not exceed 20 MB.')

            # 11-06-26: the shared check (src/utils/file_validation.py), as the Kai
            # form uses since v4.4.2. `magic.from_buffer(first 2 KB)` can't name a
            # Word or Excel file saved by Office (libmagic says zip or octet-stream),
            # so real .docx/.xlsx uploads were refused. The extension list above
            # still narrows what's allowed; this checks the content matches it.
            from django.core.files.uploadedfile import UploadedFile
            from src.utils.file_validation import validate_uploaded_file
            try:
                # Only a new upload: a file already stored was checked when it came in.
                if isinstance(file, UploadedFile):
                    validate_uploaded_file(file)
            except forms.ValidationError:
                raise
            except Exception:
                raise forms.ValidationError('Unable to verify file type. Please try again.')

        return file
