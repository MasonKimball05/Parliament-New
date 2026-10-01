"""
Pledge bigs on the education dashboard (v3.39.0, 10-01-26).

Mason: "add a feature to the education dashboard to be able to set pledge
bigs as well as a function to just set it vs when it goes live so it's on the
site outside the dashboard so the pledges can see it", plus a card on the
pledge's My Tasks page once his big is set.

Decisions (Mason, 10-01-26): drafts are visible to `can_manage_tasks` only;
after a reveal the pledge can't change his big on his own profile; no
notifications (the reveal is a ritual).

The promise these tests guard: **a draft pairing is not visible anywhere
outside the dashboard**, and a reveal puts it everywhere at once.

Run with: python manage.py test src.tests.education.test_pledge_bigs
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.big_reveal import reveal, reveal_due_bigs
from src.models import EducationMemberPermission, PledgeBigAssignment
from src.tests.education._fixtures import EducationFixtureMixin, make_user


class _BigsBase(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.code = self.committee.code

    def _url(self, name, pledge=None):
        args = [self.code] + ([pledge.pk] if pledge else [])
        return reverse(name, args=args)

    def _draft(self, **kw):
        return PledgeBigAssignment.objects.create(
            committee=self.committee, pledge=self.pledge, big=self.brother,
            created_by=self.chair, **kw,
        )

    def _refresh(self):
        self.pledge.refresh_from_db()


class SettingABigTests(_BigsBase):

    def test_setting_a_big_makes_a_draft_and_leaves_the_profile_alone(self):
        response = self.client.post(self._url('education_set_big', self.pledge),
                                    {'big': self.brother.pk, 'reveal_mode': 'manual'})
        self.assertEqual(response.status_code, 302)
        a = PledgeBigAssignment.objects.get(pledge=self.pledge)
        self.assertEqual(a.big, self.brother)
        self.assertFalse(a.is_revealed)
        self._refresh()
        self.assertIsNone(self.pledge.big_brother)

    def test_a_pledge_cannot_be_picked_as_a_big(self):
        self.client.post(self._url('education_set_big', self.pledge),
                         {'big': self.other_pledge.pk, 'reveal_mode': 'manual'})
        self.assertFalse(PledgeBigAssignment.objects.exists())

    def test_timed_mode_needs_a_time(self):
        self.client.post(self._url('education_set_big', self.pledge),
                         {'big': self.brother.pk, 'reveal_mode': 'timed', 'reveals_at': ''})
        self.assertFalse(PledgeBigAssignment.objects.exists())

    def test_changing_the_big_on_a_revealed_pairing_updates_the_profile(self):
        reveal(self._draft())
        new_big = make_user('9005', 'Another Brother')
        self.client.post(self._url('education_set_big', self.pledge),
                         {'big': new_big.pk, 'reveal_mode': 'manual'})
        self._refresh()
        self.assertEqual(self.pledge.big_brother, new_big)


class RevealTests(_BigsBase):

    def test_reveal_now_copies_the_big_to_the_profile(self):
        self._draft()
        self.client.post(self._url('education_reveal_big', self.pledge))
        self._refresh()
        self.assertEqual(self.pledge.big_brother, self.brother)
        self.assertTrue(PledgeBigAssignment.objects.get().is_revealed)

    def test_reveal_is_idempotent(self):
        a = self._draft()
        self.assertTrue(reveal(a))
        self.assertFalse(reveal(PledgeBigAssignment.objects.get(pk=a.pk)))

    def test_reveal_all(self):
        self._draft()
        brother2 = make_user('9006', 'Brother Two')
        PledgeBigAssignment.objects.create(committee=self.committee, pledge=self.other_pledge, big=brother2)
        self.client.post(self._url('education_reveal_all_bigs'))
        self._refresh()
        self.other_pledge.refresh_from_db()
        self.assertEqual(self.pledge.big_brother, self.brother)
        self.assertEqual(self.other_pledge.big_brother, brother2)

    def test_a_timed_reveal_waits_for_its_time(self):
        self._draft(reveal_mode='timed', reveals_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(reveal_due_bigs(), 0)
        self._refresh()
        self.assertIsNone(self.pledge.big_brother)
        self.assertEqual(reveal_due_bigs(now=timezone.now() + timedelta(hours=2)), 1)
        self._refresh()
        self.assertEqual(self.pledge.big_brother, self.brother)

    def test_my_tasks_applies_a_due_timed_reveal_on_load(self):
        """Backstop for a stopped Celery beat."""
        self._draft(reveal_mode='timed', reveals_at=timezone.now() - timedelta(minutes=1))
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.context['my_big'], self.brother)

    def test_hide_takes_it_back_off_the_profile(self):
        reveal(self._draft())
        self.client.post(self._url('education_unreveal_big', self.pledge))
        self._refresh()
        self.assertIsNone(self.pledge.big_brother)
        a = PledgeBigAssignment.objects.get()
        self.assertFalse(a.is_revealed)
        self.assertEqual(a.reveal_mode, 'manual')

    def test_remove_a_revealed_pairing_clears_the_profile(self):
        reveal(self._draft())
        self.client.post(self._url('education_delete_big', self.pledge))
        self._refresh()
        self.assertIsNone(self.pledge.big_brother)
        self.assertFalse(PledgeBigAssignment.objects.exists())


class DraftsStaySecretTests(_BigsBase):

    def test_my_tasks_does_not_show_a_draft(self):
        self._draft()
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertIsNone(response.context['my_big'])
        self.assertNotContains(response, self.brother.name)

    def test_my_tasks_shows_a_revealed_big(self):
        reveal(self._draft())
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertContains(response, 'Your Big Brother')
        self.assertContains(response, self.brother.name)

    def test_view_as_pledge_preview_does_not_show_a_draft(self):
        """The preview is open to any education permission, not just manage-tasks."""
        self._draft()
        response = self.client.get(reverse('education_preview_pledge_tasks',
                                           args=[self.code, self.pledge.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.brother.name)

    def test_a_grader_without_manage_tasks_does_not_see_drafts(self):
        self._draft()
        grader = make_user('9010', 'Grader')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=grader,
            can_view_submissions=True, can_grade_submissions=True, can_manage_tasks=False,
        )
        self.client.force_login(grader)
        response = self.client.get(self._url('education_home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['big_rows'], [])
        self.assertNotContains(response, 'id="bigs"')

    def test_a_grader_cannot_set_or_reveal(self):
        a = self._draft()
        grader = make_user('9010', 'Grader')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=grader, can_view_submissions=True,
        )
        self.client.force_login(grader)
        self.assertEqual(self.client.post(self._url('education_reveal_big', self.pledge)).status_code, 403)
        self.assertEqual(self.client.post(self._url('education_set_big', self.pledge),
                                          {'big': grader.pk}).status_code, 403)
        a.refresh_from_db()
        self.assertFalse(a.is_revealed)
        self.assertEqual(a.big, self.brother)

    def test_the_chair_sees_the_draft_on_the_dashboard(self):
        self._draft()
        response = self.client.get(self._url('education_home'))
        self.assertContains(response, 'id="bigs"')
        rows = {r['pledge'].pk: r for r in response.context['big_rows']}
        self.assertEqual(rows[self.pledge.pk]['assignment'].big, self.brother)


class ProfileLockTests(_BigsBase):

    def _post_profile(self, **extra):
        data = {'extended_profile_submit': '1', 'about_me': 'hi'}
        data.update(extra)
        return self.client.post(reverse('profile'), data)

    def test_before_a_reveal_the_pledge_can_still_set_his_own_big(self):
        self._draft()
        self.client.force_login(self.pledge)
        self._post_profile(big_brother=self.chair.pk)
        self._refresh()
        self.assertEqual(self.pledge.big_brother, self.chair)

    def test_after_a_reveal_the_profile_cannot_change_or_clear_it(self):
        reveal(self._draft())
        self.client.force_login(self.pledge)
        self._post_profile(big_brother=self.chair.pk)
        self._refresh()
        self.assertEqual(self.pledge.big_brother, self.brother)
        self._post_profile()   # field absent, as on the locked form
        self._refresh()
        self.assertEqual(self.pledge.big_brother, self.brother)

    def test_the_locked_profile_shows_no_select(self):
        reveal(self._draft())
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('profile'))
        self.assertTrue(response.context['big_locked'])
        self.assertNotContains(response, 'name="big_brother"')
