import logging

from django import forms

from src.models import Announcement

logger = logging.getLogger(__name__)


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
