"""
11-06-26: the "Default Reminder Lead Time (hours)" site setting (Admin v2 →
site settings) was defined but never read. A new event's first reminder now
starts at it, on the website's form and in the app's event options.

    python manage.py test src.tests.officer.test_reminder_default_hours
"""
from django.test import TestCase

from src.forms.events import EventForm, default_reminder_hours
from src.models import Event
from src.models_feature_flags import SiteSetting


class ReminderDefaultHoursTests(TestCase):

    def set(self, value):
        SiteSetting.objects.update_or_create(key='event_reminder_default_hours', defaults={
            'display_name': 'Default Reminder Lead Time (hours)', 'category': 'notifications',
            'setting_type': 'integer', 'value': value, 'default_value': '24'})

    def test_unset_is_24(self):
        self.assertEqual(default_reminder_hours(), 24)

    def test_new_event_form_starts_at_the_setting(self):
        self.set('6')
        self.assertEqual(EventForm()['reminder_1_hours_before'].value(), 6)

    def test_out_of_range_or_junk_falls_back(self):
        for bad in ('0', '500', 'soon'):
            self.set(bad)
            self.assertEqual(default_reminder_hours(), 24, bad)

    def test_an_existing_event_keeps_its_own(self):
        self.set('6')
        from django.utils import timezone
        from src.models import ParliamentUser
        officer = ParliamentUser.objects.create(user_id='rdh-1', username='rdh-1', name='Officer',
                                                member_type='Officer', member_status='Active')
        e = Event.objects.create(title='T', description='d', date_time=timezone.now(), created_by=officer,
                                 reminder_1_hours_before=48)
        self.assertEqual(EventForm(instance=e)['reminder_1_hours_before'].value(), 48)
