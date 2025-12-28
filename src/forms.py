from django import forms
from django.conf import settings
from .models import Legislation, Announcement, Event, CommitteeDocument, Committee, PassedResolution, ResolutionSectionImpact, KaiReport
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
    visible_to = forms.MultipleChoiceField(
        choices=Announcement.MEMBER_TYPES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
        }),
        help_text='Select which member types can see this announcement. Leave empty for all members.'
    )

    class Meta:
        model = Announcement
        fields = ['title', 'content', 'publish_at', 'event_date', 'visible_to', 'is_active']
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
            'visible_to': 'Visible To',
            'is_active': 'Active'
        }
        help_texts = {
            'publish_at': 'Schedule when this announcement should be published. Leave blank to publish immediately.',
            'event_date': 'If this announcement is for an event, specify the date and time',
            'is_active': 'Uncheck to hide this announcement from members'
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Convert the list from MultipleChoiceField to JSON for storage
        instance.visible_to = self.cleaned_data.get('visible_to') or None
        if commit:
            instance.save()
        return instance

class EventForm(forms.ModelForm):
    visible_to = forms.MultipleChoiceField(
        choices=Event.MEMBER_TYPES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
        }),
        help_text='Select which member types can see this event. Leave empty for all members.'
    )

    class Meta:
        model = Event
        fields = ['title', 'description', 'date_time', 'location', 'visible_to', 'is_active']
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
            'visible_to': 'Visible To',
            'is_active': 'Active'
        }
        help_texts = {
            'date_time': 'When the event will occur',
            'location': 'Physical location or virtual meeting link',
            'is_active': 'Uncheck to hide this event from the calendar'
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Convert the list from MultipleChoiceField to JSON for storage
        instance.visible_to = self.cleaned_data.get('visible_to') or None
        if commit:
            instance.save()
        return instance

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


class ForcedPasswordChangeForm(forms.Form):
    """Form for users who must change their password"""
    old_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500',
            'placeholder': 'Current password'
        }),
        label='Current Password'
    )
    new_password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500',
            'placeholder': 'New password'
        }),
        label='New Password',
        help_text='Password must be at least 9 characters with uppercase, lowercase, number, and special character.'
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500',
            'placeholder': 'Confirm new password'
        }),
        label='Confirm New Password'
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        """Verify the old password is correct"""
        old_password = self.cleaned_data.get('old_password')
        if not self.user.check_password(old_password):
            raise forms.ValidationError('Your current password is incorrect.')
        return old_password

    def clean_new_password2(self):
        """Verify the two password fields match"""
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('The two password fields must match.')
        return password2

    def save(self):
        """Save the new password and clear the force_password_change flag"""
        from django.contrib.auth.password_validation import validate_password
        password = self.cleaned_data['new_password1']

        # Validate password against Django's password validators
        validate_password(password, self.user)

        self.user.set_password(password)
        self.user.force_password_change = False
        self.user.save()
        return self.user


class PassedResolutionForm(forms.ModelForm):
    """Form for creating and editing passed resolutions"""
    class Meta:
        model = PassedResolution
        fields = ['title', 'description', 'date_passed', 'legislation', 'document', 'border_color', 'impact_summary', 'display_order', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter resolution title'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Brief description of what this resolution does',
                'rows': 4
            }),
            'date_passed': forms.DateInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'date'
            }),
            'legislation': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'document': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'accept': '.pdf,.docx'
            }),
            'border_color': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'impact_summary': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Brief summary of sections impacted (displayed in the card)',
                'rows': 3
            }),
            'display_order': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': '0'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            })
        }
        labels = {
            'title': 'Resolution Title',
            'description': 'Description',
            'date_passed': 'Date Passed',
            'legislation': 'Link to Legislation',
            'document': 'Upload Document',
            'border_color': 'Card Border Color',
            'impact_summary': 'Impact Summary',
            'display_order': 'Display Order',
            'is_active': 'Active'
        }
        help_texts = {
            'title': 'The title of the resolution as it will appear on the page',
            'description': 'A brief description of what this resolution accomplishes',
            'date_passed': 'The date this resolution was officially passed',
            'legislation': 'Optional: Link to the legislation record in the system',
            'document': 'Optional: Upload the resolution document if not linked to legislation',
            'border_color': 'The color of the border on the resolution card',
            'impact_summary': 'Brief text shown at the bottom of the card explaining the impact',
            'display_order': 'Lower numbers appear first. Use 0 for most recent.',
            'is_active': 'Uncheck to hide this resolution from the page'
        }

    def clean(self):
        """Ensure either legislation or document is provided"""
        cleaned_data = super().clean()
        legislation = cleaned_data.get('legislation')
        document = cleaned_data.get('document')

        # Allow editing without providing a new document
        if not legislation and not document and not self.instance.pk:
            raise forms.ValidationError('You must either link to existing legislation or upload a document.')

        return cleaned_data


class ResolutionSectionImpactForm(forms.ModelForm):
    """Form for adding section impacts to a resolution"""
    class Meta:
        model = ResolutionSectionImpact
        fields = ['section_name', 'section_type', 'section_anchor', 'external_url', 'display_order']
        widgets = {
            'section_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'e.g., "Constitution Article III (Leadership)"'
            }),
            'section_type': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'section_anchor': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': '#const-leadership'
            }),
            'external_url': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Full URL to another page'
            }),
            'display_order': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': '0'
            })
        }
        labels = {
            'section_name': 'Section Display Name',
            'section_type': 'Section Type',
            'section_anchor': 'Section Anchor',
            'external_url': 'External URL',
            'display_order': 'Display Order'
        }
        help_texts = {
            'section_name': 'The text that will appear on the tag',
            'section_type': 'Type of section (affects tag color)',
            'section_anchor': 'URL fragment to jump to a specific section (e.g., #const-leadership)',
            'external_url': 'Full URL to another page (use this OR section anchor, not both)',
            'display_order': 'Order to display tags (lower numbers first)'
        }


class KaiReportForm(forms.ModelForm):
    """Form for submitting Kai reports"""
    class Meta:
        model = KaiReport
        fields = ['title', 'category', 'description', 'targeted_to', 'attachment', 'tags']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter a brief title for your report'
            }),
            'category': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Provide detailed information about your report...',
                'rows': 6
            }),
            'targeted_to': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'attachment': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'accept': '.pdf,.docx,.doc,.xlsx,.xls,.jpg,.jpeg,.png'
            }),
            'tags': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'academic, urgent, follow-up, etc.'
            })
        }
        labels = {
            'title': 'Report Title',
            'category': 'Category',
            'description': 'Description',
            'targeted_to': 'Directed To (Optional)',
            'attachment': 'Attachment (Optional)',
            'tags': 'Tags (Optional)'
        }
        help_texts = {
            'title': 'A brief, descriptive title for your report',
            'description': 'Provide all relevant details about what you\'re reporting',
            'targeted_to': 'Optionally select a specific person this report is directed to',
            'attachment': 'Upload supporting documents, images, or files (max 20MB)',
            'tags': 'Add comma-separated tags to help categorize your report'
        }

    def clean_attachment(self):
        """Validate uploaded attachment for security"""
        file = self.cleaned_data.get('attachment')
        if file:
            # Allowed extensions
            allowed_extensions = ('.pdf', '.docx', '.doc', '.xlsx', '.xls', '.jpg', '.jpeg', '.png')
            if not file.name.lower().endswith(allowed_extensions):
                raise forms.ValidationError(
                    f'Only these file types are allowed: {", ".join(allowed_extensions)}'
                )

            # Check file size (20 MB max)
            if file.size > 20 * 1024 * 1024:
                raise forms.ValidationError('File size must not exceed 20 MB.')

            # Check MIME type
            try:
                mime = magic.from_buffer(file.read(2048), mime=True)
                file.seek(0)  # Reset file pointer

                allowed_mimes = [
                    'application/pdf',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    'application/msword',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'application/vnd.ms-excel',
                    'image/jpeg',
                    'image/png',
                ]

                if mime not in allowed_mimes:
                    raise forms.ValidationError(
                        f'Invalid file type detected: {mime}. Please upload a valid file.'
                    )
            except Exception as e:
                raise forms.ValidationError('Unable to verify file type. Please try again.')

        return file
