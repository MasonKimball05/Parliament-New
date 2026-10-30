from django import forms

from src import location_checkin
from src.models import CheckinPlace, Committee, Event


class EventForm(forms.ModelForm):
    committee = forms.ModelChoiceField(
        queryset=Committee.objects.filter(is_active=True).order_by('name'),
        required=False,
        empty_label='-- Chapter-wide (default) --',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
        }),
        help_text=(
            "Make this a committee meeting: attendance tracking, excuses, and "
            "push/email reminders will only cover this committee's members and "
            'chairs — plus anyone who signs up, even if not on the committee. '
            'Leave as chapter-wide for a normal event.'
        ),
    )

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

    # Location check-in area (v3.60.0; templates/officer/_checkin_area.html).
    # The map fills the two hidden coordinates; "Off" clears all three.
    CHECKIN_RADII = [('', 'Off'), (25, '25 m'), (50, '50 m'), (100, '100 m'), (150, '150 m'),
                     (200, '200 m'), (300, '300 m'), (500, '500 m')]
    checkin_radius_m = forms.TypedChoiceField(
        choices=CHECKIN_RADII, coerce=int, empty_value=None, required=False,
        label='Location check-in',
        widget=forms.Select(attrs={
            'class': 'px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg',
            'data-checkin-radius': '',
        }),
    )
    checkin_save_as = forms.CharField(
        max_length=80, required=False, label='Save this spot as',
        widget=forms.TextInput(attrs={
            'class': 'w-full px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg',
            'placeholder': 'e.g. Chapter room (optional)',
        }),
    )

    # Timed check-in (10-30-26, src/location_time.py): a minimum stay, or
    # study hours that count time spent inside the area.
    CHECKIN_MINUTES = [('', 'Arriving is enough'), (15, '15 minutes'), (30, '30 minutes'),
                       (45, '45 minutes'), (60, '1 hour'), (90, '1½ hours'), (120, '2 hours')]
    checkin_min_minutes = forms.TypedChoiceField(
        choices=CHECKIN_MINUTES, coerce=int, empty_value=None, required=False,
        label='Time needed at the event',
        widget=forms.Select(attrs={
            'class': 'px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg',
        }),
    )
    STUDY_LENGTHS = [(60, '1 hour'), (90, '1½ hours'), (120, '2 hours'), (180, '3 hours'), (240, '4 hours'),
                     (300, '5 hours'), (360, '6 hours'), (480, '8 hours'), (720, '12 hours')]
    duration_minutes = forms.TypedChoiceField(
        choices=STUDY_LENGTHS, coerce=int, empty_value=None, required=False, initial=180,
        label='Study hours last',
        widget=forms.Select(attrs={
            'class': 'px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg',
        }),
    )

    class Meta:
        model = Event
        fields = ['title', 'description', 'date_time', 'location', 'visible_to', 'is_active',
                  'committee', 'requires_attendance', 'allow_excuses', 'excuse_deadline',
                  'requires_signup', 'max_signups', 'signups_open', 'allow_waitlist', 'rsvp_email_enabled',
                  'is_recurring', 'recurrence_type', 'recurrence_interval', 'recurrence_unit',
                  'recurrence_days', 'recurrence_end_date',
                  'reminder_1_enabled', 'reminder_1_hours_before', 'reminder_1_email_enabled',
                  'reminder_2_enabled', 'reminder_2_hours_before', 'reminder_2_email_enabled',
                  'checkin_latitude', 'checkin_longitude', 'checkin_radius_m',
                  'checkin_min_minutes', 'is_study_hours', 'duration_minutes']
        widgets = {
            'checkin_latitude': forms.HiddenInput(attrs={'data-checkin-lat': ''}),
            'checkin_longitude': forms.HiddenInput(attrs={'data-checkin-lng': ''}),
            'is_study_hours': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded',
            }),
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
        if not location_checkin.enabled():
            # Off: leave any area already on the event exactly as it is.
            for name in ('checkin_latitude', 'checkin_longitude', 'checkin_radius_m', 'checkin_save_as',
                         'checkin_min_minutes', 'is_study_hours', 'duration_minutes'):
                self.fields.pop(name, None)

    def clean(self):
        cleaned = super().clean()
        for field in ('reminder_1_hours_before', 'reminder_2_hours_before'):
            val = cleaned.get(field)
            if val is not None and not (1 <= val <= 168):
                self.add_error(field, 'Must be between 1 and 168 hours (1 week).')
        self._clean_checkin_area(cleaned)
        self._clean_timed_checkin(cleaned)
        return cleaned

    def _clean_timed_checkin(self, cleaned):
        """
        Study hours need a check-in area and a length, and take no attendance;
        a minimum stay needs an area and attendance. Without an area, neither.
        """
        if 'is_study_hours' not in self.fields:
            return
        has_area = bool(cleaned.get('checkin_radius_m'))
        if cleaned.get('is_study_hours'):
            if not has_area:
                self.add_error('is_study_hours', 'Study hours need a check-in area: set one on the map.')
            if not cleaned.get('duration_minutes'):
                cleaned['duration_minutes'] = 180
            cleaned['requires_attendance'] = False
            cleaned['allow_excuses'] = False
            cleaned['checkin_min_minutes'] = None
            return
        cleaned['duration_minutes'] = None
        if cleaned.get('checkin_min_minutes') and not (has_area and cleaned.get('requires_attendance')):
            cleaned['checkin_min_minutes'] = None

    def _clean_checkin_area(self, cleaned):
        """All three set (a pin and a radius), or none: "Off" clears the pin."""
        if 'checkin_radius_m' not in self.fields:
            return
        lat, lng = cleaned.get('checkin_latitude'), cleaned.get('checkin_longitude')
        radius = cleaned.get('checkin_radius_m')
        if not radius:
            cleaned['checkin_latitude'] = cleaned['checkin_longitude'] = cleaned['checkin_radius_m'] = None
            return
        if lat is None or lng is None:
            self.add_error('checkin_radius_m', 'Drop a pin on the map (or use your location) for the check-in area.')
            return
        try:
            cleaned['checkin_latitude'], cleaned['checkin_longitude'], cleaned['checkin_radius_m'] = \
                location_checkin.clean_point(lat, lng, radius)
        except ValueError as e:
            self.add_error('checkin_radius_m', str(e))

    def save_checkin_place(self, user):
        """Keep the event's check-in area as a named place, if the officer asked to."""
        if 'checkin_save_as' not in self.fields:
            return None
        name = (self.cleaned_data.get('checkin_save_as') or '').strip()
        if not name or not self.cleaned_data.get('checkin_radius_m'):
            return None
        place, _ = CheckinPlace.objects.update_or_create(name=name, defaults={
            'latitude': self.cleaned_data['checkin_latitude'],
            'longitude': self.cleaned_data['checkin_longitude'],
            'radius_m': self.cleaned_data['checkin_radius_m'],
            'created_by': user,
        })
        return place

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
