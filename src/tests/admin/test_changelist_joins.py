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
            gaps = sorted(needed - set(joined or ()))
            if gaps:
                missing[f'{model.__name__} ({type(ma).__name__})'] = gaps
        self.maxDiff = None
        self.assertEqual(missing, {}, 'add these to list_select_related')

    def test_the_default_includes_nullable_foreign_keys(self):
        from src.models import ChatMessage
        self.assertEqual(set(admin_site._registry[ChatMessage].list_select_related),
                         {'sender', 'channel', 'committee'})
