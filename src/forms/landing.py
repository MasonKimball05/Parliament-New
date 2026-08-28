from django import forms

from src.models import PassedResolution, ResolutionSectionImpact


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
