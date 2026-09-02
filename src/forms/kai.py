from django import forms
import magic  # python-magic for MIME type detection

from src.models import KaiReport, KaiCommendation, ParliamentUser


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


class KaiCommendationForm(forms.ModelForm):
    """
    Form for submitting a Kai commendation. Deliberately much smaller than
    KaiReportForm — no category (KaiReport.CATEGORY_CHOICES is a
    disciplinary vocabulary that doesn't fit here), no tags (that
    vocabulary exists specifically to keep identity out of disciplinary
    case tags — not a concern this form has). Unlike an accommodation
    request would be, this DOES need a targeted person — `commended_member`
    is required, because selecting who you're commending is the whole
    point (Mason, 09-02-26, correcting the original "accommodation"
    wording mistake — see src/models/kai_commendations.py).

    v3.28.9. Attachment validation uses `validate_uploaded_file`
    (src/utils/file_validation.py) — the project's one general-purpose
    upload validator — rather than KaiReportForm's own inline `magic`-based
    check above, matching how `submit_kai_report`'s custom-field files are
    already validated (see kai_reports.py). Not touching KaiReportForm's
    existing check here; that's a separate, pre-existing surface.
    """

    class Meta:
        model = KaiCommendation
        fields = ['commended_member', 'title', 'description', 'attachment', 'is_submitter_anonymous']
        widgets = {
            'commended_member': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
            }),
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
                'placeholder': 'Brief summary of what this commendation is for'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
                'placeholder': 'What did they do? Be specific...',
                'rows': 6
            }),
            'attachment': forms.FileInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 transition-colors duration-200',
            }),
            'is_submitter_anonymous': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500',
            }),
        }
        labels = {
            'commended_member': 'Who are you commending?',
            'title': 'Summary',
            'description': 'Details',
            'attachment': 'Supporting File (Optional)',
            'is_submitter_anonymous': "Don't tell them it was me",
        }
        help_texts = {
            'commended_member': 'Select the member you want to recognize',
            'title': 'A brief, descriptive summary of what you\'re commending them for',
            'description': 'Provide the details the committee needs — what did they do?',
            'attachment': 'e.g. a photo or a screenshot of positive feedback',
            'is_submitter_anonymous': (
                'If the committee later shares this with the person you\'re '
                'commending, they won\'t be told who submitted it.'
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['commended_member'].queryset = (
            ParliamentUser.objects.filter(member_status='Active').order_by('name')
        )

    def clean_attachment(self):
        file = self.cleaned_data.get('attachment')
        if file:
            from src.utils.file_validation import validate_uploaded_file
            try:
                validate_uploaded_file(file)
            except forms.ValidationError:
                raise
            except Exception as exc:
                raise forms.ValidationError(str(exc))
        return file
