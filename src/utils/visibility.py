"""
Database-portable `visible_to` filtering.

`Event.visible_to` and `Announcement.visible_to` are JSONFields holding a list
of member types — empty or null means "everyone". Both models already carry the
canonical rule as a Python method (`is_visible_to_user`); this module is the
queryset-level twin, for the places that need to filter in SQL rather than
decide per object.

v3.17.3. It exists because the one place that filtered in SQL — the home page —
got it wrong twice, in two different ways, and had no test tying it back to the
Python rule.

**Bug 1: `visible_to__contains=[...]` is not portable.** JSON containment is
implemented on PostgreSQL and MySQL but not on SQLite, which raises
`NotSupportedError: contains lookup is not supported on this database backend`.
Prod is Postgres so nobody noticed — but `DB_BACKEND=sqlite` is the documented
local-dev setup, and under it the home page could not be loaded *at all*.

**Bug 2: `visible_to__len=0` never matched anything, on any backend.** JSONField
has no `len` lookup, and Django's JSONField resolves any unrecognised name after
`__` as a *key transform*. So that clause did not ask "is the list empty"; it
asked "does this JSON object have a key called `len` equal to 0", which is false
for every list. The comment above it said "null/empty visible_to = all", and the
null half worked, so an event or announcement saved with an explicit empty list
was invisible to everyone on the home page — silently, on prod, and in a way no
amount of reading the SQL backend would explain.

Both are fixed by expressing the rule once, here, with lookups that behave the
same everywhere:

* `__isnull=True` and an exact `[]` comparison for the "everyone" cases, and
* `__icontains` on the JSON text with the value **JSON-quoted** for membership.

The quoting is what makes `icontains` exact rather than a substring guess:
`"Member"` with its quotes cannot match part of another member type, because
`MemberType.ALL` is a closed set of five values and none is a prefix or suffix
of another. `MemberTypesAreUnambiguousTests` asserts that, so adding a member
type called `Members` fails the suite here rather than silently widening a
visibility filter.

**Tradeoff, deliberately taken:** on Postgres, `contains` can use a GIN index
and `icontains` cannot. There is no GIN index on either column and these are
chapter-sized tables, so the cost is nil — and running the same code path in
dev, test and prod is worth more than an index we do not have. If one is added
later, branch on `connection.vendor` here and keep the tests, which compare the
SQL result against the Python rule and so would catch the branches drifting.
"""

import json

from django.db.models import Q

from src.constants import MemberType

#: Selecting "Member" also admits Chair and Officer — they are members too.
#: Mirrors `is_visible_to_user` on both models.
_MEMBER_IMPLIES = (MemberType.CHAIR, MemberType.OFFICER)


def visible_to_values(member_type):
    """The `visible_to` entries that admit `member_type`."""
    values = {member_type}
    if member_type in _MEMBER_IMPLIES:
        values.add(MemberType.MEMBER)
    return values


def visible_to_q(member_type, field='visible_to'):
    """
    A `Q` matching rows whose `visible_to` admits `member_type`.

    Equivalent to `is_visible_to_user`, minus the `is_active` check that callers
    already apply themselves::

        Event.objects.filter(is_active=True).filter(visible_to_q(user.member_type))
    """
    query = Q(**{f'{field}__isnull': True}) | Q(**{field: []})
    for value in sorted(visible_to_values(member_type)):
        query |= Q(**{f'{field}__icontains': json.dumps(value)})
    return query
