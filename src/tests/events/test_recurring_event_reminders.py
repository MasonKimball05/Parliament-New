"""
Recurring events did not copy reminder configuration (push or email) from
the parent event to the generated instances.

`generate_recurring_events` builds each occurrence as its own `Event` row
(with `parent_event` pointing back to the original), and
`send_event_reminder_pushes` reads `reminder_N_enabled` /
`reminder_N_hours_before` / `reminder_N_email_enabled` per-row — there is no
fallback to the parent's configuration. Before this fix, none of those six
fields were passed into the generated `Event(...)`, so every instance got
the model defaults (both reminders off). An officer who configured
reminders on a recurring meeting got them for the first occurrence only —
the parent row — and every generated instance after that silently never
reminded anyone, with nothing on the create/edit form suggesting the
setting wouldn't carry forward.

`reminder_N_sent_at` is deliberately NOT copied and is asserted here to stay
null on a freshly generated instance — each instance has its own send
state, and copying a timestamp from the parent would make an instance that
has never actually dispatched a reminder look like it already had.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from src.models import Event, ParliamentUser
from src.view.officer.manage_events import generate_recurring_events


def make_officer(uid='recur-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Recur Officer', username=uid,
        member_type='Officer', is_admin=True,
    )


class RecurringEventReminderCopyTests(TestCase):
    def setUp(self):
        self.officer = make_officer()

    def _make_parent(self, **overrides):
        defaults = dict(
            title='Weekly Chapter Meeting',
            description='D',
            date_time=timezone.make_aware(
                timezone.datetime(2026, 9, 1, 19, 0)
            ),
            is_active=True,
            is_recurring=True,
            recurrence_type='weekly',
            recurrence_end_date=date(2026, 9, 29),
            created_by=self.officer,
            reminder_1_enabled=True,
            reminder_1_hours_before=24,
            reminder_1_email_enabled=True,
            reminder_2_enabled=True,
            reminder_2_hours_before=1,
            reminder_2_email_enabled=False,
        )
        defaults.update(overrides)
        return Event.objects.create(**defaults)

    def test_generated_instances_inherit_both_reminder_slots(self):
        parent = self._make_parent()
        instances = generate_recurring_events(parent)

        self.assertGreater(len(instances), 0, 'fixture should generate at least one instance')
        for instance in instances:
            self.assertTrue(instance.reminder_1_enabled)
            self.assertEqual(instance.reminder_1_hours_before, 24)
            self.assertTrue(instance.reminder_1_email_enabled)
            self.assertTrue(instance.reminder_2_enabled)
            self.assertEqual(instance.reminder_2_hours_before, 1)
            self.assertFalse(instance.reminder_2_email_enabled)

    def test_generated_instances_do_not_inherit_sent_at(self):
        """
        ⚠️ Negative case for the fix above: copying reminder_N_sent_at from
        the parent would make a brand-new instance look like it had already
        sent a reminder it never actually dispatched.
        """
        parent = self._make_parent()
        parent.reminder_1_sent_at = timezone.now()
        parent.save(update_fields=['reminder_1_sent_at'])

        instances = generate_recurring_events(parent)
        self.assertGreater(len(instances), 0)
        for instance in instances:
            self.assertIsNone(instance.reminder_1_sent_at)
            self.assertIsNone(instance.reminder_2_sent_at)

    def test_reminders_off_on_parent_stay_off_on_instances(self):
        """Control: a parent with reminders disabled generates instances with reminders disabled — the copy isn't just always-True."""
        parent = self._make_parent(
            reminder_1_enabled=False, reminder_1_email_enabled=False,
            reminder_2_enabled=False, reminder_2_email_enabled=False,
        )
        instances = generate_recurring_events(parent)
        self.assertGreater(len(instances), 0)
        for instance in instances:
            self.assertFalse(instance.reminder_1_enabled)
            self.assertFalse(instance.reminder_1_email_enabled)
            self.assertFalse(instance.reminder_2_enabled)
            self.assertFalse(instance.reminder_2_email_enabled)

    def test_saved_instances_are_picked_up_by_the_reminder_task_query(self):
        """
        End-to-end-ish: once saved, a generated instance with a due reminder
        is a real candidate row for send_event_reminder_pushes — not just a
        Python attribute that happens to be set on the unsaved object.
        """
        parent = self._make_parent(
            date_time=timezone.now() + timedelta(days=10),
            reminder_1_enabled=True, reminder_1_hours_before=24,
        )
        instances = generate_recurring_events(parent)
        for instance in instances:
            instance.save()

        from django.db.models import Q
        candidates = Event.objects.filter(
            is_active=True,
            date_time__gt=timezone.now(),
            date_time__lte=timezone.now() + timedelta(days=30),
        ).filter(
            Q(reminder_1_enabled=True, reminder_1_sent_at__isnull=True) |
            Q(reminder_2_enabled=True, reminder_2_sent_at__isnull=True)
        )
        instance_ids = {i.id for i in instances}
        matched_ids = set(candidates.values_list('id', flat=True))
        self.assertTrue(
            instance_ids.issubset(matched_ids),
            'generated instances with an enabled reminder should be candidates '
            'for send_event_reminder_pushes, not just carry the flag unused',
        )
