import os

from django import forms

from src.models import ParliamentUser, ServiceHoursSubmission, ServiceMemberExpectation, ServicePeriod


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
