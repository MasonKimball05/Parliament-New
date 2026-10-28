"""
10-28-26 bug report: a timed pledge task set to go live at 9 pm showed "goes
live <next day> at 2:00 AM" on the pledge tracker. The task went live at the
right time; the label formatted the stored UTC value (9 pm CDT = 02:00 UTC)
without converting it to the chapter's time zone.

    python manage.py test src.tests.education.test_timed_task_label_timezone
"""
from datetime import datetime

import pytz
from django.conf import settings
from django.test import TestCase

from src.models import PledgeTask


class TimedTaskLabelTests(TestCase):

    def label_for(self, local):
        aware = pytz.timezone(settings.TIME_ZONE).localize(local)
        task = PledgeTask.objects.create(title='Assignment', task_type='task', is_active=True,
                                         activation_mode='timed', activates_at=aware)
        task.refresh_from_db()   # back from the database in UTC, as the page reads it
        return task.activation_status_label

    def test_evening_in_daylight_time_keeps_its_day_and_hour(self):
        self.assertEqual(self.label_for(datetime(2030, 6, 1, 21, 0)), 'goes live 6/1 at 9:00 PM')

    def test_evening_in_standard_time(self):
        self.assertEqual(self.label_for(datetime(2030, 12, 1, 21, 30)), 'goes live 12/1 at 9:30 PM')
