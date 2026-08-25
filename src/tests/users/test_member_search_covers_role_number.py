"""
If a search box can find a member by `user_id`, it must also find him by
`role_number`.

⚠️ THE PROPERTY, AND WHY IT ONLY BECAME A PROPERTY IN v3.23.0.

Until v3.23.0, initiation *moved the primary key*: a pledge's `user_id` was
replaced with his roll number, so `user_id` and `role_number` held the same
string for every initiated member and searching either one found everybody.
Four member-search surfaces were written against `user_id` alone and worked
perfectly for years.

v3.23.0 correctly stopped doing that. `user_id` is now an opaque surrogate
(`P-C7JKZY`) assigned at creation and never changed, and `role_number` is the
roll number — the thing 32 templates render, the thing a member is called, and
the thing an officer types into a search box.

> **A search box is a reader of the identifier scheme.** v3.24.0 asked *who else
> needed to be told* and found three home-page badges printing the pk. It did
> not ask the same question of the places that take a number as INPUT.

What made this the nastiest possible shape: every member who exists **today**
still has a legacy `user_id` equal to his roll number, so all four surfaces keep
working on the current roster and fail only for members initiated from now on.
It would have surfaced months later as "search is broken for the new guys",
long after the change that caused it.

Reproduced 08-23-26 through the real endpoint before the fix: an officer
searching `/search/?q=173` for a member with `user_id='P-C7JKZY'` and
`role_number='173'` got no result, while searching his name got him.

⚠️ WHAT THIS SCAN DOES NOT COVER. It reads `icontains` lookups on `user_id`,
which is how every member search in this app is spelled. A search built some
other way — a raw `SQL LIKE`, a `SearchVector`, an `__iexact` — is invisible to
it, and the behavioural test below is the backstop for the one surface it can
reach without a slating fixture.
"""
import ast
import os

from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser

#: Functions that search `user_id` and legitimately do NOT want `role_number`,
#: with the reason. A ratchet: an entry here is a claim, and it has to be one
#: somebody can read.
#:
#: Empty is the target state and is currently the actual state. It exists so
#: that the answer to a future failure is "write the reason down", not "delete
#: the test".
_EXEMPT = {
    # (relative path, enclosing function name): 'reason'
}


def _functions_searching_user_id(root='src'):
    """
    Every function that filters members on a `user_id` *text* lookup without
    also offering the same lookup on `role_number`.

    Keyed on the enclosing function rather than the file, for the reason
    `test_user_id_is_permanent.py` records: a file-level exemption swallows
    functions nobody thought about.
    """
    offenders = []

    def keyword_names(node):
        return {
            kw.arg for kw in node.keywords
            if isinstance(kw.arg, str)
        }

    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith('.py') or name.startswith('test_'):
                continue
            path = os.path.join(dirpath, name).replace(os.sep, '/')
            # Split rather than substring-match: the literal that would read
            # most naturally here is a slash-delimited path, and
            # `test_hardcoded_urls` correctly flags any such literal in the tree
            # as an unresolvable site path. It caught this on its first run.
            if 'migrations' in path.split('/'):
                continue
            try:
                tree = ast.parse(open(path, encoding='utf-8',
                                      errors='ignore').read(), filename=path)
            except SyntaxError:                     # pragma: no cover
                continue

            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef,
                                             ast.AsyncFunctionDef)):
                    continue
                lookups = set()
                for node in ast.walk(function):
                    if isinstance(node, ast.Call):
                        lookups |= keyword_names(node)

                searches_key = any(
                    lookup.endswith('user_id__icontains') for lookup in lookups
                )
                if not searches_key:
                    continue
                searches_roll = any(
                    lookup.endswith('role_number__icontains')
                    for lookup in lookups
                )
                if searches_roll:
                    continue
                if (path, function.name) in _EXEMPT:
                    continue
                offenders.append(f'{path}:{function.lineno}  {function.name}()')

    return sorted(offenders)


class EverySearchBoxKnowsTheRollNumberTests(TestCase):

    def test_no_member_search_looks_at_the_key_alone(self):
        offenders = _functions_searching_user_id()

        self.assertEqual(
            offenders, [],
            'These search a member by `user_id` but not by `role_number`:\n  '
            + '\n  '.join(offenders)
            + '\n\nSince v3.23.0 `user_id` is an opaque surrogate assigned at '
              'creation (`P-C7JKZY`) and `role_number` is the roll number a '
              'member is actually called by. Every existing member still has a '
              'legacy `user_id` equal to his roll number, so this will look '
              'fine on today\'s roster and fail only for members initiated '
              'from now on.\n\n'
              'Add `role_number__icontains` to the same `Q(...)` chain, or add '
              'the function to _EXEMPT with a reason.',
        )

    def test_the_scan_can_see_an_offender(self):
        """
        ⚠️ CONTROL. A scan that matches nothing passes the assertion above no
        matter what the code does — the exact way `test_user_id_is_permanent`'s
        first regex stayed green over a button that moved primary keys.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'searchy.py'), 'w') as fh:
                fh.write(
                    'def search(q):\n'
                    '    return User.objects.filter(Q(user_id__icontains=q))\n'
                )
            with open(os.path.join(tmp, 'covered.py'), 'w') as fh:
                fh.write(
                    'def search(q):\n'
                    '    return User.objects.filter(\n'
                    '        Q(user_id__icontains=q) | Q(role_number__icontains=q))\n'
                )
            found = _functions_searching_user_id(tmp)

        self.assertTrue(any('searchy.py' in line for line in found),
                        f'the scan cannot see an uncovered search: {found}')
        self.assertFalse(any('covered.py' in line for line in found),
                         f'the scan flagged a covered search: {found}')

    def test_a_related_lookup_counts_too(self):
        """
        `applications_review` spells it `applicant__user_id__icontains`, so the
        match has to be on the suffix. A scan that only recognised the bare
        field name would have reported slating clean.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'related.py'), 'w') as fh:
                fh.write(
                    'def search(q):\n'
                    '    return App.objects.filter(Q(applicant__user_id__icontains=q))\n'
                )
            found = _functions_searching_user_id(tmp)

        self.assertTrue(any('related.py' in line for line in found), found)


class SearchingTheRollNumberFindsTheMemberTests(TestCase):
    """
    The behavioural half, through the real endpoint.

    ⚠️ THE FIXTURE IS THE POINT. `user_id` is deliberately NOT the roll number
    here, because that is what every member initiated from v3.23.0 onward looks
    like — and a fixture where the two are equal cannot tell the bug from the
    fix. Same rule as v3.21.1's `<int:>` routes, which shipped past a green
    suite on numeric pledge ids.
    """

    PASSWORD = 'roll-search-pass-12345!'

    @classmethod
    def setUpTestData(cls):
        cls.officer = ParliamentUser.objects.create_user(
            user_id='RS-OFFICER', password=cls.PASSWORD, name='Officer Oak',
            username='rs_officer', member_type='Officer', is_admin=True,
        )
        cls.brother = ParliamentUser.objects.create_user(
            user_id='P-C7JKZY', password=cls.PASSWORD,
            name='Bartholomew Brother', username='rs_brother',
            member_type='Member', role_number='173',
        )

    def _search(self, query):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('global_search'), {'q': query})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_global_search_finds_a_member_by_roll_number(self):
        self.assertIn(
            'Bartholomew', self._search('173'),
            'Searching a member\'s roll number did not find him. Since v3.23.0 '
            'that number lives in `role_number`, not in the primary key.',
        )

    def test_global_search_still_finds_him_by_name(self):
        """CONTROL: the search works at all, so the test above means something."""
        self.assertIn('Bartholomew', self._search('Bartholomew'))

    def test_global_search_still_finds_him_by_the_internal_id(self):
        """
        CONTROL, and a deliberate one: `user_id` stays searchable. It is what
        appears in logs, in the admin and in a URL an officer may have pasted,
        so removing it would trade one lookup failure for another.
        """
        self.assertIn('Bartholomew', self._search('P-C7JKZY'))
