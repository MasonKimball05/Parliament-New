"""
11-10-26: the officer transition in the app (views_slating_transition.py),
the functions it shares with the web page, and the beat task that now runs
scheduled transitions (only a cron'd command did before).
"""
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from src.models import Role, SlatingPeriod, SlatingPosition
from src.tests.mobile.test_slating_batch5 import _AppBase
from src.tests.slating.test_slating_review_fixes import member


class _TransitionBase(_AppBase):

    def setUp(self):
        super().setUp()
        self.role = Role.objects.create(name='Sim President', code='SIMP')
        self.pres.role = self.role
        self.pres.save()
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='results_published')
        self.holder = member('tr-holder')
        self.holder.roles.add(self.role)
        self.newbie = member('P-G48HFG')   # a text user id
        self.path = f'slating/periods/{self.period.id}/transition/'

    def fresh(self):
        return SlatingPeriod.objects.get(pk=self.period.pk)

    def picks(self, who=None):
        return {str(self.pres.id): (who or self.newbie).user_id}


class AppTransitionTests(_TransitionBase):

    def test_form(self):
        r = self.call('get', self.path, user=self.admin)
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual(b['state'], 'ready')
        row = next(x for x in b['rows'] if x['position_id'] == self.pres.id)
        self.assertEqual((row['role'], row['current']), ('Sim President', [self.holder.name]))
        self.assertIn(self.newbie.user_id, [m['user_id'] for m in b['members']])

    def test_preview_then_run_now(self):
        r = self.call('post', self.path, {'action': 'preview', 'assignments': self.picks()}, user=self.admin)
        self.assertEqual(r.status_code, 200, r.content)
        change = r.json()['changes'][0]
        self.assertEqual((change['new'], change['removed']), (self.newbie.name, [self.holder.name]))
        self.assertFalse(self.fresh().officer_transition_completed)   # preview changes nothing
        r = self.call('post', self.path, {'action': 'confirm', 'assignments': self.picks()}, user=self.admin)
        self.assertEqual(r.json()['outcome'], 'done')
        self.assertTrue(self.newbie.roles.filter(pk=self.role.pk).exists())
        self.assertFalse(self.holder.roles.filter(pk=self.role.pk).exists())
        # Only once.
        r = self.call('post', self.path, {'action': 'confirm', 'assignments': self.picks()}, user=self.admin)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'done'))
        self.assertEqual(self.call('get', self.path, user=self.admin).json()['state'], 'done')

    def test_schedule_and_cancel(self):
        when = (timezone.now() + timedelta(days=3)).isoformat()
        r = self.call('post', self.path, {'action': 'confirm', 'assignments': self.picks(), 'effective_at': when},
                      user=self.admin)
        self.assertEqual(r.json()['outcome'], 'scheduled')
        b = self.call('get', self.path, user=self.admin).json()
        self.assertEqual(b['state'], 'scheduled')
        row = next(x for x in b['rows'] if x['position_id'] == self.pres.id)
        self.assertEqual(row['selected']['user_id'], self.newbie.user_id)
        self.call('post', self.path, {'action': 'cancel_scheduled'}, user=self.admin)
        self.assertIsNone(self.fresh().officer_transition_at)
        self.assertFalse(self.newbie.roles.filter(pk=self.role.pk).exists())

    def test_refusals(self):
        self.assertEqual(self.call('get', self.path, user=self.manager).status_code, 403)   # not a site admin
        r = self.call('post', self.path, {'action': 'confirm', 'assignments': {str(self.pres.id): 'nobody'}},
                      user=self.admin)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'unknown_member'))
        r = self.call('post', self.path, {'action': 'confirm', 'assignments': {}}, user=self.admin)
        self.assertEqual(r.json()['error'], 'nothing_selected')
        r = self.call('post', self.path, {'action': 'confirm', 'assignments': self.picks(),
                                          'effective_at': 'next tuesday'}, user=self.admin)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.fresh().officer_transition_completed)
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_closed')
        r = self.call('get', self.path, user=self.admin)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'not_published'))

    def test_results_payload_offers_it_to_site_admins(self):
        r = self.call('get', f'slating/periods/{self.period.id}/results/', user=self.admin)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['transition']['state'], 'ready')
        self.assertIsNone(self.call('get', f'slating/periods/{self.period.id}/results/').json()['transition'])

    def test_form_queries_dont_grow_with_positions(self):
        from src.view.slating.transition import build_auto_assignments, transition_rows

        def count():
            with CaptureQueriesContext(connection) as ctx:
                transition_rows(self.fresh(), build_auto_assignments(self.fresh()))
            return len(ctx.captured_queries)
        one = count()
        for i in range(4):
            r = Role.objects.create(name=f'R{i}', code=f'R{i}')
            SlatingPosition.objects.create(period=self.period, title=f'P{i}', code=f'P{i}', role=r)
            member(f'tr-h{i}').roles.add(r)
        self.assertEqual(count(), one)


class ScheduledRunTests(_TransitionBase):
    """The beat task runs a due transition once; the web page's own path is unchanged."""

    def test_beat_task_runs_due_transitions_once(self):
        from src.tasks import execute_scheduled_slating_transitions
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            officer_transition_at=timezone.now() - timedelta(minutes=1), officer_transition_data=self.picks())
        execute_scheduled_slating_transitions()
        self.assertTrue(self.fresh().officer_transition_completed)
        self.assertTrue(self.newbie.roles.filter(pk=self.role.pk).exists())
        # A second tick (or the old cron command) does nothing.
        self.newbie.roles.remove(self.role)
        execute_scheduled_slating_transitions()
        import io
        from django.core.management import call_command
        call_command('execute_scheduled_transitions', stdout=io.StringIO())
        self.assertFalse(self.newbie.roles.filter(pk=self.role.pk).exists())

    def test_not_yet_due_waits(self):
        from src.tasks import execute_scheduled_slating_transitions
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            officer_transition_at=timezone.now() + timedelta(hours=1), officer_transition_data=self.picks())
        execute_scheduled_slating_transitions()
        self.assertFalse(self.fresh().officer_transition_completed)

    def test_registered_with_beat(self):
        from src.management.commands.setup_celery_schedules import SCHEDULES
        self.assertIn('tasks.execute_scheduled_slating_transitions', [s['task'] for s in SCHEDULES])
