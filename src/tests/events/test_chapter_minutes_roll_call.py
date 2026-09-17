"""
09-16-26 — Mason: "can you make it so advisors also show when taking
attendance just for the roll call? We gave one of our advisors a roll
number in the chapter and it'd be nice to be able to call him so he can
say his number in response. This is for chapter mins."

Two things bundled into one fix, because the second was blocking the first
from actually working:

1. `edit_chapter_minutes` excluded `member_type='Advisor'` outright from the
   roll-call member list — advisors never appeared at all.
2. Even once included, the roll call displayed and sorted by `user_id` —
   which, per the v3.23.0 initiation redesign (see CLAUDE.md), is an OPAQUE
   surrogate key for anyone initiated since ("P-C7JKZY"-shaped), not a
   callable number. The actual roll number members answer to is
   `role_number`. This is the same class of bug v3.24.0 already fixed in
   home.html/home_modern.html/home_classic.html — chapter_minutes.py was
   never touched by that pass. Fixed here: `role_number` is now a separate
   field in the member JSON (user_id stays as the internal form/JS key),
   displayed instead of user_id, and non-pledges sort by role_number
   (numeric where possible) instead of the opaque id.

Covers: an advisor with a role_number appears in the roll call and sorts by
that number; an advisor with no role_number still appears (just sorts
last, nothing to call); role_number rather than user_id is what's exposed
for display; and numeric-not-lexicographic sort order (2 before 19).
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import ChapterMinutes, ParliamentUser


def _officer(uid='RC-OFFICER'):
    return ParliamentUser.objects.create_user(
        user_id=uid, password='roll-call-pass-12345!',
        name='Officer', username=uid.lower().replace('-', '_'),
        member_type='Officer',
    )


def _member(uid, name, member_type='Member', role_number=None):
    return ParliamentUser.objects.create_user(
        user_id=uid, password='roll-call-pass-12345!',
        name=name, username=uid.lower().replace('-', '_'),
        member_type=member_type, member_status='Active', role_number=role_number,
    )


class AdvisorsAppearInRollCallTests(TestCase):
    def setUp(self):
        self.officer = _officer()
        self.minutes = ChapterMinutes.objects.create(
            title='Chapter Meeting', date=timezone.localdate(),
            start_time='19:00', created_by=self.officer, status='draft',
        )
        self.client.login(username=self.officer.username, password='roll-call-pass-12345!')

    def _member_list(self):
        resp = self.client.get(reverse('edit_chapter_minutes', args=[self.minutes.id]))
        # members_json is embedded via json.dumps + |escapejs — pull it back
        # out of the context rather than re-parsing escaped JS out of the
        # rendered HTML.
        return json.loads(resp.context['members_json'])

    def test_an_advisor_with_a_role_number_appears(self):
        advisor = _member('RC-ADV1', 'Advisor Bob', member_type='Advisor', role_number='42')
        members = self._member_list()
        self.assertIn(advisor.user_id, [m['user_id'] for m in members])

    def test_an_advisor_with_no_role_number_still_appears(self):
        advisor = _member('RC-ADV2', 'Advisor Jane', member_type='Advisor', role_number=None)
        members = self._member_list()
        self.assertIn(advisor.user_id, [m['user_id'] for m in members])

    def test_role_number_not_user_id_is_exposed_for_display(self):
        _member('RC-M1', 'Regular Member', role_number='17')
        members = self._member_list()
        entry = next(m for m in members if m['user_id'] == 'RC-M1')
        self.assertEqual(entry['role_number'], '17')


class RollCallSortOrderTests(TestCase):
    def setUp(self):
        self.officer = _officer('RC-SORT-OFF')
        self.minutes = ChapterMinutes.objects.create(
            title='Chapter Meeting', date=timezone.localdate(),
            start_time='19:00', created_by=self.officer, status='draft',
        )
        self.client.login(username=self.officer.username, password='roll-call-pass-12345!')

    def _member_list(self):
        resp = self.client.get(reverse('edit_chapter_minutes', args=[self.minutes.id]))
        return json.loads(resp.context['members_json'])

    def test_non_pledges_sort_numerically_by_role_number_not_lexicographically(self):
        _member('RC-N19', 'Nineteen', role_number='19')
        _member('RC-N2', 'Two', role_number='2')
        _member('RC-N3', 'Three', role_number='3')

        members = self._member_list()
        ids_in_order = [m['user_id'] for m in members if m['user_id'] in ('RC-N19', 'RC-N2', 'RC-N3')]

        self.assertEqual(ids_in_order, ['RC-N2', 'RC-N3', 'RC-N19'])

    def test_members_without_a_role_number_sort_after_numbered_ones(self):
        numbered = _member('RC-HASNUM', 'Has Number', role_number='5')
        unnumbered = _member('RC-NONUM', 'No Number', member_type='Advisor', role_number=None)

        members = self._member_list()
        ids_in_order = [m['user_id'] for m in members]

        self.assertLess(ids_in_order.index(numbered.user_id), ids_in_order.index(unnumbered.user_id))
