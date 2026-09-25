"""
09-25-26 — logging in with an EMAIL address 500'd (UnboundLocalError).

`login_view` had a function-local `from django.contrib.auth import
get_user_model` ~140 lines below the email lookup. Any import inside a function
makes the name local to the WHOLE function, so `User = get_user_model()` in the
email branch ran before assignment. Latent since 06-11-26 (email login added
after the 05-23 local import); surfaced in prod 09-24-26. Fixed in 2115781.

Run with: python manage.py test src.tests.security.test_login_with_email_address
"""
import ast
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from src.models import ParliamentUser


class LoginWithEmailTests(TestCase):
    def setUp(self):
        self.user = ParliamentUser.objects.create(
            user_id='9901', username='emaillogin', name='Email Login',
            email='email.login@samford.edu', member_type='Member', member_status='Active',
        )
        self.user.set_password('email-login-pass-12345!')
        self.user.save()

    def test_an_email_address_login_does_not_500(self):
        r = Client().post(reverse('login'), {'username': 'Email.Login@samford.edu', 'password': 'wrong-password'})
        self.assertNotEqual(r.status_code, 500)

    def test_an_unknown_email_does_not_500(self):
        r = Client().post(reverse('login'), {'username': 'nobody@samford.edu', 'password': 'x'})
        self.assertNotEqual(r.status_code, 500)


class NoFunctionLocalImportsInLoginViewTests(SimpleTestCase):
    """The general form: no import statement inside `login_view` at all."""
    def test_login_view_has_no_function_local_imports_of_module_level_names(self):
        src = (Path(settings.BASE_DIR) / 'src/view/login_view.py').read_text()
        tree = ast.parse(src)
        module_names = {
            (a.asname or a.name).split('.')[0]
            for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names
        }
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'login_view')
        shadowing = [
            f'line {n.lineno}: {a.asname or a.name}'
            for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names if (a.asname or a.name).split('.')[0] in module_names
        ]
        self.assertEqual(shadowing, [], 'A function-local import of a module-level name makes it '
                         'local to the whole function — earlier uses raise UnboundLocalError.')
