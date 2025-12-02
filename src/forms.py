from django import forms
from .models import Legislation

class LegislationForm(forms.ModelForm):
    class Meta:
        model = Legislation
        fields = ['title', 'description', 'available_at', 'document', 'anonymous_vote', 'allow_abstain', 'required_percentage']

    def clean_document(self):
        file = self.cleaned_data.get('document')
        if file and not file.name.endswith(('.pdf', '.docx')):
            raise forms.ValidationError('The file must be a PDF or DOCX.')
        return file
