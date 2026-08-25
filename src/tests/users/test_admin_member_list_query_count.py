"""
The /admin/ ParliamentUser change list must not N+1 on roles.

WHY THIS EXISTS
----------------
`ParliamentUserAdmin.role_list()` reads `obj.roles.all()[:3]` and, for any
user with at least one role, `obj.roles.count()` (twice, if the user has more
than 3 roles — once for the `> 3` check, once more for the `- 3` display
math). Without `prefetch_related('roles')` on the admin's queryset, that's up
to 3 extra queries per row, on every /admin/ page load — reported directly
from the live site's query profiler (08-25-26): 50x the sliced `.all()`
query, 11x the `.count()` query, on a single 50-row page.

Fixed by adding `ParliamentUserAdmin.get_queryset()` with
`.prefetch_related('roles')`. Both `.all()[:3]` and `.count()` on a
*prefetched* relation read the prefetch cache and cost zero queries — the
`.count()`/`.exists()`-bypasses-the-cache belief this codebase held earlier
was itself corrected 07-31-26 after being measured wrong; see CLAUDE.md.
"""

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from src.admin import ParliamentUserAdmin
from src.models import ParliamentUser, Role


class AdminMemberListDoesNotNPlusOneOnRolesTests(TestCase):
    def setUp(self):
        self.roles = [
            Role.objects.create(name=f'Role {i}', code=f'R{i}') for i in range(6)
        ]
        # A mix: no roles, a couple of roles, and more than 3 (the `role_list`
        # branch that calls .count() twice) — the shape that actually fired
        # the reported N+1.
        for i in range(15):
            user = ParliamentUser.objects.create(
                user_id=f'nplus1-{i}', username=f'nplus1-{i}', name=f'User {i}',
                member_type='Member', member_status='Active',
            )
            if i % 3 == 1:
                user.roles.add(self.roles[0], self.roles[1])
            elif i % 3 == 2:
                user.roles.add(*self.roles)

    def test_query_count_does_not_scale_with_row_count(self):
        admin_instance = ParliamentUserAdmin(ParliamentUser, AdminSite())
        queryset = admin_instance.get_queryset(request=None)

        with CaptureQueriesContext(connection) as ctx:
            users = list(queryset.filter(user_id__startswith='nplus1-'))
            for user in users:
                admin_instance.role_list(user)

        # One query for the page of users, one for the roles prefetch — flat
        # regardless of how many of the 15 rows have roles. A regression here
        # (the prefetch removed, or bypassed by a fresh `.filter()`/`.exclude()`
        # inside role_list) would scale with row count instead.
        self.assertLessEqual(
            len(ctx.captured_queries), 2,
            f'Expected at most 2 queries (user list + roles prefetch), got '
            f'{len(ctx.captured_queries)} for {len(users)} rows — role_list() '
            f'is N+1ing on roles again:\n  '
            + '\n  '.join(q['sql'][:120] for q in ctx.captured_queries)
        )
