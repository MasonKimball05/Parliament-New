"""
10-28-26 — the shared ballot code (v4.0.0 `src/ballots.py`) logged
"<username> voted 'no' on '<bill>'" to the `function_calls` logger for every
bill. That logger writes django_actions.log, which /officers/system-logs/ shows
to every officer — so an anonymous bill's ballots were readable by name.

    python manage.py test src.tests.legislation.test_anonymous_ballot_log
"""
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from src import ballots
from src.models import Attendance, Legislation, ParliamentUser


class AnonymousBallotLogTests(TestCase):

    def setUp(self):
        cache.clear()
        self.voter = ParliamentUser.objects.create_user(
            user_id='anonlog1', name='Anon Log', username='anonlog1', member_type='Member')
        Attendance.objects.create(user=self.voter, status='present')

    def leg(self, anonymous):
        return Legislation.objects.create(
            title=f'Log Leg {anonymous}', description='D', posted_by=self.voter,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='51', document='t.pdf', anonymous_vote=anonymous)

    def test_anonymous_choice_is_not_logged(self):
        with self.assertLogs('function_calls', level='INFO') as logs:
            ballots.cast(self.voter, self.leg(True), ['no'])
        text = '\n'.join(logs.output)
        self.assertIn('anonlog1 voted (anonymous)', text)
        self.assertNotIn("'no'", text)

    def test_open_ballot_still_logs_the_choice(self):
        with self.assertLogs('function_calls', level='INFO') as logs:
            ballots.cast(self.voter, self.leg(False), ['no'])
        self.assertIn("anonlog1 voted 'no'", '\n'.join(logs.output))
