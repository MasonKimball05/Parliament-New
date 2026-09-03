"""
Committee events/meetings — v3.29.4.

Requested by Mason: a way to mark a calendar event as belonging to a
committee so that attendance tracking, the excuse system, and push/email
reminders only cover that committee's own members, rather than the whole
chapter (the behavior every event has had up to now). Clarified mid-request:
if someone outside the committee signs up for a sign-up-enabled committee
event, they should still be tracked and notified — sign-up is an explicit
opt-in that should count regardless of committee membership.

Deliberately NOT touched by `Event.committee`: calendar visibility
(`is_visible_to_user`, still governed only by `visible_to`) and event
sign-up eligibility (`event_signup`, unchanged) — a member can see a
committee meeting on the calendar and sign up for it without being
required to attend it. The new `committee` field only narrows who is
REQUIRED (attendance/excuses/reminders); it does not additionally restrict
who may look or opt in.
"""
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    AttendanceExcuse, Committee, Event, EventSignup, ParliamentUser,
    PushSubscription,
)
from src.tasks.notifications import send_event_reminder_pushes


def _member(uid, name, member_type='Member', member_status='Active', push=False):
    user = ParliamentUser.objects.create_user(
        user_id=uid, password='committee-event-pass-12345!',
        name=name, username=uid.lower().replace('-', '_'),
        member_type=member_type, member_status=member_status,
    )
    if push:
        PushSubscription.objects.create(
            user=user, endpoint=f'https://push.example.com/{uid}',
            p256dh='key', auth='secret',
        )
    return user


def _officer(uid='CE-OFF1'):
    return _member(uid, 'Officer', member_type='Officer')


def _committee(name='Test Committee', code='CETEST1', members=(), chairs=(), advisors=()):
    committee = Committee.objects.create(name=name, code=code, is_active=True)
    for m in members:
        committee.members.add(m)
    for c in chairs:
        committee.chairs.add(c)
    for a in advisors:
        committee.advisors.add(a)
    return committee


def _event(creator, committee=None, requires_attendance=True, requires_signup=False, **kwargs):
    defaults = dict(
        title='Committee Meeting', description='x',
        date_time=timezone.now() + timedelta(days=1),
        created_by=creator, is_active=True,
        requires_attendance=requires_attendance,
        allow_excuses=True,
        requires_signup=requires_signup,
    )
    defaults.update(kwargs)
    return Event.objects.create(committee=committee, **defaults)


class CommitteeAttendanceEligibleMembersTests(TestCase):

    def test_includes_members_and_chairs(self):
        member = _member('CAE-MEM1', 'Member One')
        chair = _member('CAE-CHR1', 'Chair One')
        committee = _committee(members=[member], chairs=[chair])

        eligible = set(committee.attendance_eligible_members())

        self.assertIn(member, eligible)
        self.assertIn(chair, eligible)

    def test_excludes_advisors_even_if_listed_as_member(self):
        advisor = _member('CAE-ADV1', 'Advisor One', member_type='Advisor')
        committee = _committee(members=[advisor])

        eligible = set(committee.attendance_eligible_members())

        self.assertNotIn(advisor, eligible)

    def test_excludes_inactive_members(self):
        inactive = _member('CAE-INA1', 'Inactive One', member_status='Alumni')
        committee = _committee(members=[inactive])

        eligible = set(committee.attendance_eligible_members())

        self.assertNotIn(inactive, eligible)

    def test_excludes_non_members(self):
        outsider = _member('CAE-OUT1', 'Outsider')
        committee = _committee()

        eligible = set(committee.attendance_eligible_members())

        self.assertNotIn(outsider, eligible)


class RequiredMembersTests(TestCase):

    def setUp(self):
        self.officer = _officer()

    def test_non_committee_event_requires_the_whole_active_chapter(self):
        member = _member('RM-MEM1', 'Member One')
        event = _event(self.officer, committee=None)

        required = set(event.required_members())

        self.assertIn(member, required)
        self.assertIn(self.officer, required)

    def test_committee_event_requires_only_committee_members(self):
        committee_member = _member('RM-CM1', 'Committee Member')
        outsider = _member('RM-OUT1', 'Outsider')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee)

        required = set(event.required_members())

        self.assertIn(committee_member, required)
        self.assertNotIn(outsider, required)

    def test_outside_signup_is_still_required(self):
        """The specific scenario Mason clarified: an outsider who signs up
        is tracked/notified even though they aren't on the committee."""
        committee = _committee()
        outsider = _member('RM-SIGN1', 'Outside Signer-Upper')
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider)

        required = set(event.required_members())

        self.assertIn(outsider, required)

    def test_cancelled_signup_is_not_required(self):
        committee = _committee()
        outsider = _member('RM-CANC1', 'Cancelled Signer')
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider, is_cancelled=True)

        required = set(event.required_members())

        self.assertNotIn(outsider, required)

    def test_waitlisted_signup_is_not_required(self):
        committee = _committee()
        outsider = _member('RM-WAIT1', 'Waitlisted Signer')
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider, waitlist_position=1)

        required = set(event.required_members())

        self.assertNotIn(outsider, required)

    def test_committee_member_who_also_signs_up_is_not_double_counted(self):
        committee_member = _member('RM-DBL1', 'Double Counted')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=committee_member)

        self.assertEqual(event.required_members().filter(pk=committee_member.pk).count(), 1)


class UserIsRequiredTests(TestCase):

    def setUp(self):
        self.officer = _officer()

    def test_always_true_for_non_committee_event(self):
        member = _member('UIR-MEM1', 'Member One')
        event = _event(self.officer, committee=None)

        self.assertTrue(event.user_is_required(member))

    def test_true_for_committee_member(self):
        committee_member = _member('UIR-CM1', 'Committee Member')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee)

        self.assertTrue(event.user_is_required(committee_member))

    def test_false_for_outsider(self):
        outsider = _member('UIR-OUT1', 'Outsider')
        committee = _committee()
        event = _event(self.officer, committee=committee)

        self.assertFalse(event.user_is_required(outsider))

    def test_true_for_signed_up_outsider(self):
        outsider = _member('UIR-SIGN1', 'Signed Up Outsider')
        committee = _committee()
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider)

        self.assertTrue(event.user_is_required(outsider))


class AttendanceStatsScopingTests(TestCase):

    def setUp(self):
        self.officer = _officer()

    def test_total_members_is_committee_sized_not_chapter_sized(self):
        for i in range(5):
            _member(f'STAT-CHAP{i}', f'Chapter Member {i}')
        committee_member = _member('STAT-CM1', 'Committee Member')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee)

        stats = event.get_attendance_stats()

        # Only the one committee member (chapter members above don't count)
        self.assertEqual(stats['total_members'], 1)

    def test_prime_attendance_stats_matches_the_unbatched_method_for_committee_events(self):
        committee_member = _member('PRIME-CM1', 'Committee Member')
        outsider_signup = _member('PRIME-SIGN1', 'Outside Signer')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider_signup)

        unbatched = event.get_attendance_stats()
        # Fresh instance so the batched path doesn't read the memoised cache
        fresh = Event.objects.get(pk=event.pk)
        batched_events = Event.prime_attendance_stats([fresh])
        batched = batched_events[0].get_attendance_stats()

        self.assertEqual(unbatched['total_members'], batched['total_members'])
        self.assertEqual(batched['total_members'], 2)  # committee member + signup

    def test_prime_attendance_stats_handles_a_mixed_page_of_committee_and_chapter_events(self):
        chapter_member = _member('MIX-CHAP1', 'Chapter Member')
        committee_member_a = _member('MIX-CMA1', 'Committee A Member')
        committee_member_b = _member('MIX-CMB1', 'Committee B Member')
        committee_a = _committee(name='Committee A', code='MIXA1', members=[committee_member_a])
        committee_b = _committee(name='Committee B', code='MIXB1', members=[committee_member_b])

        chapter_event = _event(self.officer, committee=None)
        event_a = _event(self.officer, committee=committee_a, title='A Meeting')
        event_b = _event(self.officer, committee=committee_b, title='B Meeting')

        results = Event.prime_attendance_stats([chapter_event, event_a, event_b])
        by_title = {e.title: e.get_attendance_stats() for e in results}

        # Chapter event: officer + chapter_member + committee_member_a/b (all Active)
        self.assertEqual(by_title['Committee Meeting']['total_members'], 4)
        self.assertEqual(by_title['A Meeting']['total_members'], 1)
        self.assertEqual(by_title['B Meeting']['total_members'], 1)


class MarkEventAttendanceRosterScopingTests(TestCase):
    """The officer-facing attendance-marking page only rosters required members."""

    def setUp(self):
        self.officer = _officer()
        self.client.login(username=self.officer.username, password='committee-event-pass-12345!')

    def test_outsider_is_not_on_the_roster(self):
        committee_member = _member('MEAR-CM1', 'Committee Member')
        outsider = _member('MEAR-OUT1', 'Outsider')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee)

        resp = self.client.get(reverse('mark_event_attendance', args=[event.id]))
        body = resp.content.decode()

        self.assertIn(committee_member.name, body)
        self.assertNotIn(outsider.name, body)

    def test_signed_up_outsider_is_on_the_roster(self):
        committee = _committee()
        outsider = _member('MEAR-SIGN1', 'Signed Up Outsider')
        event = _event(self.officer, committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider)

        resp = self.client.get(reverse('mark_event_attendance', args=[event.id]))

        self.assertContains(resp, outsider.name)


class ExcuseEligibilityScopingTests(TestCase):

    def setUp(self):
        self.officer = _officer()

    def test_outsider_does_not_see_committee_event_in_available_list(self):
        committee_member = _member('EXC-CM1', 'Committee Member')
        outsider = _member('EXC-OUT1', 'Outsider')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee, title='Committee Only Meeting')

        self.client.login(username=outsider.username, password='committee-event-pass-12345!')
        resp = self.client.get(reverse('my_excuses'))

        self.assertNotContains(resp, 'Committee Only Meeting')

    def test_committee_member_sees_it_in_available_list(self):
        committee_member = _member('EXC-CM2', 'Committee Member Two')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee, title='Committee Only Meeting Two')

        self.client.login(username=committee_member.username, password='committee-event-pass-12345!')
        resp = self.client.get(reverse('my_excuses'))

        self.assertContains(resp, 'Committee Only Meeting Two')

    def test_signed_up_outsider_sees_it_in_available_list(self):
        committee = _committee()
        outsider = _member('EXC-SIGN1', 'Signed Up Outsider')
        event = _event(
            self.officer, committee=committee, requires_signup=True,
            title='Sign Up Meeting',
        )
        EventSignup.objects.create(event=event, user=outsider)

        self.client.login(username=outsider.username, password='committee-event-pass-12345!')
        resp = self.client.get(reverse('my_excuses'))

        self.assertContains(resp, 'Sign Up Meeting')

    def test_outsider_cannot_submit_excuse_via_direct_url(self):
        committee = _committee()
        outsider = _member('EXC-DIRECT1', 'Direct URL Outsider')
        event = _event(self.officer, committee=committee)

        self.client.login(username=outsider.username, password='committee-event-pass-12345!')
        resp = self.client.post(
            reverse('submit_excuse', args=[event.id]),
            data={'reason': 'x' * 20},
        )

        self.assertEqual(AttendanceExcuse.objects.filter(event=event, user=outsider).count(), 0)

    def test_committee_member_can_submit_excuse(self):
        committee_member = _member('EXC-CANSUB1', 'Can Submit')
        committee = _committee(members=[committee_member])
        event = _event(self.officer, committee=committee)

        self.client.login(username=committee_member.username, password='committee-event-pass-12345!')
        resp = self.client.post(
            reverse('submit_excuse', args=[event.id]),
            data={'reason': 'A perfectly good reason for missing it.'},
        )

        self.assertEqual(AttendanceExcuse.objects.filter(event=event, user=committee_member).count(), 1)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ReminderEligibilityScopingTests(TestCase):
    """`send_event_reminder_pushes` — the push/email reminder task."""

    def setUp(self):
        self.officer = _officer()
        mail.outbox = []

    def _due_event(self, committee=None, requires_signup=False):
        return _event(
            self.officer, committee=committee, requires_signup=requires_signup,
            date_time=timezone.now() + timedelta(hours=2),
            reminder_1_enabled=True, reminder_1_hours_before=24,
            reminder_2_enabled=False,
        )

    def test_only_committee_members_receive_the_push(self):
        committee_member = _member('REL-CM1', 'Committee Member', push=True)
        outsider = _member('REL-OUT1', 'Outsider', push=True)
        committee = _committee(members=[committee_member])
        event = self._due_event(committee=committee)

        send_event_reminder_pushes()

        recipient_ids = set(
            event.reminder_logs.get(reminder_slot=1).recipients.values_list('user_id', flat=True)
        )
        self.assertIn(committee_member.pk, recipient_ids)
        self.assertNotIn(outsider.pk, recipient_ids)

    def test_signed_up_outsider_receives_the_push(self):
        committee = _committee()
        outsider = _member('REL-SIGN1', 'Signed Up Outsider', push=True)
        event = self._due_event(committee=committee, requires_signup=True)
        EventSignup.objects.create(event=event, user=outsider)

        send_event_reminder_pushes()

        recipient_ids = set(
            event.reminder_logs.get(reminder_slot=1).recipients.values_list('user_id', flat=True)
        )
        self.assertIn(outsider.pk, recipient_ids)

    def test_visible_to_is_not_consulted_for_committee_events(self):
        """A committee event's `visible_to` (if set) doesn't further
        restrict reminder eligibility — `committee` is authoritative."""
        committee_member = _member('REL-VT1', 'Committee Member', push=True, member_type='Pledge')
        committee = _committee(members=[committee_member])
        event = self._due_event(committee=committee)
        event.visible_to = ['Officer']  # would normally exclude a Pledge
        event.save()

        send_event_reminder_pushes()

        recipient_ids = set(
            event.reminder_logs.get(reminder_slot=1).recipients.values_list('user_id', flat=True)
        )
        self.assertIn(committee_member.pk, recipient_ids)

    def test_chapter_wide_event_is_unaffected(self):
        """Regression check: ordinary events keep respecting `visible_to`
        exactly as before."""
        officer_member = _member('REL-CHAPOFF1', 'Officer Member', push=True, member_type='Officer')
        pledge_member = _member('REL-CHAPPLG1', 'Pledge Member', push=True, member_type='Pledge')
        event = self._due_event(committee=None)
        event.visible_to = ['Officer']
        event.save()

        send_event_reminder_pushes()

        recipient_ids = set(
            event.reminder_logs.get(reminder_slot=1).recipients.values_list('user_id', flat=True)
        )
        self.assertIn(officer_member.pk, recipient_ids)
        self.assertNotIn(pledge_member.pk, recipient_ids)
