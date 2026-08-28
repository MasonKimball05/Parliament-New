from django import forms

from src.models import Committee, ParliamentUser, Role


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
