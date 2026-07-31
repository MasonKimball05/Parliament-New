"""
`filter()` before `annotate()` reuses the join — and the count comes out wrong.

WHAT WENT WRONG (found 07-31-26, fixed v3.17.7)
-----------------------------------------------
v3.17.5 replaced `{{ committee.members.count }}` on the home page (four queries
per committee) with an annotation. The rewrite was::

    Committee.objects.filter(
        Q(members=user) | Q(chairs=user) | Q(advisors=user), ...
    ).annotate(member_total=Count('members', distinct=True))

and its comment was careful about `distinct=True`, which is genuinely
load-bearing but is **not the trap here**. The trap is one Django rule earlier:

    filter() on a multi-valued relation, followed by annotate() on the SAME
    relation, reuses the filter's join — so the aggregate is computed over the
    rows the WHERE left standing, not over the relation.

The emitted SQL makes it obvious once you look::

    COUNT(DISTINCT committee_members.member_id) …
    LEFT OUTER JOIN committee_members ON …
    WHERE (committee_members.member_id = ME OR chairs.member_id = ME OR …)

For a committee where the OR is satisfied by the *chairs* or *advisors*
disjunct, every member row survives the WHERE and the count is right. For a
committee where the only true disjunct is `members = ME`, **exactly one member
row survives** — so the card read "1 member" for every committee you are a
plain member of, which is the common case. A wrong number, not a slow one.

Moving `.annotate()` above `.filter()` fixes it: Django then emits a second join
for the filter instead of reusing one.

THE PART WORTH REMEMBERING
--------------------------
`distinct=True` protects against row *multiplication* from several joins. It
does nothing about a join that has been *narrowed* by a WHERE. The question to
ask of any `Count()` annotation is not "is distinct set" but **"does this
queryset filter on the relation it is counting?"** — and the answer decides the
clause order. `manage_committees` and `global_search` use the identical
annotation safely because neither filters on members/chairs/advisors.
"""

from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser


def make_user(uid, member_type='Member', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('join-reuse-test-pass-12345!')
    user.save()
    return user


class HomeCommitteeMemberCountTests(TestCase):
    """
    Three committees, one per role, each with a member count the filter cannot
    be allowed to narrow. The 'plain member' case is the one that regressed;
    the other two are here because they passed *before* the fix too, and a test
    that only covers the broken case cannot tell you the fix was safe.
    """

    def setUp(self):
        self.me = make_user('jr-me')
        self.others = [make_user(f'jr-other-{i}') for i in range(6)]

        # I am a plain MEMBER here. 7 members total. Reported 1 before the fix.
        self.as_member = Committee.objects.create(name='Finance', code='FIN')
        self.as_member.members.add(self.me, *self.others)

        # I am the CHAIR here and not a member. 6 members.
        self.as_chair = Committee.objects.create(name='Rush', code='RSH')
        self.as_chair.members.add(*self.others)
        self.as_chair.chairs.add(self.me)

        # I am an ADVISOR here. 6 members.
        self.as_advisor = Committee.objects.create(name='Alumni', code='ALM')
        self.as_advisor.members.add(*self.others)
        self.as_advisor.advisors.add(self.me)

    def _committees_by_code(self):
        client = Client()
        client.force_login(self.me)
        response = client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        return {c.code: c for c in response.context['user_committees']}

    def test_a_committee_i_am_a_plain_member_of_reports_every_member(self):
        """The regression. Was 1, should be 7."""
        committees = self._committees_by_code()
        self.assertEqual(committees['FIN'].member_total, 7)

    def test_a_committee_i_chair_reports_every_member(self):
        committees = self._committees_by_code()
        self.assertEqual(committees['RSH'].member_total, 6)

    def test_a_committee_i_advise_reports_every_member(self):
        committees = self._committees_by_code()
        self.assertEqual(committees['ALM'].member_total, 6)

    def test_the_annotation_agrees_with_the_relation_it_replaced(self):
        """
        The strongest form: compare against `committee.members.count()`, which
        is what the template called before v3.17.5 and is by definition right.
        """
        committees = self._committees_by_code()
        for code, committee in committees.items():
            self.assertEqual(
                committee.member_total,
                Committee.objects.get(code=code).members.count(),
                f'{code}: annotated member_total disagrees with members.count()',
            )

    def test_all_three_committees_are_returned(self):
        """`.distinct()` must not drop rows while we are reordering clauses."""
        self.assertEqual(set(self._committees_by_code()), {'FIN', 'RSH', 'ALM'})

    def test_membership_by_a_single_role_still_matches(self):
        """A committee I have no relationship to must not appear."""
        Committee.objects.create(name='Housing', code='HSG')
        self.assertNotIn('HSG', self._committees_by_code())


class SiblingAnnotationsAreUnaffectedTests(TestCase):
    """
    `manage_committees` uses the same three-way `distinct=True` annotation with
    no filter on those relations. Recorded so the next person to see the pattern
    knows it was checked and is fine, rather than re-deriving it.
    """

    def setUp(self):
        self.officer = make_user('jr-officer', member_type='Officer')
        self.members = [make_user(f'jr-mc-{i}') for i in range(5)]
        self.committee = Committee.objects.create(name='Scholarship', code='SCH')
        self.committee.members.add(*self.members)
        self.committee.chairs.add(self.officer)

    def test_manage_committees_counts_are_correct(self):
        client = Client()
        client.force_login(self.officer)
        response = client.get(reverse('manage_committees'))
        self.assertEqual(response.status_code, 200)
        row = next(
            r for r in response.context['committees_data']
            if r['committee'].code == 'SCH'
        )
        self.assertEqual(row['member_count'], 5)
        self.assertEqual(row['chair_count'], 1)
        self.assertEqual(row['advisor_count'], 0)


class NoQuerysetCountsARelationItFiltersOnTests(TestCase):
    """
    The structural guard. The behavioural tests above cover `home`; this one is
    meant to catch the next occurrence anywhere in `src/view/`.

    It is deliberately narrow: it flags only the exact shape that bites —
    `.filter(...)` textually preceding `.annotate(...Count('X'...))` where `X`
    also appears inside the filter. Chains that annotate first are fine, and so
    are filters on ordinary columns.
    """

    ALLOWLIST = {
        # 'path/to/view.py': 'why this one is safe'
    }

    def test_no_view_filters_on_the_relation_it_counts(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent / 'view'
        chain_re = re.compile(
            r'\.filter\((?P<filter>(?:[^()]|\([^()]*\))*)\)\s*'
            r'(?:\n\s*)?\.annotate\((?P<annotate>(?:[^()]|\([^()]*\))*)\)',
            re.MULTILINE,
        )
        count_re = re.compile(r"Count\(\s*['\"](\w+)")

        offenders = []
        for path in sorted(root.rglob('*.py')):
            rel = str(path.relative_to(root.parent))
            if rel in self.ALLOWLIST:
                continue
            source = path.read_text(errors='ignore')
            for match in chain_re.finditer(source):
                counted = set(count_re.findall(match.group('annotate')))
                filtered = match.group('filter')
                for relation in counted:
                    if re.search(rf'\b{relation}\b', filtered):
                        line = source[:match.start()].count('\n') + 1
                        offenders.append(f'{rel}:{line} counts {relation!r} '
                                         f'after filtering on it')

        self.assertEqual(
            offenders, [],
            'A queryset filters on the same multi-valued relation it counts, so '
            'Django reuses the join and the aggregate is computed over the '
            'filtered rows. Move .annotate() above .filter(). See this module\'s '
            'docstring.\n  ' + '\n  '.join(offenders),
        )
