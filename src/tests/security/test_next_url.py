"""
v3.38.2 (10-01-26) — `?next=` must be a same-site path starting with one `/`.

Before: `safe_next` accepted any same-site relative URL, including a bare word.
`redirect('logout')` resolves a bare word as a route NAME, so `?next=logout`
on the 2FA verify page sent the member to /logout/ after a successful code,
and any other word was a NoReverseMatch (a 500).

Run with: python manage.py test src.tests.security.test_next_url
"""
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from src.next_url import safe_next


class SafeNextTests(SimpleTestCase):
    def check(self, value):
        return safe_next(RequestFactory().get('/', {'next': value}))

    def test_same_site_paths_pass(self):
        home, profile = reverse('home'), reverse('profile')
        for value in ('/', home, f'{profile}?tab=email', f'{home}#top'):
            self.assertEqual(self.check(value), value)

    def test_bare_words_and_relative_paths_are_refused(self):
        for value in ('logout', 'home', 'not-a-route', 'events/5/', '../x', '?x=1', ''):
            self.assertEqual(self.check(value), '', value)

    def test_other_sites_are_refused(self):
        for value in ('//evil.example/x', 'https://evil.example/', '/\\evil.example', 'javascript:alert(1)'):
            self.assertEqual(self.check(value), '', value)
