"""
v3.31.0 flagged, and deliberately didn't fix, a residual "×2 duplicate
query" redundancy on `committee_documents()`: `is_chair`/`can_delete` (via
`committee.is_chair(user)`) and `can_view_minutes` (via
`is_committee_member_or_above()` → `committee.is_member(user)`) each
re-derive membership/chair status through their own query, on top of the
`user_is_committee_member`/`user_is_committee_chair` facts the view already
computed once for `can_user_view()`.

Fixed here, in two pieces:

- `is_committee_member_or_above()` takes an optional `is_committee_member`
  kwarg (same `can_edit_any=`-style idiom as `can_edit_specific_minutes`)
  and skips its own `committee.is_member(user)` query when the caller
  already knows the answer. Always safe — `is_member()` has no exec-board
  wrinkle, so the two facts are identical in every case, not just usually.

- `is_chair` reuses `user_is_committee_chair` directly ONLY when
  `committee.is_exec_board` is False — the one case where `is_chair()`'s
  own logic (see `Committee.is_chair`) reduces to exactly the same raw
  chairs-table check `user_is_committee_chair` already ran. When
  `is_exec_board` is True, `is_chair()` also checks committee membership,
  so the view still calls the real (memoizing) method rather than silently
  narrowing who counts as a chair on those committees. `Committee.
  prime_chair_memo()` records the reused answer in `is_chair()`'s own
  per-request memo so `can_edit_committee_minutes()` — which calls
  `committee.is_chair(user)` independently a few lines later — gets the
  free cached path too, rather than just moving the second query there.

These tests exist because that exec-board boundary is exactly the kind of
thing a "just reuse the flag" optimization gets quietly wrong: a test
suite that only exercises non-exec-board committees would stay green while
silently downgrading every exec-board member's access on documents pages
system-wide.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, CommitteePermissions, ParliamentUser


def make_user(uid, member_type='Member'):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status='Active',
    )
    user.set_password('doc-chair-shortcut-test-pass-12345!')
    user.save()
    return user


class NonExecBoardCommitteeChairShortcutTests(TestCase):
    """The common case: `is_exec_board=False`, so the raw chairs-table fact
    the view already has IS the answer `committee.is_chair()` would give —
    reusing it, and priming the memo, must not change what the page shows
    or what `can_edit_committee_minutes()` decides a few lines later."""

    def setUp(self):
        self.chair = make_user('docsc-chair')
        self.plain_member = make_user('docsc-plain')
        self.committee = Committee.objects.create(
            name='Documents Test Committee', code='DOCSC', is_active=True,
        )
        assert self.committee.is_exec_board is False
        self.committee.chairs.add(self.chair)
        self.committee.members.add(self.chair, self.plain_member)
        for u in (self.chair, self.plain_member):
            CommitteePermissions.objects.create(committee=self.committee, user=u, can_view_docs=True)
        self.client = Client()

    def test_a_real_chair_can_delete_and_edit_minutes(self):
        self.client.force_login(self.chair)
        response = self.client.get(reverse('committee_documents', args=['DOCSC']))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_chair'])
        self.assertTrue(response.context['can_delete'])
        self.assertTrue(response.context['can_edit_minutes'])

    def test_a_plain_member_cannot_delete_or_edit_minutes(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('committee_documents', args=['DOCSC']))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_chair'])
        self.assertFalse(response.context['can_delete'])
        self.assertFalse(response.context['can_edit_minutes'])


class ExecBoardCommitteeChairShortcutTests(TestCase):
    """The boundary case: `is_exec_board=True`. A plain committee member who
    is NOT a designated chair must still be treated as chair-level (per
    `Committee.is_chair()`'s own exec-board branch) — the shortcut above
    must NOT engage here, or this population silently loses access."""

    def setUp(self):
        self.exec_member = make_user('docsc-execmem')  # on the committee, not a chair — the case the shortcut must not touch
        self.outsider = make_user('docsc-outsider')  # on neither members nor chairs
        self.committee = Committee.objects.create(
            name='Exec Board Test Committee', code='DOCSCX', is_active=True, is_exec_board=True,
        )
        self.committee.members.add(self.exec_member)
        for u in (self.exec_member, self.outsider):
            CommitteePermissions.objects.create(committee=self.committee, user=u, can_view_docs=True)
        self.client = Client()

    def test_an_exec_board_member_who_is_not_a_designated_chair_still_gets_chair_level_access(self):
        self.client.force_login(self.exec_member)
        response = self.client.get(reverse('committee_documents', args=['DOCSCX']))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.context['is_chair'],
            'An is_exec_board committee member must count as chair-level '
            '(Committee.is_chair()), even though the raw chairs-table check '
            'this view also runs for can_user_view() would say False — the '
            'chair-status shortcut must not engage for is_exec_board=True.',
        )
        self.assertTrue(response.context['can_delete'])
        self.assertTrue(response.context['can_edit_minutes'])

    def test_a_non_member_non_chair_outsider_is_not_chair_level_even_on_an_exec_board_committee(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('committee_documents', args=['DOCSCX']))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_chair'])
        self.assertFalse(response.context['can_delete'])


class PrimeChairMemoTests(TestCase):
    """`Committee.prime_chair_memo()` directly — the mechanism the view-level
    tests above exercise indirectly through a real request."""

    def setUp(self):
        self.user = make_user('docsc-memo')
        self.committee = Committee.objects.create(name='Memo Test', code='DOCSCM', is_active=True)

    def test_priming_short_circuits_a_later_is_chair_call(self):
        self.committee.prime_chair_memo(self.user, True)
        with self.assertNumQueries(0):
            self.assertTrue(self.committee.is_chair(self.user))

    def test_priming_false_is_also_honoured(self):
        self.committee.prime_chair_memo(self.user, False)
        with self.assertNumQueries(0):
            self.assertFalse(self.committee.is_chair(self.user))

    def test_a_fresh_call_without_priming_still_queries_and_is_correct(self):
        self.committee.chairs.add(self.user)
        with self.assertNumQueries(1):
            self.assertTrue(self.committee.is_chair(self.user))
