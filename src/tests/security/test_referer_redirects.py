"""
v3.44.8 (10-07-26) — "back where you came from" redirects read the Referer.

v3.44.7 routed these through `safe_next`, which only accepts a PATH starting
with `/`. A Referer header is always a full URL (https://host/path), so every
one of them was refused and the member always landed on the fallback page:
setting an email from the pop-up on any page sent them to the home page.

`safe_referer` checks the Referer's host against this request's host and
returns its path, which then goes through `safe_next` like every other target.

Run with: python manage.py test src.tests.security.test_referer_redirects
"""
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from src.models import ParliamentUser
from src.next_url import safe_referer


class SafeRefererTests(SimpleTestCase):
    def check(self, referer, **extra):
        if referer is not None:
            extra['HTTP_REFERER'] = referer
        return safe_referer(RequestFactory().post('/set-email/', **extra))

    def test_same_site_referer_gives_its_path(self):
        self.assertEqual(self.check('http://testserver/profile/'), '/profile/')
        self.assertEqual(self.check('http://testserver/events/?month=10&x=a%20b'), '/events/?month=10&x=a%20b')
        self.assertEqual(self.check('http://testserver'), '/')

    def test_fragment_is_dropped(self):
        self.assertEqual(self.check('http://testserver/profile/#email'), '/profile/')

    def test_scheme_difference_is_fine_because_only_the_path_is_used(self):
        self.assertEqual(self.check('https://testserver/profile/'), '/profile/')

    def test_other_sites_are_refused(self):
        for referer in (
            'https://evil.example/profile/',
            'http://testserver.evil.example/',
            'http://evil.example/?http://testserver/',
            'http://testserver@evil.example/',
            'http://testserver:8000/profile/',
            '//evil.example/x',
            'javascript:alert(1)',
            'ftp://testserver/x',
        ):
            self.assertEqual(self.check(referer), '', referer)

    def test_paths_that_would_leave_the_site_are_refused(self):
        for referer in ('http://testserver//evil.example/x', 'http://testserver/\\evil.example'):
            self.assertEqual(self.check(referer), '', referer)

    def test_missing_empty_and_malformed(self):
        for referer in (None, '', 'profile', '/profile/', 'http://[bad/', 'about:blank'):
            self.assertEqual(self.check(referer), '', referer)


@override_settings(BEHIND_CLOUDFLARE=False)
class SetEmailGoesBackTests(TestCase):
    """The set-email pop-up is in base.html, so it is posted from any page."""

    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='ref1', name='Riley', username='ref1', member_type='Member',
            password='Pw-Referer-12345!')
        self.user.onboarding_complete = True
        self.user.save()
        self.client.force_login(self.user)

    def test_returns_to_the_page_it_was_posted_from(self):
        response = self.client.post(
            reverse('set_email'), {'email': 'riley@example.com'},
            HTTP_REFERER='http://testserver/profile/')
        self.assertRedirects(response, '/profile/', fetch_redirect_response=False)

    def test_off_site_referer_goes_home(self):
        response = self.client.post(
            reverse('set_email'), {'email': 'riley@example.com'},
            HTTP_REFERER='https://evil.example/profile/')
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)

    def test_no_referer_goes_home(self):
        response = self.client.post(reverse('set_email'), {'email': 'riley@example.com'})
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class NoRefererThroughSafeNextTests(SimpleTestCase):
    """`safe_next(request, <Referer>)` always answers ''. Use `safe_referer`."""

    def test_no_view_passes_the_referer_to_safe_next(self):
        import re
        from pathlib import Path

        from django.conf import settings

        pattern = re.compile(r"safe_next\([^)\n]*HTTP_REFERER")
        offenders = [
            str(path.relative_to(settings.BASE_DIR))
            for path in (Path(settings.BASE_DIR) / 'src').rglob('*.py')
            if 'tests' not in path.parts and pattern.search(path.read_text(encoding='utf-8'))
        ]
        self.assertEqual(offenders, [])
