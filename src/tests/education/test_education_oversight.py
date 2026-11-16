"""
The Education Oversight Committee (11-16-26).

* With no VP of Education the committee is "Education Oversight Committee";
  electing one (even mid-term) brings its usual name back.
* Its chair is a Chair member type (officer tools: events, announcements) and
  has the education admin's access; stepping down reverts it. An officer's
  manual change on Manage committees does the same.
* The committee elects its chair in the app: secret, one vote each, closes
  when everyone has voted, a tie elects nobody.

    python manage.py test src.tests.education.test_education_oversight
"""
from django.test import Client, TestCase, override_settings

from src import committee_chair_election as ce
from src.education_oversight import OVERSIGHT_NAME, in_oversight, sync_name
from src.models import Committee, CommitteeChairElection, ParliamentUser, Role
from src.permissions import user_is_officer_or_chair
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view.committee.education import _get_education_access

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        self.vpe_role = Role.objects.create(name='Vice President of Education', code='VPEO')
        self.committee = Committee.objects.create(name='Education Committee', code='EDUO',
                                                  is_education_committee=True, role=self.vpe_role)
        self.a, self.b, self.c = (make_user(f'eo-{x}', name=f'Member {x.upper()}') for x in 'abc')
        self.committee.members.add(self.a, self.b, self.c)

    def fresh(self):
        return Committee.objects.get(pk=self.committee.pk)


class NameTests(_Base):

    def test_the_name_follows_the_vp_of_education(self):
        self.assertTrue(in_oversight(self.committee))
        sync_name(self.committee)
        self.assertEqual(self.fresh().name, OVERSIGHT_NAME)
        # The chapter elects a VP mid-term: the usual name comes back on its own.
        vp = make_user('eo-vp', name='Victor VP')
        vp.roles.add(self.vpe_role)
        self.assertEqual(self.fresh().name, 'Education Committee')
        self.assertFalse(in_oversight(self.fresh()))
        # And the role ending (a transition) makes it the oversight committee again.
        vp.roles.remove(self.vpe_role)
        self.assertEqual(self.fresh().name, OVERSIGHT_NAME)

    def test_other_committees_are_never_renamed(self):
        social = Committee.objects.create(name='Social', code='SOCO')
        self.assertFalse(sync_name(social))
        self.assertEqual(Committee.objects.get(pk=social.pk).name, 'Social')


class ChairTests(_Base):

    def test_the_chair_gets_officer_tools_and_loses_them_on_stepping_down(self):
        self.assertFalse(user_is_officer_or_chair(self.a))
        self.committee.chairs.add(self.a)          # the admin override, as Manage committees does
        self.a.refresh_from_db()
        self.assertEqual(self.a.member_type, 'Chair')
        self.assertTrue(user_is_officer_or_chair(self.a))
        self.assertTrue(_get_education_access(self.a, self.committee)['is_full_access'])
        self.committee.chairs.set([self.b])
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.member_type, self.b.member_type), ('Member', 'Chair'))
        self.committee.chairs.clear()
        self.b.refresh_from_db()
        self.assertEqual(self.b.member_type, 'Member')

    def test_someone_with_another_role_keeps_their_type(self):
        historian = Role.objects.create(name='Historian', code='HISTO')
        self.a.roles.add(historian)
        ParliamentUser.objects.filter(pk=self.a.pk).update(member_type='Chair')
        self.a.refresh_from_db()
        self.committee.chairs.add(self.a)
        self.committee.chairs.remove(self.a)
        self.a.refresh_from_db()
        self.assertEqual(self.a.member_type, 'Chair')

    def test_other_committees_chairs_are_unchanged(self):
        social = Committee.objects.create(name='Social', code='SOCO2')
        social.chairs.add(self.a)
        self.a.refresh_from_db()
        self.assertEqual(self.a.member_type, 'Member')


class ElectionTests(_Base):

    def setUp(self):
        super().setUp()
        self.api = Client()

    def call(self, user, body=None):
        _, access, _ = make_device(user)
        if body is None:
            return self.api.get(f'{API}committees/EDUO/chair-election/', **bearer(access))
        return self.api.post(f'{API}committees/EDUO/chair-election/', body, content_type='application/json',
                             **bearer(access))

    def test_the_committee_elects_its_chair(self):
        state = self.call(self.a).json()
        self.assertTrue(state['allowed'] and state['can_open'])
        self.assertEqual(len(state['candidates']), 3)
        outsider = make_user('eo-out')
        self.assertEqual(self.call(outsider, {'action': 'open'}).status_code, 403)

        state = self.call(self.a, {'action': 'open'}).json()
        self.assertEqual(state['election']['status'], 'open')
        self.assertEqual(self.call(self.b, {'action': 'open'}).json()['error'], 'already_open')
        self.assertEqual(self.call(outsider, {'action': 'vote', 'user_id': self.a.pk}).status_code, 403)
        self.assertEqual(self.call(self.a, {'action': 'vote', 'user_id': outsider.pk}).status_code, 400)

        state = self.call(self.a, {'action': 'vote', 'user_id': self.b.pk}).json()
        self.assertEqual(state['election']['my_vote'], 'Member B')
        self.assertIsNone(state['election']['totals'])                       # secret while open
        self.assertEqual(self.call(self.a, {'action': 'vote', 'user_id': self.c.pk}).json()['error'],
                         'already_voted')
        self.call(self.b, {'action': 'vote', 'user_id': self.b.pk})
        state = self.call(self.c, {'action': 'vote', 'user_id': self.a.pk}).json()   # the last vote closes it
        e = state['election']
        self.assertEqual((e['status'], e['winner']['name']), ('closed', 'Member B'))
        self.assertEqual(e['totals'][0], {'name': 'Member B', 'votes': 2})
        self.assertEqual(list(self.fresh().chairs.all()), [self.b])
        self.b.refresh_from_db()
        self.assertEqual(self.b.member_type, 'Chair')
        self.assertEqual(self.fresh().name, OVERSIGHT_NAME)                  # no VP of Education

    def test_a_tie_elects_nobody_and_only_the_opener_chair_or_admin_closes(self):
        self.call(self.a, {'action': 'open'})
        self.call(self.a, {'action': 'vote', 'user_id': self.a.pk})
        self.call(self.b, {'action': 'vote', 'user_id': self.b.pk})
        self.assertEqual(self.call(self.c, {'action': 'close'}).status_code, 403)
        state = self.call(self.a, {'action': 'close'}).json()
        self.assertIsNone(state['election']['winner'])
        self.assertFalse(self.fresh().chairs.exists())
        self.assertTrue(state['can_open'])                                   # run it again

    def test_only_the_education_committee_elects_in_the_app(self):
        social = Committee.objects.create(name='Social', code='SOCO3')
        social.members.add(self.a, self.b)
        with self.assertRaises(ce.ChairElectionError):
            ce.open_election(social, self.a)
        self.assertFalse(CommitteeChairElection.objects.exists())
