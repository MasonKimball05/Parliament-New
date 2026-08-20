"""
v3.21.1 — a route carrying a member's id may not use the `int` converter.

⚠️ THE BUG. `ParliamentUser.user_id` is a **CharField** primary key. Initiated
brothers carry a roll number, which looks numeric — but a **pledge carries
something like `P-C7JKZY`** until initiation. Three routes declared
`<int:…>` for a member id:

* `education_pledge_detail` — `NoReverseMatch`, a hard 500 on the education
  dashboard the moment a real pledge existed;
* `education_toggle_completion` — the completion grid. It builds its URL in
  JavaScript rather than with `{% url %}`, so instead of raising it 404'd
  **quietly**. It had presumably never worked for a real pledge;
* `get_member_adjustments` — service hours, outside education entirely.

⚠️ AND THE TESTS PASSED, because the fixture used numeric ids and carried a
comment asserting that real ones were numeric too. **A fixture that is easier
than production tests something else.** The numeric-brother/`P-`-pledge split is
the kind of fact that is obvious on the live site and invisible in a test
database somebody seeded by hand.

So this file does not check the three routes that were wrong. It derives the
population: every kwarg any view uses to look up a `ParliamentUser`, then every
URL pattern that captures one of those with the `int` converter. A fourth
instance fails the build.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Names that look like a user kwarg but are not — verified individually.
#: `announcement_id` is here because the loose scan below can pair it with a
#: nearby `ParliamentUser` reference; it is an Announcement pk and genuinely an
#: integer.
NOT_USER_KWARGS = {
    'announcement_id',
    'request', 'user', 'form', 'identifier', 'uid',   # locals, not URL kwargs
}


def _source_files():
    return [
        p for p in (Path(settings.BASE_DIR) / 'src').rglob('*.py')
        if 'test' not in p.name and 'migrations' not in p.parts
    ]


def user_pk_kwargs():
    """
    Every name a view uses as a `ParliamentUser` primary key.

    Deliberately a loose text scan rather than an AST walk: the point is to
    catch a *new* route naming its parameter the same way an existing one does,
    and a loose scan over-collects, which is the safe direction. Anything it
    over-collects is either in `NOT_USER_KWARGS` with a reason or is not a URL
    kwarg at all.
    """
    found = set()
    patterns = (
        re.compile(r'ParliamentUser[^)]{0,120}?\bpk\s*=\s*(\w+)', re.S),
        re.compile(r'ParliamentUser[^)]{0,120}?\buser_id\s*=\s*(\w+)', re.S),
    )
    for path in _source_files():
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in patterns:
            found.update(m.group(1) for m in pattern.finditer(text))
    return found - NOT_USER_KWARGS


class MemberIdRoutesAcceptNonNumericIdsTests(SimpleTestCase):

    def setUp(self):
        self.urls = (Path(settings.BASE_DIR) / 'src' / 'urls.py').read_text(encoding='utf-8')

    def test_the_scan_finds_the_kwargs_we_know_about(self):
        """
        Not a formality: a scan that matches nothing passes the real assertion
        vacuously, and the whole failure being guarded against is a thing that
        looked fine because nothing was actually being checked.
        """
        kwargs = user_pk_kwargs()
        self.assertIn('pledge_pk', kwargs)
        self.assertIn('member_id', kwargs)

    def test_no_route_captures_a_member_id_as_an_int(self):
        kwargs = user_pk_kwargs()
        offenders = []
        for lineno, line in enumerate(self.urls.split('\n'), start=1):
            for match in re.finditer(r'<int:(\w+)>', line):
                if match.group(1) in kwargs:
                    offenders.append(f'src/urls.py:{lineno}  <int:{match.group(1)}>')
        self.assertEqual(
            offenders, [],
            'These routes carry a ParliamentUser id and declare the int '
            'converter. `user_id` is a CharField and pledges are "P-XXXXXX", '
            'so these 404 (or NoReverseMatch) for every pledge:\n  '
            + '\n  '.join(offenders)
            + '\n\nUse <str:…>.',
        )

    def test_the_three_known_routes_are_str(self):
        """
        The regression test. Named explicitly so that a future refactor that
        loses one of them fails with the reason attached rather than as a
        generic scan miss.
        """
        for fragment in (
            'education/pledge/<str:pledge_pk>/',
            'toggle/<str:pledge_pk>/',
            'adjustments/<int:period_id>/<str:member_id>/',
        ):
            with self.subTest(route=fragment):
                self.assertIn(fragment, self.urls)


class PledgeIdsInFixturesAreRealisticTests(SimpleTestCase):
    """
    ⚠️ THE FIXTURE IS WHY THIS SHIPPED. The education tests used numeric pledge
    ids, so they exercised a URL space the application does not have — and they
    were green while the dashboard 500'd.

    This asserts the fixture keeps using a `P-` id, because the moment it goes
    back to digits the `<str:>` routes above stop being load-bearing here and
    the next converter mistake sails through again.
    """

    def test_the_education_fixture_uses_a_pledge_shaped_id(self):
        body = (
            Path(settings.BASE_DIR) / 'src' / 'test_education_scoring_and_meetings.py'
        ).read_text(encoding='utf-8')
        self.assertIn("make_user('P-", body)
