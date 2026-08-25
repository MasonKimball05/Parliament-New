"""
v3.25.2 — every registered ModelAdmin must still be a class when the module has
finished importing.

⚠️ `@log_function_call` SAT ON `ParliamentUserAdmin` AND TURNED IT INTO A
FUNCTION.

`log_function_call`'s wrapper is `def wrapper(request, *args, **kwargs)` and its
first act is `request.user.username`. Applied to a class, it rebinds the
module-level name to a function, so `src.admin.ParliamentUserAdmin` was not a
class and constructing it raised `AttributeError: type object 'ParliamentUser'
has no attribute 'user'`.

⚠️ WHY IT SURVIVED. `@admin.register(...)` is the *inner* decorator, so it ran
first and registered the real class with the site — `/admin/` worked perfectly
and every page rendered. `functools.wraps` then copied the class's `__dict__`
onto the wrapper, so even `hasattr(ParliamentUserAdmin, 'login_as_link')` was
`True` and v3.25.0's own admin tests passed straight through it. Nothing
observable was wrong until something tried to *instantiate* or *subclass* the
name — which is a landmine rather than a bug, and the third instance of the
category (`_get_kai_access` carried three orphaned request-shaped decorators
until v3.16.2, and 500'd the whole Kai module when it was finally called).

> **A decorator written for views takes `request` as its first argument, so
> putting one on anything that is not a view is a type error that Python will
> not raise until the wrapped thing is called.** For a class registered by an
> inner decorator, that call may never come.
"""
import ast
import inspect
import os

from django.test import SimpleTestCase

from src.admin import admin_site

_ADMIN_MODULES = ('src/admin.py', 'src/admin_extra.py')
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class EveryRegisteredModelAdminIsStillAClassTests(SimpleTestCase):

    def test_the_module_level_name_of_each_registered_admin_is_a_class(self):
        import importlib

        offenders = []
        for model, instance in admin_site._registry.items():
            cls = type(instance)
            module = importlib.import_module(cls.__module__)
            bound = getattr(module, cls.__name__, None)
            if bound is not None and not isinstance(bound, type):
                offenders.append(
                    f'{cls.__module__}.{cls.__name__} is a '
                    f'{type(bound).__name__}, not a class (model={model.__name__})')
        self.assertEqual(offenders, [], '\n  ' + '\n  '.join(offenders))

    def test_parliament_user_admin_can_be_constructed(self):
        """
        The direct reproduction. Against the pre-fix tree this raises
        `AttributeError: type object 'ParliamentUser' has no attribute 'user'`.
        """
        from django.contrib.admin.sites import AdminSite

        from src.admin import ParliamentUserAdmin
        from src.models import ParliamentUser

        self.assertTrue(inspect.isclass(ParliamentUserAdmin))
        ParliamentUserAdmin(ParliamentUser, AdminSite())

    def test_no_class_in_an_admin_module_carries_a_view_decorator(self):
        """
        The general form, so the next one is caught in the diff rather than
        years later. A bare-name decorator on a `class` in these modules is
        either a registration helper (which takes the class) or a mistake.
        """
        allowed = {'register', 'admin.register', 'receiver'}
        offenders = []
        for relative in _ADMIN_MODULES:
            path = os.path.join(_REPO_ROOT, relative)
            if not os.path.exists(path):                    # pragma: no cover
                continue
            tree = ast.parse(open(path, encoding='utf-8').read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for decorator in node.decorator_list:
                    name = ast.unparse(decorator).split('(')[0]
                    if name not in allowed:
                        offenders.append(f'{relative}:{node.lineno} '
                                         f'class {node.name} @{name}')
        self.assertEqual(offenders, [], '\n  ' + '\n  '.join(offenders))
