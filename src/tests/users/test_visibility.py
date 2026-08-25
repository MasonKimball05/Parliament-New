"""
`visible_to` filtering — SQL must agree with the models' own Python rule.

Two rules that were previously only expressed in Python (`is_visible_to_user`)
and re-implemented inline in `src/view/home.py`, where both halves went wrong:
`__contains` is unsupported on SQLite, and `__len=0` was silently a JSON key
lookup rather than an emptiness test. See `src/utils/visibility.py`.

The load-bearing test is `test_sql_filter_matches_python_rule`: it enumerates
every (member_type × visible_to) combination and asserts the queryset and the
model method give the same answer. That is the assertion that would have caught
both bugs, and it will catch the next one without anyone having to think about
which lookups a given backend implements.
"""

from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from src.constants import MemberType
from src.models import Announcement, Event, ParliamentUser
from src.utils.visibility import visible_to_q, visible_to_values

#: Every shape `visible_to` can take, including the two "everyone" spellings.
VISIBLE_TO_CASES = [
    None,
    [],
    [MemberType.MEMBER],
    [MemberType.CHAIR],
    [MemberType.OFFICER],
    [MemberType.ADVISOR],
    [MemberType.PLEDGE],
    [MemberType.MEMBER, MemberType.PLEDGE],
    [MemberType.CHAIR, MemberType.OFFICER],
    [MemberType.ADVISOR, MemberType.PLEDGE],
    list(MemberType.ALL),
]


def make_user(uid, member_type):
    user = ParliamentUser.objects.create_user(
        user_id=uid, name=f'N{uid}', username=f'u{uid}',
        member_type=member_type, password='testpass123!',
    )
    return user


class MemberTypesAreUnambiguousTests(TestCase):
    """
    `visible_to_q` matches on the JSON-quoted value, which is only exact while
    no member type is a substring of another once quoted. Adding a type called
    `Members` would silently widen every visibility filter — fail here instead.
    """

    def test_no_member_type_contains_another(self):
        quoted = {f'"{value}"' for value in MemberType.ALL}
        for value in quoted:
            others = quoted - {value}
            for other in others:
                self.assertNotIn(value, other, f'{value} is a substring of {other}')

    def test_all_types_are_distinct(self):
        self.assertEqual(len(MemberType.ALL), len(set(MemberType.ALL)))


class VisibleToQueryTests(TestCase):
    """The SQL rule and the Python rule must not be able to disagree."""

    @classmethod
    def setUpTestData(cls):
        cls.author = make_user('vt-author', MemberType.OFFICER)
        cls.users = {
            member_type: make_user(f'vt-{member_type}', member_type)
            for member_type in MemberType.ALL
        }
        now = timezone.now()
        cls.events = {}
        for index, visible_to in enumerate(VISIBLE_TO_CASES):
            cls.events[index] = Event.objects.create(
                title=f'Event {index}', description='d',
                date_time=now + timedelta(days=index + 1),
                created_by=cls.author, is_active=True, visible_to=visible_to,
            )

    def test_sql_filter_matches_python_rule(self):
        """Every member type against every visible_to shape."""
        mismatches = []
        for member_type, user in self.users.items():
            visible_ids = set(
                Event.objects.filter(is_active=True)
                .filter(visible_to_q(member_type))
                .values_list('id', flat=True)
            )
            for index, event in self.events.items():
                by_python = event.is_visible_to_user(user)
                by_sql = event.id in visible_ids
                if by_python != by_sql:
                    mismatches.append(
                        f'{member_type} vs visible_to={VISIBLE_TO_CASES[index]!r}: '
                        f'python={by_python} sql={by_sql}'
                    )
        self.assertEqual(mismatches, [], 'SQL filter disagrees with is_visible_to_user')

    def test_empty_list_is_visible_to_everyone(self):
        """
        The `__len=0` bug: an explicitly-empty visible_to matched nothing, on
        every backend, because JSONField read `len` as a key name.
        """
        empty = Event.objects.get(title=f'Event {VISIBLE_TO_CASES.index([])}')
        for member_type in MemberType.ALL:
            visible = (Event.objects.filter(visible_to_q(member_type))
                       .filter(id=empty.id).exists())
            self.assertTrue(visible, f'empty visible_to hidden from {member_type}')

    def test_null_is_visible_to_everyone(self):
        null_event = Event.objects.get(title=f'Event {VISIBLE_TO_CASES.index(None)}')
        for member_type in MemberType.ALL:
            self.assertTrue(
                Event.objects.filter(visible_to_q(member_type))
                .filter(id=null_event.id).exists(),
                f'null visible_to hidden from {member_type}',
            )

    def test_member_covers_chair_and_officer(self):
        self.assertEqual(
            visible_to_values(MemberType.CHAIR), {MemberType.CHAIR, MemberType.MEMBER})
        self.assertEqual(
            visible_to_values(MemberType.OFFICER), {MemberType.OFFICER, MemberType.MEMBER})
        self.assertEqual(visible_to_values(MemberType.PLEDGE), {MemberType.PLEDGE})
        self.assertEqual(visible_to_values(MemberType.ADVISOR), {MemberType.ADVISOR})

    def test_pledge_does_not_see_member_only_events(self):
        """The case that matters for the confidentiality boundary."""
        member_only = Event.objects.get(
            title=f'Event {VISIBLE_TO_CASES.index([MemberType.MEMBER])}')
        self.assertFalse(
            Event.objects.filter(visible_to_q(MemberType.PLEDGE))
            .filter(id=member_only.id).exists())

    def test_works_on_announcements_too(self):
        """Same JSONField, same rule, different model."""
        Announcement.objects.create(
            title='A', content='c', posted_by=self.author,
            is_active=True, visible_to=[MemberType.PLEDGE],
        )
        self.assertTrue(
            Announcement.objects.filter(visible_to_q(MemberType.PLEDGE)).exists())
        self.assertFalse(
            Announcement.objects.filter(visible_to_q(MemberType.ADVISOR)).exists())


class HomePageLoadsTests(TestCase):
    """
    `/home/` raised NotSupportedError under DB_BACKEND=sqlite — the documented
    local-dev setup could not open the home page at all.
    """

    def test_home_renders_for_every_member_type(self):
        author = make_user('hp-author', MemberType.OFFICER)
        now = timezone.now()
        for index, visible_to in enumerate(VISIBLE_TO_CASES):
            Event.objects.create(
                title=f'E{index}', description='d',
                date_time=now + timedelta(days=index + 1),
                created_by=author, is_active=True, visible_to=visible_to,
            )
        for member_type in MemberType.ALL:
            with self.subTest(member_type=member_type):
                user = make_user(f'hp-{member_type}', member_type)
                client = Client()
                client.force_login(user)
                response = client.get('/home/')
                self.assertLess(response.status_code, 500)
