from django import forms
from django.conf import settings
from .models import Legislation, Announcement, Event, CommitteeDocument, Committee
import magic  # python-magic for MIME type detection

class LegislationForm(forms.ModelForm):
    class Meta:
        model = Legislation
        fields = ['title', 'description', 'available_at', 'document', 'anonymous_vote', 'allow_abstain', 'required_percentage']

    def clean_document(self):
        file = self.cleaned_data.get('document')
        if file:
            # Check file extension
            if not file.name.lower().endswith(('.pdf', '.docx')):
                raise forms.ValidationError('Only PDF and DOCX files are allowed.')

            # Check file size (20 MB max)
            if file.size > 20 * 1024 * 1024:
                raise forms.ValidationError('File size must not exceed 20 MB.')

            # Check MIME type to prevent file extension spoofing
            try:
                mime = magic.from_buffer(file.read(2048), mime=True)
                file.seek(0)  # Reset file pointer

                allowed_mimes = [
                    'application/pdf',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                ]

                if mime not in allowed_mimes:
                    raise forms.ValidationError(
                        f'Invalid file type. Expected PDF or DOCX, but got {mime}.'
                    )
            except Exception as e:
                raise forms.ValidationError('Unable to verify file type. Please try again.')

        return file

class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'content', 'publish_at', 'event_date', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter announcement title'
            }),
            'content': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter announcement content',
                'rows': 5
            }),
            'publish_at': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'event_date': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            })
        }
        labels = {
            'title': 'Announcement Title',
            'content': 'Content',
            'publish_at': 'Publish Date & Time (Optional)',
            'event_date': 'Event Date (Optional)',
            'is_active': 'Active'
        }
        help_texts = {
            'publish_at': 'Schedule when this announcement should be published. Leave blank to publish immediately.',
            'event_date': 'If this announcement is for an event, specify the date and time',
            'is_active': 'Uncheck to hide this announcement from members'
        }

class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['title', 'description', 'date_time', 'location', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter event title'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter event description',
                'rows': 5
            }),
            'date_time': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'location': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter event location (e.g., Room 123, Zoom link, etc.)'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            })
        }
        labels = {
            'title': 'Event Title',
            'description': 'Description',
            'date_time': 'Date & Time',
            'location': 'Location',
            'is_active': 'Active'
        }
        help_texts = {
            'date_time': 'When the event will occur',
            'location': 'Physical location or virtual meeting link',
            'is_active': 'Uncheck to hide this event from the calendar'
        }

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
                'accept': '.pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt'
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
            allowed_extensions = ('.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt')
            if not file.name.lower().endswith(allowed_extensions):
                raise forms.ValidationError(
                    f'Only these file types are allowed: {", ".join(allowed_extensions)}'
                )

            # Check file size (20 MB max)
            if file.size > 20 * 1024 * 1024:
                raise forms.ValidationError('File size must not exceed 20 MB.')

            # Check MIME type to prevent file extension spoofing
            try:
                mime = magic.from_buffer(file.read(2048), mime=True)
                file.seek(0)  # Reset file pointer

                allowed_mimes = getattr(settings, 'ALLOWED_DOCUMENT_TYPES', [
                    'application/pdf',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'application/msword',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'application/vnd.ms-excel',
                    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    'application/vnd.ms-powerpoint',
                ])

                if mime not in allowed_mimes:
                    raise forms.ValidationError(
                        f'Invalid file type detected: {mime}. Please upload a valid document.'
                    )
            except Exception as e:
                # If MIME detection fails, reject the upload for security
                raise forms.ValidationError('Unable to verify file type. Please try again.')

        return file
