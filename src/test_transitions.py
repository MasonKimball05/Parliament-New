"""
Tests for the role-transfer → RoleHistory wiring and the transition checklist
(v3.13.0). Run with: python manage.py test src.test_transitions
"""
import json
from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from src.models import (
    ParliamentUser, Role, RoleHistory,
    TransitionChecklistItem, TransitionChecklistStatus,
)
from src.utils.semester import current_semester, transition_semesters


def _make_user(user_id, name, username, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=name, username=username, member_type=member_type,
    )
    user.is_active = True
    user.member_status = 'Active'
    user.set_password('test-pass-12345')
    user.save()
    return user


class SemesterHelperTests(TestCase):
    def test_current_semester(self):
        self.assertEqual(current_semester(date(2026, 3, 1)), 'Spring 2026')
        self.assertEqual(current_semester(date(2026, 5, 31)), 'Spring 2026')
        self.assertEqual(current_semester(date(2026, 6, 1)), 'Fall 2026')
        self.assertEqual(current_semester(date(2026, 12, 15)), 'Fall 2026')

    def test_transition_at_term_boundary(self):
        # December handoff: outgoing served through Fall, incoming starts next Spring
        self.assertEqual(transition_semesters(date(2026, 12, 5)), ('Fall 2026', 'Spring 2027'))
        # January handoff: same term boundary, seen from the other side
        self.assertEqual(transition_semesters(date(2027, 1, 20)), ('Fall 2026', 'Spring 2027'))

    def test_offcycle_transition(self):
        self.assertEqual(transition_semesters(date(2026, 3, 10)), ('Spring 2026', 'Spring 2026'))
        self.assertEqual(transition_semesters(date(2026, 9, 10)), ('Fall 2026', 'Fall 2026'))


class ScriptSafeJsonTests(TestCase):
    """The JSON islands in role_transitions.html render with |safe — a raw
    '</script>' in a member name must not terminate the script block."""

    def test_escapes_script_breakout(self):
        from src.view.officer.transitions import _script_safe_json
        evil = 'Evil </script><script>alert(1)</script>'
        payload = _script_safe_json({'name': evil})
        self.assertNotIn('</script>', payload)
        self.assertNotIn('<', payload)
        # Escaping must be lossless — the browser's JSON/JS parser gets the
        # original string back
        self.assertEqual(json.loads(payload)['name'], evil)


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class TransferRoleHistoryTests(TestCase):
    """transfer_role must maintain RoleHistory and create checklist statuses."""

    def setUp(self):
        self.role = Role.objects.create(name='President', code='President', one_per_chapter=True)
        self.officer = _make_user('9001', 'Olivia Officer', 'olivia', member_type='Officer')
        self.outgoing = _make_user('9002', 'Owen Outgoing', 'owen', member_type='Officer')
        self.incoming = _make_user('9003', 'Ivy Incoming', 'ivy')
        self.outgoing.roles.add(self.role)
        RoleHistory.objects.create(
            user=self.outgoing, role_name='President', start_semester='Spring 2026',
        )
        self.global_item = TransitionChecklistItem.objects.create(text='Meet predecessor', order=0)
        self.role_item = TransitionChecklistItem.objects.create(
            role=self.role, text='Meet the advisor', order=10,
        )
        self.other_role_item = TransitionChecklistItem.objects.create(
            role=Role.objects.create(name='VPF', code='VPF'), text='Bank signature', order=20,
        )
        self.inactive_item = TransitionChecklistItem.objects.create(
            text='Old item', order=30, is_active=False,
        )
        self.client.force_login(self.officer)

    def _transfer(self, **overrides):
        payload = {'incoming_user_id': '9003', 'outgoing_user_id': '9002'}
        payload.update(overrides)
        return self.client.post(
            reverse('transfer_role', kwargs={'role_id': self.role.id}),
            data=json.dumps(payload), content_type='application/json',
        )

    def test_transfer_closes_outgoing_and_opens_incoming_history(self):
        response = self._transfer()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        old = RoleHistory.objects.get(user=self.outgoing, role_name='President')
        self.assertNotEqual(old.end_semester, '')

        new = RoleHistory.objects.get(user=self.incoming, role_name='President')
        self.assertEqual(new.end_semester, '')
        self.assertEqual(data['role_history_id'], new.id)
        self.assertIn('checklist_url', data)

    def test_transfer_creates_checklist_statuses(self):
        self._transfer()
        new = RoleHistory.objects.get(user=self.incoming, role_name='President')
        item_ids = set(new.checklist_statuses.values_list('item_id', flat=True))
        # Global + matching-role items only; other-role and inactive excluded
        self.assertEqual(item_ids, {self.global_item.id, self.role_item.id})

    def test_double_transfer_is_idempotent(self):
        self._transfer()
        # Second identical submit (outgoing no longer holds the role, but the
        # endpoint should not duplicate history or statuses for the incoming)
        self._transfer(outgoing_user_id='')
        self.assertEqual(
            RoleHistory.objects.filter(
                user=self.incoming, role_name='President', end_semester='',
            ).count(), 1,
        )
        new = RoleHistory.objects.get(user=self.incoming, role_name='President', end_semester='')
        self.assertEqual(new.checklist_statuses.count(), 2)

    def test_one_per_chapter_autoclear_closes_histories(self):
        # No outgoing specified on an exclusive role → all holders cleared and closed
        response = self._transfer(outgoing_user_id='')
        self.assertTrue(response.json()['success'])
        old = RoleHistory.objects.get(user=self.outgoing, role_name='President')
        self.assertNotEqual(old.end_semester, '')


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class ChecklistViewTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name='President', code='President')
        self.officer = _make_user('9101', 'Olivia Officer', 'olivia2', member_type='Officer')
        self.holder = _make_user('9102', 'Harry Holder', 'harry')
        self.rando = _make_user('9103', 'Randy Random', 'randy')
        self.chair = _make_user('9104', 'Charlie Chair', 'charlie', member_type='Chair')
        self.history = RoleHistory.objects.create(
            user=self.holder, role_name='President', start_semester='Spring 2026',
        )
        item = TransitionChecklistItem.objects.create(text='Meet predecessor')
        self.status = TransitionChecklistStatus.objects.create(
            item=item, role_history=self.history,
        )
        self.url = reverse('transition_checklist', kwargs={'role_history_id': self.history.id})
        self.toggle_url = reverse('toggle_checklist_item', kwargs={'status_id': self.status.id})

    def test_officer_can_view(self):
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_holder_can_view_own(self):
        self.client.force_login(self.holder)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_chair_can_view_and_toggle(self):
        # Chairs pass officer_required on the transitions page, so they must
        # also be able to open the checklists it links to (403'd before 07-09-26).
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertTrue(self.client.post(self.toggle_url).json()['success'])

    def test_other_member_forbidden(self):
        self.client.force_login(self.rando)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        # Page view must render the styled 403, not bare JSON
        self.assertTemplateUsed(response, '403.html')

    def test_toggle_sets_and_clears(self):
        self.client.force_login(self.holder)

        data = self.client.post(self.toggle_url).json()
        self.assertTrue(data['success'] and data['completed'])
        self.status.refresh_from_db()
        self.assertIsNotNone(self.status.completed_at)
        self.assertEqual(self.status.completed_by, self.holder)
        self.assertEqual(data['progress'], {'total': 1, 'done': 1})

        data = self.client.post(self.toggle_url).json()
        self.assertFalse(data['completed'])
        self.status.refresh_from_db()
        self.assertIsNone(self.status.completed_at)
        self.assertIsNone(self.status.completed_by)

    def test_toggle_forbidden_for_other_member(self):
        self.client.force_login(self.rando)
        self.assertEqual(self.client.post(self.toggle_url).status_code, 403)

    def test_toggle_requires_post(self):
        self.client.force_login(self.holder)
        self.assertEqual(self.client.get(self.toggle_url).status_code, 405)
