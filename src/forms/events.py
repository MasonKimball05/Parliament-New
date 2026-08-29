from django import forms

from src.models import Event


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
                  'requires_signup', 'max_signups', 'signups_open', 'allow_waitlist', 'rsvp_email_enabled',
                  'is_recurring', 'recurrence_type', 'recurrence_interval', 'recurrence_unit',
                  'recurrence_days', 'recurrence_end_date',
                  'reminder_1_enabled', 'reminder_1_hours_before', 'reminder_1_email_enabled',
                  'reminder_2_enabled', 'reminder_2_hours_before', 'reminder_2_email_enabled']
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
            }),
            'reminder_1_enabled': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'reminder_1_hours_before': forms.NumberInput(attrs={
                'class': 'w-32 px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'min': 1,
                'max': 168,
            }),
            'reminder_1_email_enabled': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'reminder_2_enabled': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'reminder_2_hours_before': forms.NumberInput(attrs={
                'class': 'w-32 px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'min': 1,
                'max': 168,
            }),
            'reminder_2_email_enabled': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'requires_signup': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'max_signups': forms.NumberInput(attrs={
                'class': 'w-32 px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'min': 1,
                'placeholder': 'Unlimited',
            }),
            'signups_open': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'allow_waitlist': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
            'rsvp_email_enabled': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded'
            }),
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
            'recurrence_end_date': 'End Date (Optional)',
            'reminder_1_enabled': 'Reminder 1',
            'reminder_1_hours_before': 'Hours Before',
            'reminder_1_email_enabled': 'Also email this reminder',
            'reminder_2_enabled': 'Reminder 2',
            'reminder_2_hours_before': 'Hours Before',
            'reminder_2_email_enabled': 'Also email this reminder',
            'requires_signup': 'Requires Sign-Up',
            'max_signups': 'Max Sign-Ups',
            'signups_open': 'Signups Open',
            'allow_waitlist': 'Enable Waitlist',
            'rsvp_email_enabled': 'Send RSVP Announcement Email',
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

    def clean(self):
        cleaned = super().clean()
        for field in ('reminder_1_hours_before', 'reminder_2_hours_before'):
            val = cleaned.get(field)
            if val is not None and not (1 <= val <= 168):
                self.add_error(field, 'Must be between 1 and 168 hours (1 week).')
        return cleaned

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
