from django import forms
import magic  # python-magic for MIME type detection

from src.models import KaiReport


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
