"""
v3.51.0 — "N posts waiting more than 10 minutes" on the GroupMe dashboard.

Idea from the 10-18 auto-run, approved by Mason 10-19-26: the v3.49.0 Postgres
bug left every post 'queued' forever while Send test kept working, so nothing
on the dashboard looked wrong.

Run with: python manage.py test src.tests.officer.test_groupme_stuck
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import GroupMeMessage
from src.tests.officer.test_groupme_bot import GroupMeFixture

WARNING = 'waiting more than 10 minutes'


class StuckWarningTests(GroupMeFixture, TestCase):
    def message(self, minutes_ago, status='queued'):
        m = GroupMeMessage.objects.create(channel=self.brothers, channel_name='Brothers',
                                          kind='announcement', text='hi', status=status)
        GroupMeMessage.objects.filter(pk=m.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
        return m

    def page(self):
        return self.oc.get(reverse('groupme_dashboard')).content.decode()

    def test_no_warning_when_nothing_is_stuck(self):
        self.message(2)                    # still within the retry window
        self.message(30, status='sent')
        self.message(30, status='failed')  # shown in the log with Retry, not "waiting"
        self.assertNotIn(WARNING, self.page())

    def test_warning_counts_old_queued_posts(self):
        self.message(11)
        self.message(45)
        self.message(1)
        html = self.page()
        self.assertIn('2 posts ' + WARNING, html)
        self.assertIn('parliament-worker', html)

    def test_singular(self):
        self.message(11)
        self.assertIn('1 post ' + WARNING, self.page())
