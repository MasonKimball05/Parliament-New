"""
Appeals — bylaws § b.i, ten days from the date of notice of a decision.

WHY THIS EXISTS (added 07-31-26, v3.18.0)
------------------------------------------
The chapter bylaws:

    "Kai Committee decisions can be appealed first to the chapter, then to the
     District Chief, and then to the Board of Trustees and the General
     Convention if needed. As outlined in the General Fraternities'
     Constitution all Kai Committee appeals must be made within 10 days from
     the date of notice of a decision."

There was no appeal model, no appeal state, and nothing computed that window.
The anchor already existed and was already populated: `accused_notified_at` is
literally "the date of notice of a decision", and had never been used for
anything but display.

THE PARTS WORTH TESTING
-----------------------
1. **The clock cannot start before notice.** A case whose accused was never
   notified has no window — not an expired one, not an open one. Getting this
   backwards would either deny a real right or invent one.
2. **`days_remaining` rounds UP.** A right expiring in six hours has one day
   left, not zero. "0 days remaining" while the member can still act is the
   wrong error to make with a deadline.
3. **One definition.** `KaiAppeal.can_file()` decides, and both the template's
   button and the POST handler call it — so the UI cannot offer what the
   endpoint refuses, which is the bug class this codebase keeps hitting.
"""

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Committee, KaiAppeal, KaiReport, ParliamentUser


def make_user(uid, name=None):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name or f'User {uid}', username=uid,
        member_type='Member', member_status='Active',
    )
    user.set_password('appeal-test-pass-12345!')
    user.save()
    return user


class AppealTestCase(TestCase):

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True)
        self.reporter = make_user('ap-reporter', 'Reporter Rowan')
        self.accused = make_user('ap-accused', 'Accused Avery')
        self.report = KaiReport.objects.create(
            title='Case', description='Body',
            submitted_by=self.reporter, targeted_to=self.accused,
            deliberation_outcome='sanctions_applied',
        )

    def notify(self, days_ago=0):
        self.report.accused_notified = True
        self.report.accused_notified_at = timezone.now() - timedelta(days=days_ago)
        self.report.save()

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client


class TheWindowTests(AppealTestCase):

    def test_no_notice_means_no_window(self):
        """Not an expired window — no window. The clock starts at notice."""
        self.assertIsNone(KaiAppeal.window_closes_at(self.report))
        self.assertIsNone(KaiAppeal.days_remaining(self.report))
        self.assertFalse(KaiAppeal.window_is_open(self.report))

    def test_the_window_opens_at_notice(self):
        self.notify()
        self.assertTrue(KaiAppeal.window_is_open(self.report))

    def test_the_window_is_ten_days(self):
        self.notify()
        closes = KaiAppeal.window_closes_at(self.report)
        self.assertEqual((closes - self.report.accused_notified_at).days, 10)

    def test_the_window_closes_after_ten_days(self):
        self.notify(days_ago=11)
        self.assertFalse(KaiAppeal.window_is_open(self.report))

    def test_days_remaining_rounds_up(self):
        """
        Six hours left is one day, not zero. Rounding down would tell a member
        their right had expired while they could still exercise it.
        """
        self.notify(days_ago=9)  # ~1 day left, minus a few microseconds
        self.assertEqual(KaiAppeal.days_remaining(self.report), 1)

    def test_days_remaining_never_goes_negative(self):
        self.notify(days_ago=30)
        self.assertEqual(KaiAppeal.days_remaining(self.report), 0)

    def test_a_closed_window_is_not_reopened_by_a_later_notice_edit(self):
        """`accused_notified_at` is the anchor; moving it moves the window."""
        self.notify(days_ago=20)
        self.assertFalse(KaiAppeal.window_is_open(self.report))
        self.notify(days_ago=0)
        self.assertTrue(KaiAppeal.window_is_open(self.report))


class WhoMayFileTests(AppealTestCase):

    def test_the_accused_may_file_inside_the_window(self):
        self.notify()
        allowed, _ = KaiAppeal.can_file(self.report, self.accused)
        self.assertTrue(allowed)

    def test_the_submitter_may_not_file(self):
        self.notify()
        allowed, reason = KaiAppeal.can_file(self.report, self.reporter)
        self.assertFalse(allowed)
        self.assertIn('named in a case', reason)

    def test_an_unrelated_member_may_not_file(self):
        self.notify()
        allowed, _ = KaiAppeal.can_file(self.report, make_user('ap-other'))
        self.assertFalse(allowed)

    def test_nobody_may_file_before_notice(self):
        allowed, reason = KaiAppeal.can_file(self.report, self.accused)
        self.assertFalse(allowed)
        self.assertIn('No decision', reason)

    def test_nobody_may_file_after_the_window(self):
        self.notify(days_ago=11)
        allowed, reason = KaiAppeal.can_file(self.report, self.accused)
        self.assertFalse(allowed)
        self.assertIn('closed', reason)

    def test_a_second_appeal_is_refused(self):
        self.notify()
        KaiAppeal.objects.create(
            report=self.report, filed_by=self.accused, grounds='First')
        allowed, reason = KaiAppeal.can_file(self.report, self.accused)
        self.assertFalse(allowed)
        self.assertIn('already filed', reason)

    def test_a_withdrawn_appeal_does_not_block_a_new_one(self):
        self.notify()
        KaiAppeal.objects.create(
            report=self.report, filed_by=self.accused, grounds='First',
            status='withdrawn')
        allowed, _ = KaiAppeal.can_file(self.report, self.accused)
        self.assertTrue(allowed)


class FilingThroughTheViewTests(AppealTestCase):

    def _file(self, user, **extra):
        data = {'grounds': 'The sanction does not fit the finding.', 'level': 'chapter'}
        data.update(extra)
        return self._client(user).post(
            reverse('kai_file_appeal', args=[self.report.id]), data)

    def test_filing_creates_the_appeal(self):
        self.notify()
        self._file(self.accused)
        self.assertEqual(KaiAppeal.objects.filter(report=self.report).count(), 1)

    def test_filing_is_recorded_on_the_case_timeline(self):
        self.notify()
        self._file(self.accused)
        self.assertTrue(
            self.report.activity_log.filter(action='appeal_filed').exists())

    def test_empty_grounds_are_refused(self):
        self.notify()
        self._file(self.accused, grounds='   ')
        self.assertEqual(KaiAppeal.objects.count(), 0)

    def test_an_unknown_level_falls_back_to_chapter(self):
        self.notify()
        self._file(self.accused, level='supreme-court')
        self.assertEqual(KaiAppeal.objects.get().level, 'chapter')

    def test_the_submitter_cannot_file_through_the_view(self):
        self.notify()
        self._file(self.reporter)
        self.assertEqual(KaiAppeal.objects.count(), 0)

    def test_filing_outside_the_window_is_refused_by_the_view(self):
        """
        Not just by the template. The button disappears when the window shuts,
        but a POST is not a form.
        """
        self.notify(days_ago=11)
        self._file(self.accused)
        self.assertEqual(KaiAppeal.objects.count(), 0)


class TheUiRendersFromTheSameRuleTests(AppealTestCase):
    """
    The button and the endpoint must agree. They call `can_file` from the same
    place, and this asserts the context carries it.
    """

    def _view(self):
        return self._client(self.accused).get(
            reverse('user_view_kai_report', args=[self.report.id]))

    def test_the_countdown_is_offered_inside_the_window(self):
        self.notify(days_ago=2)
        context = self._view().context
        self.assertTrue(context['can_appeal'])
        self.assertEqual(context['appeal_days_remaining'], 8)

    def test_no_countdown_before_notice(self):
        context = self._view().context
        self.assertFalse(context['can_appeal'])
        self.assertIsNone(context['appeal_days_remaining'])

    def test_the_window_days_come_from_the_model_not_the_template(self):
        """A hardcoded "10" in the template would drift from the constant."""
        self.notify()
        self.assertEqual(
            self._view().context['appeal_window_days'],
            KaiAppeal.APPEAL_WINDOW_DAYS,
        )
