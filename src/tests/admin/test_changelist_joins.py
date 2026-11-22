"""
Django admin change lists don't run a query per row (11-22-26).

The dev panel flagged `admin/change_list.html` firing the same query 11-16
times a page. Django's own join for a ForeignKey column (`select_related()`
with no arguments) skips nullable foreign keys, and a related object's
``__str__`` that reads a further relation (``self.applicant.name``) costs a
query per row unless that path is joined too.

The site now defaults every admin's ``list_select_related`` to the foreign
keys among its columns; admins whose columns print a further relation list
those paths themselves. This test reads every registered admin, so a new
column or ``__str__`` that needs a join fails here.

    python manage.py test src.tests.admin.test_changelist_joins
"""
import inspect
import re

from django.db.models import ForeignKey, OneToOneField
from django.test import SimpleTestCase

from src.admin import admin_site

SELF_RELATION = re.compile(r'self\.(\w+)\.')


def _fk(model, name):
    try:
        f = model._meta.get_field(name)
    except Exception:
        return None
    return f if isinstance(f, (ForeignKey, OneToOneField)) else None


def _str_relations(model):
    """Foreign keys that ``model.__str__`` reads through (``self.fk.attr``)."""
    for klass in model.__mro__:
        if '__str__' in klass.__dict__:
            try:
                src = inspect.getsource(klass.__dict__['__str__'])
            except (OSError, TypeError):
                return []
            return sorted({n for n in SELF_RELATION.findall(src) if _fk(model, n)})
    return []


def _needed_joins(model, list_display):
    needed = set()
    for name in list_display:
        if name == '__str__':
            needed.update(_str_relations(model))
            continue
        if not isinstance(name, str):
            continue
        fk = _fk(model, name)
        if fk:
            needed.add(name)
            needed.update(f'{name}__{n}' for n in _str_relations(fk.related_model))
    return needed


class ChangeListJoinTests(SimpleTestCase):

    def test_every_change_list_joins_what_its_columns_print(self):
        missing = {}
        for model, ma in admin_site._registry.items():
            needed = _needed_joins(model, ma.list_display)
            joined = ma.list_select_related
            if not needed or joined is True:
                continue
            joined = set(joined or ())
            # 'a__b' joins 'a' as well.
            gaps = sorted(n for n in needed
                          if n not in joined and not any(j.startswith(n + '__') for j in joined))
            if gaps:
                missing[f'{model.__name__} ({type(ma).__name__})'] = gaps
        self.maxDiff = None
        self.assertEqual(missing, {}, 'add these to list_select_related')

    def test_the_default_includes_nullable_foreign_keys(self):
        from src.models import ChatMessage
        self.assertEqual(set(admin_site._registry[ChatMessage].list_select_related),
                         {'sender', 'channel', 'committee'})

    def test_every_join_path_is_a_real_relation(self):
        # A misspelt path only fails when the page builds its query.
        for model, ma in admin_site._registry.items():
            joined = ma.list_select_related
            if joined and joined is not True:
                with self.subTest(model=model.__name__):
                    str(model._default_manager.select_related(*joined).query)


# ── The pages' query counts don't grow with their rows ───────────────────────

from django.test import TestCase, override_settings  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.db import connection  # noqa: E402
from django.urls import reverse  # noqa: E402


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class ChangeListQueryCountTests(TestCase):

    def setUp(self):
        from src.models import ParliamentUser
        self.n = 0
        self.admin = ParliamentUser.objects.create(
            user_id='cl-admin', username='cl-admin', name='CL Admin', member_type='Officer',
            member_status='Active', is_admin=True)
        self.client.force_login(self.admin)

    def user(self):
        from src.models import ParliamentUser
        self.n += 1
        return ParliamentUser.objects.create(user_id=f'cl-{self.n}', username=f'cl-{self.n}',
                                             name=f'CL Member {self.n}', member_status='Active')

    def queries(self, model_name):
        url = reverse(f'admin:src_{model_name}_changelist')
        with CaptureQueriesContext(connection) as q:
            r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return len(q.captured_queries)

    def assertFlat(self, model_name, add_row):
        add_row()
        self.queries(model_name)  # the first request also warms per-process caches
        few = self.queries(model_name)
        for _ in range(5):
            add_row()
        self.assertEqual(self.queries(model_name), few)

    def test_roles(self):
        from src.models import Role

        def add():
            role = Role.objects.create(name=f'CL Role {self.n}', code=f'CLR{self.n}')
            self.user().roles.add(role)
        self.assertFlat('role', add)

    def test_committees(self):
        from src.models import Committee

        def add():
            c = Committee.objects.create(name=f'CL Committee {self.n}', code=f'CLC{self.n}')
            c.members.add(self.user())
            c.chairs.add(self.user())
        self.assertFlat('committee', add)

    def test_chat_messages_with_a_channel_or_a_committee(self):
        from src.models import ChatChannel, ChatMessage, Committee

        def add():
            u = self.user()
            ch = ChatChannel.objects.create(name=f'CL Channel {self.n}', created_by=u)
            com = Committee.objects.create(name=f'CL Chat Committee {self.n}', code=f'CLK{self.n}')
            ChatMessage.objects.create(channel=ch, sender=u, message='hello')
            ChatMessage.objects.create(committee=com, sender=u, message='hi')
        self.assertFlat('chatmessage', add)

    def test_announcements(self):
        from src.models import Announcement, UserAnnouncementView

        def add():
            u = self.user()
            a = Announcement.objects.create(title=f'CL {self.n}', content='x', posted_by=u)
            UserAnnouncementView.objects.create(user=u, announcement=a)
        self.assertFlat('announcement', add)

    def test_counts_still_read_right(self):
        from src.models import Role
        role = Role.objects.create(name='CL Counted', code='CLCOUNT')
        self.user().roles.add(role)
        self.user().roles.add(role)
        r = self.client.get(reverse('admin:src_role_changelist'))
        self.assertContains(r, '2 members')
