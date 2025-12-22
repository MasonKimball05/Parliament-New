from django import forms
from .models import Legislation, Announcement, Event

class LegislationForm(forms.ModelForm):
    class Meta:
        model = Legislation
        fields = ['title', 'description', 'available_at', 'document', 'anonymous_vote', 'allow_abstain', 'required_percentage']

    def clean_document(self):
        file = self.cleaned_data.get('document')
        if file and not file.name.endswith(('.pdf', '.docx')):
            raise forms.ValidationError('The file must be a PDF or DOCX.')
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
