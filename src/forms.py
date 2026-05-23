from django import forms
from django.conf import settings
from .models import (
    Legislation, Announcement, Event, CommitteeDocument, Committee,
    PassedResolution, ResolutionSectionImpact, KaiReport, UserPreferences,
    ParliamentUser, Role, ServicePeriod, ServiceMemberExpectation, ServiceHoursSubmission,
    Song, SongCategory
)
import magic  # python-magic for MIME type detection
import logging

logger = logging.getLogger(__name__)

class LegislationForm(forms.ModelForm):
    class Meta:
        model = Legislation
        fields = ['title', 'description', 'available_at', 'voting_starts_at', 'voting_ends_at', 'document', 'anonymous_vote', 'allow_abstain', 'required_percentage']
        widgets = {
            'available_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'voting_starts_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'voting_ends_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
        }
        help_texts = {
            'available_at': 'When the document becomes visible for review.',
            'voting_starts_at': 'Optional: When voting opens. Leave blank to start voting when document is available.',
            'voting_ends_at': 'Optional: Voting will automatically close at this time. Leave blank for manual close only.',
            'document': 'Optional if you provide a detailed description (20+ characters).',
        }

    def clean(self):
        cleaned_data = super().clean()
        document = cleaned_data.get('document')
        description = cleaned_data.get('description', '').strip()

        # Require either a document OR a meaningful description (at least 20 characters)
        if not document and len(description) < 20:
            raise forms.ValidationError(
                'Please either upload a document OR provide a detailed description (at least 20 characters).'
            )

        return cleaned_data

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
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded dark:bg-gray-700'
        }),
        help_text='Select which member types can see this announcement. Leave empty for all members.'
    )

    class Meta:
        model = Announcement
        fields = ['title', 'content', 'publish_at', 'event_date', 'visible_to', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter announcement title'
            }),
            'content': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter announcement content',
                'rows': 5
            }),
            'publish_at': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'event_date': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded dark:bg-gray-700'
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
        visible_to_raw = self.cleaned_data.get('visible_to')
        instance.visible_to = visible_to_raw or None

        # Log the visibility settings for debugging
        logger.info(f"[ANNOUNCEMENT FORM] Saving announcement '{instance.title}'")
        logger.info(f"[ANNOUNCEMENT FORM] Raw form data visible_to: {visible_to_raw}")
        logger.info(f"[ANNOUNCEMENT FORM] Stored visible_to: {instance.visible_to}")

        if commit:
            instance.save()
            logger.info(f"[ANNOUNCEMENT FORM] Saved announcement ID: {instance.id}, visible_to: {instance.visible_to}")
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

    recurrence_days = forms.MultipleChoiceField(
        choices=[
            (0, 'Monday'),
            (1, 'Tuesday'),
            (2, 'Wednesday'),
            (3, 'Thursday'),
            (4, 'Friday'),
            (5, 'Saturday'),
            (6, 'Sunday'),
        ],
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
        }),
        help_text='Select which days of the week for weekly recurring events'
    )

    class Meta:
        model = Event
        fields = ['title', 'description', 'date_time', 'location', 'visible_to', 'is_active',
                  'requires_attendance', 'allow_excuses', 'excuse_deadline',
                  'is_recurring', 'recurrence_type', 'recurrence_interval', 'recurrence_unit',
                  'recurrence_days', 'recurrence_end_date']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter event title'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter event description',
                'rows': 5
            }),
            'date_time': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'location': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter event location (e.g., Room 123, Zoom link, etc.)'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'requires_attendance': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'allow_excuses': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'excuse_deadline': forms.DateTimeInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'datetime-local'
            }),
            'is_recurring': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded',
                'id': 'is_recurring'
            }),
            'recurrence_type': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'id': 'recurrence_type'
            }),
            'recurrence_interval': forms.NumberInput(attrs={
                'class': 'w-20 px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'min': 1,
                'max': 99
            }),
            'recurrence_unit': forms.Select(attrs={
                'class': 'px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'recurrence_end_date': forms.DateInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'type': 'date'
            })
        }
        labels = {
            'title': 'Event Title',
            'description': 'Description',
            'date_time': 'Date & Time',
            'location': 'Location',
            'visible_to': 'Visible To',
            'is_active': 'Active',
            'requires_attendance': 'Requires Attendance',
            'allow_excuses': 'Allow Excuse Requests',
            'excuse_deadline': 'Excuse Deadline (Optional)',
            'is_recurring': 'Repeating Event',
            'recurrence_type': 'Repeat Frequency',
            'recurrence_interval': 'Every',
            'recurrence_unit': '',
            'recurrence_days': 'Repeat on Days',
            'recurrence_end_date': 'End Date (Optional)'
        }
        help_texts = {
            'date_time': 'When the event will occur',
            'location': 'Physical location or virtual meeting link',
            'is_active': 'Uncheck to hide this event from the calendar',
            'requires_attendance': 'Check if this event requires attendance tracking',
            'allow_excuses': 'Allow members to submit excuse requests for this event',
            'excuse_deadline': 'Deadline for submitting excuses. Leave blank to use event time as deadline.',
            'is_recurring': 'Check if this event repeats',
            'recurrence_type': 'How often the event repeats',
            'recurrence_end_date': 'Leave blank for indefinite recurrence'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Convert JSON field to list for form display
        if self.instance and self.instance.pk and self.instance.recurrence_days:
            self.initial['recurrence_days'] = [str(d) for d in self.instance.recurrence_days]

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Convert the list from MultipleChoiceField to JSON for storage
        instance.visible_to = self.cleaned_data.get('visible_to') or None
        # Convert recurrence_days to list of integers
        recurrence_days = self.cleaned_data.get('recurrence_days')
        if recurrence_days:
            instance.recurrence_days = [int(d) for d in recurrence_days]
        else:
            instance.recurrence_days = None
        # Set is_recurring based on recurrence_type
        instance.is_recurring = instance.recurrence_type != 'none'
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
                    'text/plain',
                    'text/markdown',
                    'text/csv',
                    'application/csv',
                    'application/rtf',
                    'text/rtf',
                    'text/x-log',
                    'application/json',
                    'application/xml',
                    'text/xml',
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
        self.user.has_default_password = False
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
    tags = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
            'placeholder': 'academic, urgent, follow-up, etc.'
        }),
        label='Tags (Optional)',
        help_text='Add comma-separated tags to help categorize your report'
    )

    class Meta:
        model = KaiReport
        fields = ['title', 'category', 'description', 'targeted_to', 'attachment', 'tags']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
                'placeholder': 'Enter a brief title for your report'
            }),
            'category': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
                'placeholder': 'Provide detailed information about your report...',
                'rows': 6
            }),
            'targeted_to': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200'
            }),
            'attachment': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
                'accept': '.pdf,.docx,.doc,.xlsx,.xls,.jpg,.jpeg,.png'
            }),
        }
        labels = {
            'title': 'Report Title',
            'category': 'Category',
            'description': 'Description',
            'targeted_to': 'Directed To (Optional)',
            'attachment': 'Attachment (Optional)',
        }
        help_texts = {
            'title': 'A brief, descriptive title for your report',
            'description': 'Provide all relevant details about what you\'re reporting',
            'targeted_to': 'Optionally select a specific person this report is directed to',
            'attachment': 'Upload supporting documents, images, or files (max 20MB)',
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

    def clean_tags(self):
        tags_str = self.cleaned_data.get('tags', '')
        if not tags_str:
            return []
        return [t.strip() for t in tags_str.split(',') if t.strip()]


class UserPreferencesForm(forms.Form):
    """
    Form for users to update their preferences.

    Accepts an ``instance`` kwarg (a UserPreferences object) for compatibility
    with the existing preferences_view, which calls:
        UserPreferencesForm(request.POST, instance=preferences)
        UserPreferencesForm(instance=preferences)
    The ``save()`` method writes cleaned data back into instance.prefs and saves.
    """
    _CB = "h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
    _CB_MENU = "h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 menu-checkbox"

    # Theme
    theme = forms.ChoiceField(
        choices=UserPreferences.THEME_CHOICES,
        label="Color Theme",
        widget=forms.Select(attrs={
            "class": "mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm focus:border-primary-500 focus:ring-primary-500"
        }),
    )

    # Email notifications
    email_announcements = forms.BooleanField(required=False, label="Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    email_legislation = forms.BooleanField(required=False, label="New Legislation",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    email_events = forms.BooleanField(required=False, label="Upcoming Events",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    email_committee_updates = forms.BooleanField(required=False, label="Committee Updates",
        widget=forms.CheckboxInput(attrs={"class": _CB}))

    # Display
    show_announcement_popups = forms.BooleanField(required=False, label="Show announcement popups",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    compact_view = forms.BooleanField(required=False, label="Compact view mode",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    home_layout = forms.ChoiceField(
        choices=[('modern', 'Modern (default)'), ('classic', 'Classic')],
        label="Home Page Layout",
        widget=forms.Select(attrs={
            "class": "mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm focus:border-primary-500 focus:ring-primary-500"
        }),
    )
    landing_page = forms.ChoiceField(
        choices=[
            ('home', 'Home'),
            ('announcements', 'Announcements'),
            ('calendar', 'Calendar'),
            ('vote', 'Vote'),
        ],
        label="Landing Page After Login",
        widget=forms.Select(attrs={
            "class": "mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm focus:border-primary-500 focus:ring-primary-500"
        }),
    )

    # In-app notifications
    notify_announcements = forms.BooleanField(required=False, label="Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    notify_legislation = forms.BooleanField(required=False, label="Legislation & Voting",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    notify_events = forms.BooleanField(required=False, label="Events",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    notify_slating = forms.BooleanField(required=False, label="Officer Elections (Slating)",
        widget=forms.CheckboxInput(attrs={"class": _CB}))

    # Menu
    show_vote_menu = forms.BooleanField(required=False, label="Show Vote",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_committees_menu = forms.BooleanField(required=False, label="Show Committees",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_chats_menu = forms.BooleanField(required=False, label="Show Chats",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_documents_menu = forms.BooleanField(required=False, label="Show Documents",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_announcements_menu = forms.BooleanField(required=False, label="Show Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_calendar_menu = forms.BooleanField(required=False, label="Show Calendar",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_legislation_menu = forms.BooleanField(required=False, label="Show Legislation",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_excuses_menu = forms.BooleanField(required=False, label="Show My Excuses",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_search_menu = forms.BooleanField(required=False, label="Show Search",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_roberts_rules_menu = forms.BooleanField(required=False, label="Show Robert's Rules",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))

    def __init__(self, data=None, instance=None, **kwargs):
        self._instance = instance
        if instance is not None and data is None:
            kwargs['initial'] = {
                'theme': instance.theme,
                'email_announcements': instance.email_announcements,
                'email_legislation': instance.email_legislation,
                'email_events': instance.email_events,
                'email_committee_updates': instance.email_committee_updates,
                'show_announcement_popups': instance.show_announcement_popups,
                'compact_view': instance.compact_view,
                'home_layout': instance.home_layout,
                'landing_page': instance.landing_page,
                'notify_announcements': instance.notify_announcements,
                'notify_legislation': instance.notify_legislation,
                'notify_events': instance.notify_events,
                'notify_slating': instance.notify_slating,
                'show_vote_menu': instance.show_vote_menu,
                'show_committees_menu': instance.show_committees_menu,
                'show_chats_menu': instance.show_chats_menu,
                'show_documents_menu': instance.show_documents_menu,
                'show_announcements_menu': instance.show_announcements_menu,
                'show_calendar_menu': instance.show_calendar_menu,
                'show_legislation_menu': instance.show_legislation_menu,
                'show_excuses_menu': instance.show_excuses_menu,
                'show_search_menu': instance.show_search_menu,
                'show_roberts_rules_menu': instance.show_roberts_rules_menu,
            }
        super().__init__(data, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        menu_fields = [
            'show_vote_menu', 'show_committees_menu', 'show_chats_menu',
            'show_documents_menu', 'show_announcements_menu', 'show_calendar_menu',
            'show_legislation_menu', 'show_excuses_menu', 'show_search_menu',
            'show_roberts_rules_menu',
        ]
        selected_count = sum(1 for f in menu_fields if cleaned_data.get(f))
        if selected_count > 9:
            raise forms.ValidationError(
                f"You can select at most 9 menu items. "
                f"You selected {selected_count}; please deselect {selected_count - 9}."
            )
        return cleaned_data

    def save(self):
        """Write cleaned data into instance.prefs and save. Returns the instance."""
        p = self._instance
        p.theme = self.cleaned_data['theme']
        p.prefs = {
            'email': {
                'announcements': self.cleaned_data['email_announcements'],
                'legislation': self.cleaned_data['email_legislation'],
                'events': self.cleaned_data['email_events'],
                'committee_updates': self.cleaned_data['email_committee_updates'],
            },
            'display': {
                'compact_view': self.cleaned_data['compact_view'],
                'announcement_popups': self.cleaned_data['show_announcement_popups'],
                'home_layout': self.cleaned_data['home_layout'],
                'landing_page': self.cleaned_data['landing_page'],
            },
            'menu': {
                'vote': self.cleaned_data['show_vote_menu'],
                'committees': self.cleaned_data['show_committees_menu'],
                'chats': self.cleaned_data['show_chats_menu'],
                'documents': self.cleaned_data['show_documents_menu'],
                'announcements': self.cleaned_data['show_announcements_menu'],
                'calendar': self.cleaned_data['show_calendar_menu'],
                'legislation': self.cleaned_data['show_legislation_menu'],
                'excuses': self.cleaned_data['show_excuses_menu'],
                'search': self.cleaned_data['show_search_menu'],
                'roberts_rules': self.cleaned_data['show_roberts_rules_menu'],
            },
            'notifications': {
                'announcements': self.cleaned_data['notify_announcements'],
                'legislation': self.cleaned_data['notify_legislation'],
                'events': self.cleaned_data['notify_events'],
                'slating': self.cleaned_data['notify_slating'],
            },
        }
        p.save()
        return p


class AddMemberForm(forms.Form):
    """Form for officers to add new members"""
    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Full name (e.g., John Smith)'
        })
    )
    user_id = forms.CharField(
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Unique member ID'
        }),
        help_text='Unique identifier for the member (cannot be changed later)'
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'email@example.com (optional)'
        })
    )
    member_type = forms.ChoiceField(
        choices=ParliamentUser.MEMBER_TYPES,
        initial='Pledge',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    member_status = forms.ChoiceField(
        choices=ParliamentUser.MEMBER_STATUS,
        initial='Active',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded'
        })
    )

    def clean_user_id(self):
        user_id = self.cleaned_data.get('user_id')
        if ParliamentUser.objects.filter(user_id=user_id).exists():
            raise forms.ValidationError('A member with this ID already exists.')
        return user_id

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and ParliamentUser.objects.filter(email=email).exists():
            raise forms.ValidationError('A member with this email already exists.')
        return email


class EditMemberForm(forms.ModelForm):
    """Form for officers to edit existing members"""
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded'
        })
    )

    class Meta:
        model = ParliamentUser
        fields = ['name', 'preferred_name', 'email', 'member_type', 'member_status', 'roles']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'preferred_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Optional preferred first name'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'member_type': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'member_status': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['roles'] = self.instance.roles.all()

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            existing = ParliamentUser.objects.filter(email=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError('A member with this email already exists.')
        return email

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            # Handle roles separately since it's a ManyToMany field
            instance.roles.set(self.cleaned_data.get('roles', []))
        return instance


# =============================================================================
# SERVICE HOURS FORMS
# =============================================================================

class ServiceHoursSubmissionForm(forms.ModelForm):
    """Form for submitting service hours"""

    class Meta:
        model = ServiceHoursSubmission
        fields = ['period', 'hours', 'service_date', 'organization', 'description', 'attachment']
        widgets = {
            'period': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'hours': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': '0.00',
                'step': '0.25',
                'min': '0.25'
            }),
            'service_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'organization': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Organization or event name'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Describe the service you performed...',
                'rows': 4
            }),
            'attachment': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:bg-blue-50 file:text-blue-700 dark:file:bg-blue-900 dark:file:text-blue-200',
                'accept': '.pdf,.jpg,.jpeg,.png,.docx'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active periods
        self.fields['period'].queryset = ServicePeriod.objects.filter(is_active=True)

    def clean_hours(self):
        hours = self.cleaned_data.get('hours')
        if hours and hours <= 0:
            raise forms.ValidationError('Hours must be greater than 0.')
        if hours and hours > 24:
            raise forms.ValidationError('Hours cannot exceed 24 for a single day.')
        return hours

    def clean_attachment(self):
        attachment = self.cleaned_data.get('attachment')
        if attachment:
            # Validate file size (max 20 MB)
            if attachment.size > 20 * 1024 * 1024:
                raise forms.ValidationError('File size cannot exceed 20 MB.')

            # Validate file extension
            allowed_extensions = ['.pdf', '.jpg', '.jpeg', '.png', '.docx']
            import os
            ext = os.path.splitext(attachment.name)[1].lower()
            if ext not in allowed_extensions:
                raise forms.ValidationError(f'Invalid file type. Allowed: {", ".join(allowed_extensions)}')

        return attachment


class ServicePeriodForm(forms.ModelForm):
    """Form for creating/editing service periods"""

    class Meta:
        model = ServicePeriod
        fields = ['name', 'start_date', 'end_date', 'default_hours_required', 'requires_approval', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'e.g., Fall 2026'
            }),
            'start_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'end_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'default_hours_required': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'step': '0.5',
                'min': '0'
            }),
            'requires_approval': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded'
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and end_date and start_date >= end_date:
            raise forms.ValidationError('End date must be after start date.')

        return cleaned_data


class ServiceMemberExpectationForm(forms.ModelForm):
    """Form for setting individual member hour expectations"""

    class Meta:
        model = ServiceMemberExpectation
        fields = ['member', 'expected_hours', 'reason']
        widgets = {
            'member': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'expected_hours': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'step': '0.5',
                'min': '0'
            }),
            'reason': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Reason for adjusted hours (optional)',
                'rows': 3
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active members
        self.fields['member'].queryset = ParliamentUser.objects.filter(
            member_status='Active'
        ).order_by('name')


class CommitteeCreateForm(forms.ModelForm):
    """Form for creating a new committee"""

    initial_members = forms.ModelMultipleChoiceField(
        queryset=ParliamentUser.objects.filter(member_status='Active').order_by('name'),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'size': '8'
        }),
        help_text='Select members to add to this committee (Ctrl+Click for multiple)'
    )

    initial_chairs = forms.ModelMultipleChoiceField(
        queryset=ParliamentUser.objects.filter(member_status='Active').order_by('name'),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'size': '4'
        }),
        help_text='Select chair(s) for this committee'
    )

    class Meta:
        model = Committee
        fields = ['name', 'code', 'role', 'is_ad_hoc', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Committee Name'
            }),
            'code': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'CODE (e.g., FINANCE, RISK)',
                'style': 'text-transform: uppercase;'
            }),
            'role': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'is_ad_hoc': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
        }
        help_texts = {
            'code': 'Short uppercase code to identify the committee (e.g., FINANCE, RISK)',
            'role': 'The officer role that administers this committee (optional)',
            'is_ad_hoc': 'Check if this is a temporary committee',
            'is_active': 'Uncheck to hide the committee from normal views',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].queryset = Role.objects.all().order_by('name')
        self.fields['role'].required = False
        self.fields['is_active'].initial = True

    def clean_code(self):
        code = self.cleaned_data.get('code')
        if code:
            code = code.upper().strip()
            # Check for existing committee with same code
            if Committee.objects.filter(code__iexact=code).exists():
                raise forms.ValidationError('A committee with this code already exists.')
        return code

    def save(self, commit=True):
        committee = super().save(commit=commit)
        if commit:
            # Add initial members
            initial_members = self.cleaned_data.get('initial_members')
            if initial_members:
                committee.members.set(initial_members)

            # Add initial chairs
            initial_chairs = self.cleaned_data.get('initial_chairs')
            if initial_chairs:
                committee.chairs.set(initial_chairs)

        return committee


class SongForm(forms.ModelForm):
    """Form for creating and editing songs in the songbook"""

    class Meta:
        model = Song
        fields = ['title', 'category', 'lyrics', 'audio_file']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Enter song title'
            }),
            'category': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'lyrics': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono',
                'rows': 15,
                'placeholder': 'Enter song lyrics...\n\nVerse 1:\nFirst line of verse...\nSecond line...\n\nChorus:\nChorus lyrics...'
            }),
            'audio_file': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'accept': '.mp3,.wav,.m4a,.ogg,.flac'
            }),
        }
        labels = {
            'title': 'Song Title',
            'category': 'Category',
            'lyrics': 'Lyrics',
            'audio_file': 'Audio File (Optional)',
        }
        help_texts = {
            'lyrics': 'Enter the full lyrics. Line breaks will be preserved.',
            'audio_file': 'Upload an audio recording (MP3, WAV, M4A, OGG, or FLAC). Max 50MB.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = SongCategory.objects.all()
        self.fields['category'].required = False
        self.fields['audio_file'].required = False

    def clean_audio_file(self):
        file = self.cleaned_data.get('audio_file')
        if file:
            # Check file size (50 MB max for audio)
            if file.size > 50 * 1024 * 1024:
                raise forms.ValidationError('Audio file size must not exceed 50 MB.')

            # Check file extension
            allowed_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac']
            import os
            ext = os.path.splitext(file.name)[1].lower()
            if ext not in allowed_extensions:
                raise forms.ValidationError(
                    f'Invalid file type. Allowed types: MP3, WAV, M4A, OGG, FLAC.'
                )

            # Check MIME type
            try:
                mime = magic.from_buffer(file.read(2048), mime=True)
                file.seek(0)

                allowed_mimes = [
                    'audio/mpeg',       # MP3
                    'audio/mp3',        # MP3 alternate
                    'audio/wav',        # WAV
                    'audio/x-wav',      # WAV alternate
                    'audio/mp4',        # M4A
                    'audio/x-m4a',      # M4A alternate
                    'audio/ogg',        # OGG
                    'audio/flac',       # FLAC
                    'audio/x-flac',     # FLAC alternate
                ]

                if mime not in allowed_mimes:
                    raise forms.ValidationError(
                        f'Invalid audio file type. Expected audio file, but got {mime}.'
                    )
            except Exception as e:
                logger.warning(f"Audio file MIME check failed: {e}")
                # Allow if we can't check MIME but extension is valid
                pass

        return file


class SongCategoryForm(forms.ModelForm):
    """Form for creating and editing song categories"""

    class Meta:
        model = SongCategory
        fields = ['name', 'color', 'description', 'display_order']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Category name (e.g., Hymns)'
            }),
            'color': forms.Select(
                choices=[
                    ('blue', 'Blue'),
                    ('green', 'Green'),
                    ('red', 'Red'),
                    ('yellow', 'Yellow'),
                    ('purple', 'Purple'),
                    ('pink', 'Pink'),
                    ('gray', 'Gray'),
                ],
                attrs={
                    'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
                }
            ),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'rows': 2,
                'placeholder': 'Optional description'
            }),
            'display_order': forms.NumberInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'min': 0
            }),
        }
        help_texts = {
            'display_order': 'Lower numbers appear first in the list.',
        }

