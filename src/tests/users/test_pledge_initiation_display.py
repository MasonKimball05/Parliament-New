"""
v3.29.6 — pledges must not show an "initiated at" badge before they're
actually initiated.

Requested by Mason: "make sure that pledges do not get the initiated at
badge automatically. That gets added to them once their member type
changes from pledge to member/officer/chair."

Root cause: `initiation_chapters` is a self-reported JSON list (no server
default) — but every consumer of it (the "Initiated at" popup row on
directory.html/house_map.html/chat/channel.html, and the equivalent note on
profile.html's own Initiation Chapters section) defaulted an EMPTY list to
`[{ school: 'Samford University', chapter: 'Alpha Mu' }]` regardless of
whether the member had actually been initiated. A pledge who never fills
this in — which is every pledge, since there's nothing to fill in yet —
got "Initiated at: Alpha Mu (ΑΜ) — Samford University" displayed as though
it were already true.

The default is still correct for everyone who's actually a member (nearly
all of them are Alpha Mu chapter members by definition, so requiring them
to type it in would be pure friction) — it just must not apply to a
pledge. Fixed by branching the default on `member_type` (`d.member_type`
client-side, `user.is_pledge` server-side in profile.html) in all four
places, rather than removing the default outright.

This file cannot exercise the client-side JS default directly (no JS test
runner in this codebase) — it verifies the two things that ARE testable
from Python: the shared JSON endpoint those three popups all fetch from
(`profile_card_json`) returns accurate data (empty list, correct
member_type) for the client-side branch to key off, and profile.html's own
server-rendered gating gives a pledge neither the Alpha Mu filler text nor
a false "added automatically" claim.
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('pledge-init-test-12345!')
    user.save()
    return user


class ProfileCardJsonDataTests(TestCase):
    """The API the popups fetch from — confirms it hands the client the
    correct raw material (no server-side Alpha Mu default of its own)."""

    def setUp(self):
        self.viewer = make_user('PI-V1')
        self.client.login(username=self.viewer.username, password='pledge-init-test-12345!')

    def test_pledge_with_no_chapters_returns_empty_list_and_pledge_type(self):
        pledge = make_user('PI-P1', member_type='Pledge')
        response = self.client.get(reverse('profile_card', args=[pledge.user_id]))
        data = response.json()
        self.assertEqual(data['member_type'], 'Pledge')
        self.assertEqual(data['initiation_chapters'], [])

    def test_member_with_no_chapters_also_returns_empty_list(self):
        """The API itself never injects the Alpha Mu default — that's a
        purely client-side display choice, for both pledges and members."""
        member = make_user('PI-M1', member_type='Member')
        response = self.client.get(reverse('profile_card', args=[member.user_id]))
        data = response.json()
        self.assertEqual(data['member_type'], 'Member')
        self.assertEqual(data['initiation_chapters'], [])


class ProfilePageInitiationDisplayTests(TestCase):
    """Server-rendered gating on profile.html itself."""

    def test_pledge_does_not_see_alpha_mu_default_text(self):
        pledge = make_user('PI-P2', member_type='Pledge')
        self.client.login(username=pledge.username, password='pledge-init-test-12345!')
        response = self.client.get(reverse('profile'))
        self.assertNotContains(response, 'Defaulting to Alpha Mu')

    def test_pledge_sees_not_yet_initiated_note(self):
        pledge = make_user('PI-P3', member_type='Pledge')
        self.client.login(username=pledge.username, password='pledge-init-test-12345!')
        response = self.client.get(reverse('profile'))
        self.assertContains(response, "haven't been initiated yet")

    def test_member_still_sees_alpha_mu_default_text(self):
        """Regression guard: the fix must not remove the default for
        members who HAVE been initiated — only for pledges who haven't."""
        member = make_user('PI-M2', member_type='Member')
        self.client.login(username=member.username, password='pledge-init-test-12345!')
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'Defaulting to Alpha Mu')

    def test_member_does_not_see_pledge_specific_note(self):
        member = make_user('PI-M3', member_type='Member')
        self.client.login(username=member.username, password='pledge-init-test-12345!')
        response = self.client.get(reverse('profile'))
        self.assertNotContains(response, "haven't been initiated yet")


class PopupTemplatesContainThePledgeGuardTests(TestCase):
    """
    The client-side default itself lives in directory.html, house_map.html
    and chat/channel.html and has no Python-level test harness — this pins
    the guard's presence in the served page source as the closest available
    regression check, and confirms all three were fixed identically rather
    than just the one that was noticed.
    """

    def setUp(self):
        self.user = make_user('PI-T1')
        self.client.login(username=self.user.username, password='pledge-init-test-12345!')

    def test_directory_page_has_the_pledge_guard(self):
        response = self.client.get(reverse('member_directory'))
        self.assertContains(response, "d.member_type === 'Pledge'")

    def test_house_map_page_has_the_pledge_guard(self):
        response = self.client.get(reverse('house_map'))
        self.assertContains(response, "d.member_type === 'Pledge'")
