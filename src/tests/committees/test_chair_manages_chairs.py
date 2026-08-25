"""
v3.26.6 — committee chairs can manage committee membership up to and
including other chairs.

Requested by Mason: chairs should be able to manage committee members "all
the way up to chairs". Before this, `committee_add_member` /
`committee_remove_member` (the views behind every "+ Add" / "Remove"
control on `committee_home.html`) gated on `is_vp or is_admin` only — a
plain chair was excluded, for every role type, not just 'chair'.

That was a live, pre-existing mismatch worth noting: `committee_home.py`'s
`can_manage` already includes `is_chair`, so `committee_home.html` was
already SHOWING a chair the "+ Add member" button and letting them open the
modal — the submit then silently failed with "You do not have permission
to manage this committee." This fix closes both gaps at once: it grants the
specific 'chair' capability Mason asked for, and it makes the member /
advisor / voter controls chairs could already see actually work.

`committee.is_chair()` (not a raw `.chairs.filter()` check) is used in both
views, matching `committee_chair_required` and everywhere else "chair-level"
is decided — which also means an exec-board committee's plain members count
as chairs here too (`Committee.is_exec_board`), same as they do everywhere
else in this codebase. Tested explicitly below, since a raw `.chairs`
lookup would have missed that population silently.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser, Role


def make_user(uid, member_type='Member', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('chair-mgmt-test-pass-12345!')
    user.save()
    return user


class ChairsCanManageChairsTests(TestCase):
    def setUp(self):
        self.chair = make_user('cmc-chair')
        self.target = make_user('cmc-target')
        self.plain_member = make_user('cmc-plain')
        self.outsider = make_user('cmc-outsider')

        self.committee = Committee.objects.create(name='Recruitment', code='CMC')
        self.committee.chairs.add(self.chair)
        self.committee.members.add(self.chair, self.target, self.plain_member)

        self.client = Client()

    def test_a_chair_can_add_another_chair(self):
        self.client.force_login(self.chair)
        response = self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': self.target.pk, 'role_type': 'chair'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMC']))
        self.assertTrue(self.committee.chairs.filter(pk=self.target.pk).exists())

    def test_a_chair_can_remove_another_chair(self):
        self.committee.chairs.add(self.target)
        self.client.force_login(self.chair)
        response = self.client.post(
            reverse('committee_remove_member', args=['CMC']),
            {'user_id': self.target.pk, 'role_type': 'chair'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMC']))
        self.assertFalse(self.committee.chairs.filter(pk=self.target.pk).exists())

    def test_a_chair_can_also_add_a_plain_member(self):
        """
        The pre-existing bug this also fixes: `committee_home.html` already
        showed chairs this exact control for every role type, not just
        'chair', and it silently failed for all of them.
        """
        new_person = make_user('cmc-newperson')
        self.client.force_login(self.chair)
        response = self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': new_person.pk, 'role_type': 'member'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMC']))
        self.assertTrue(self.committee.members.filter(pk=new_person.pk).exists())

    def test_a_chair_can_add_an_advisor_and_a_voter(self):
        advisor = make_user('cmc-advisor')
        voter = make_user('cmc-voter')
        self.client.force_login(self.chair)

        self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': advisor.pk, 'role_type': 'advisor'},
        )
        self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': voter.pk, 'role_type': 'voter'},
        )
        self.assertTrue(self.committee.advisors.filter(pk=advisor.pk).exists())
        self.assertTrue(self.committee.voting_members.filter(pk=voter.pk).exists())

    def test_a_plain_member_still_cannot_manage_chairs(self):
        """The widening is to chairs, not to every committee member."""
        self.client.force_login(self.plain_member)
        response = self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': self.target.pk, 'role_type': 'chair'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMC']))
        self.assertFalse(self.committee.chairs.filter(pk=self.target.pk).exists())

    def test_someone_outside_the_committee_entirely_still_cannot(self):
        # Not `assertRedirects` — an outsider is bounced from `committee_home`
        # itself too (no access to the page at all), a second redirect
        # `assertRedirects`'s single-hop follow doesn't expect. The
        # permission check under test is the first redirect; asserting on
        # the status code and the unchanged DB state is what this test is
        # actually about.
        self.client.force_login(self.outsider)
        response = self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': self.target.pk, 'role_type': 'chair'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('committee_home', args=['CMC']))
        self.assertFalse(self.committee.chairs.filter(pk=self.target.pk).exists())

    def test_the_committee_vp_can_still_manage_chairs(self):
        """Regression check: VP access predates this change and must survive it."""
        role = Role.objects.create(name='CMC VP', code='cmcvp')
        vp = make_user('cmc-vp')
        vp.roles.add(role)
        self.committee.role = role
        self.committee.save(update_fields=['role'])

        self.client.force_login(vp)
        response = self.client.post(
            reverse('committee_add_member', args=['CMC']),
            {'user_id': self.target.pk, 'role_type': 'chair'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMC']))
        self.assertTrue(self.committee.chairs.filter(pk=self.target.pk).exists())

    def test_a_site_admin_can_still_manage_chairs(self):
        """Regression check: site-admin access predates this change too."""
        admin = make_user('cmc-admin', member_type='Officer', is_admin=True)
        self.client.force_login(admin)
        response = self.client.post(
            reverse('committee_remove_member', args=['CMC']),
            {'user_id': self.chair.pk, 'role_type': 'chair'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMC']))
        self.assertFalse(self.committee.chairs.filter(pk=self.chair.pk).exists())

    def test_an_exec_board_plain_member_counts_as_a_chair_here_too(self):
        """
        `committee.is_chair()` treats every member of an exec-board
        committee as chair-level, everywhere else in this codebase. Using
        that method (rather than a raw `.chairs.filter()` check) here means
        this population is covered too, without a second code path.
        """
        exec_committee = Committee.objects.create(
            name='Exec Board', code='CMCX', is_exec_board=True,
        )
        exec_member = make_user('cmc-execmember')
        exec_committee.members.add(exec_member)
        new_chair = make_user('cmc-newchair')
        exec_committee.members.add(new_chair)

        self.client.force_login(exec_member)
        response = self.client.post(
            reverse('committee_add_member', args=['CMCX']),
            {'user_id': new_chair.pk, 'role_type': 'chair'},
        )
        self.assertRedirects(response, reverse('committee_home', args=['CMCX']))
        self.assertTrue(exec_committee.chairs.filter(pk=new_chair.pk).exists())
