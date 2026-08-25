"""
The public content pages must not cache one visitor's page for another.

WHAT WENT WRONG (v3.17.4)
------------------------
`changelog`, `changelog_detail` and `roadmap` were decorated with
`@cache_page(60 * 30)` since v3.11.1. That caches the whole rendered *response*,
and all three pages are **public** and extend `base.html` — so the cached HTML
carried whichever visitor happened to prime it: their navbar, their name, their
avatar, and the inline `const theme = '…'` that switches dark mode on.

Reported as *"the changelog page is suddenly displaying in light mode"* — an
anonymous visitor primed the cache, so every dark-mode member got light. The
same mechanism served a logged-in member's name to anonymous visitors.

**`Vary: Cookie` does not protect you here**, which is the counter-intuitive part
and the reason this survived code review. `UpdateCacheMiddleware` runs *inside*
the `cache_page` decorator and stores the response before `SessionMiddleware`
adds the Vary header, so the learned Vary list is empty and the cache key is the
URL alone. The `Vary: Cookie` you can see on the response is added afterwards,
too late to affect the key.

The fix caches the expensive part — reading and parsing ~100 markdown files —
and lets the template render per request.

Same failure class as the 07-18-26 Cloudflare seal incident: **never cache a
response whose body depends on who asked for it.**
"""

import pathlib
import re

from django.core.cache import cache
from django.test import Client, TestCase

from src.models import ParliamentUser, UserPreferences

SRC = pathlib.Path(__file__).resolve().parent.parent.parent

#: Pages that are public AND extend base.html, so their body depends on the
#: viewer. Anything added here must never be response-cached.
VIEWER_DEPENDENT_PUBLIC_PAGES = ['/changelog/', '/roadmap/']


def make_user(uid, name, theme='dark', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type='Officer' if is_admin else 'Member',
        member_status='Active', is_admin=is_admin,
    )
    user.set_password('changelog-pass-12345!')
    user.save()
    prefs, _ = UserPreferences.objects.get_or_create(user=user)
    prefs.theme = theme
    prefs.save()
    cache.delete(f'user_prefs_{user.pk}')
    return user


def theme_of(response):
    match = re.search(r"const theme = '([^']*)'", response.content.decode())
    return match.group(1) if match else None


class NoResponseCachingOnViewerDependentPagesTests(TestCase):
    """A static guard: the decorator itself must not come back."""

    def test_no_view_uses_cache_page(self):
        """
        `cache_page` is unsafe on any page that renders `base.html`, which is
        nearly all of them. If a genuinely viewer-independent endpoint ever needs
        it (a JSON feed with no user chrome), add it to an explicit allowlist here
        with a reason rather than deleting this test.
        """
        offenders = []
        for path in sorted(SRC.rglob('*.py')):
            if path.name.startswith('test_'):
                continue
            source = path.read_text(encoding='utf-8')
            for line_no, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith('#') or stripped.startswith('#:'):
                    continue
                if '@cache_page' in stripped:
                    offenders.append(f'{path.relative_to(SRC)}:{line_no}')
        self.assertEqual(
            offenders, [],
            '@cache_page caches the whole response including the viewer\'s '
            'navbar and theme — cache the data instead',
        )

    def test_no_site_wide_cache_middleware(self):
        """The same hazard, applied to every page at once."""
        from django.conf import settings

        for middleware in settings.MIDDLEWARE:
            self.assertNotIn('django.middleware.cache', middleware)


class ChangelogDoesNotLeakAcrossVisitorsTests(TestCase):
    """The behavioural half — what the user actually reported."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.dark_admin = make_user('cl-dark', 'Dark Admin', 'dark', is_admin=True)
        self.light_member = make_user('cl-light', 'Light Member', 'light')

    def test_each_visitor_gets_their_own_theme(self):
        dark_client = Client()
        dark_client.force_login(self.dark_admin)
        self.assertEqual(theme_of(dark_client.get('/changelog/')), 'dark')

        light_client = Client()
        light_client.force_login(self.light_member)
        self.assertEqual(
            theme_of(light_client.get('/changelog/')), 'light',
            'a light-mode member is being served the dark-mode cached page')

        self.assertEqual(theme_of(Client().get('/changelog/')), 'light',
                         'anonymous visitors should get the default theme')

    def test_a_dark_member_is_not_given_the_anonymous_page(self):
        """The exact reported symptom: anonymous primes it, members go light."""
        Client().get('/changelog/')                     # anonymous primes
        dark_client = Client()
        dark_client.force_login(self.dark_admin)
        self.assertEqual(
            theme_of(dark_client.get('/changelog/')), 'dark',
            'dark-mode member served the anonymous cached page — this is the bug')

    def test_one_members_identity_is_not_shown_to_another(self):
        admin_client = Client()
        admin_client.force_login(self.dark_admin)
        admin_client.get('/changelog/')                 # primes the cache

        member_client = Client()
        member_client.force_login(self.light_member)
        body = member_client.get('/changelog/').content.decode()

        self.assertNotIn('Dark Admin', body,
                         "another member's name is in this member's navbar")
        self.assertIn('Light Member', body, 'own name missing from navbar')

    def test_nothing_authenticated_reaches_an_anonymous_visitor(self):
        admin_client = Client()
        admin_client.force_login(self.dark_admin)
        admin_client.get('/changelog/')                 # primes the cache

        body = Client().get('/changelog/').content.decode()
        self.assertNotIn('Dark Admin', body,
                         'a member name is being served to the public')
        # Link hrefs, not the word "Admin" — that appears ~64 times in the
        # changelog prose itself and is not a leak.
        self.assertNotIn('href="/admin-v2', body)
        self.assertNotIn('href="/admin/', body)

    def test_all_viewer_dependent_public_pages_behave(self):
        for url in VIEWER_DEPENDENT_PUBLIC_PAGES:
            with self.subTest(url=url):
                cache.clear()
                Client().get(url)                       # anonymous primes
                dark_client = Client()
                dark_client.force_login(self.dark_admin)
                response = dark_client.get(url)
                self.assertEqual(theme_of(response), 'dark', url)


class ChangelogContentIsStillCachedTests(TestCase):
    """
    Removing `cache_page` must not have removed the point of it: this page reads
    and markdown-parses ~100 files, which is the actual cost.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('cl-cache', 'Cache User')

    def test_index_content_is_cached(self):
        client = Client()
        client.force_login(self.user)
        self.assertIsNone(cache.get('changelog_index_v1'))
        client.get('/changelog/')
        self.assertIsNotNone(cache.get('changelog_index_v1'),
                             'parsed changelog index was not cached')

    def test_detail_content_is_cached_per_version(self):
        client = Client()
        client.force_login(self.user)
        response = client.get('/changelog/v3.17.3/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(cache.get('changelog_detail_v1:v3.17.3'))

    def test_detail_cache_key_uses_the_sanitised_version(self):
        """
        The key is built from the sanitised name, so a hostile path cannot
        choose the cache key it writes to.
        """
        client = Client()
        client.force_login(self.user)
        client.get('/changelog/v3.17.3/')
        keys = ['changelog_detail_v1:v3.17.3']
        self.assertTrue(any(cache.get(k) is not None for k in keys))


class ChangelogDetailCacheKeysAreBoundedTests(TestCase):
    """
    The second half of the caching problem (v3.17.5).

    Removing `@cache_page` fixed *whose* page was served. It did not fix *how
    many* cache entries an anonymous visitor could create. The replacement keyed
    on `f'changelog_detail_v1:{safe_version}'` and let the not-found branch fall
    through to the same `cache.set(...)` as a hit — so every junk URL minted its
    own 30-minute entry, and the route is public with no rate limit.

    ⚠️ WHY THIS IS NOT MERELY CACHE BLOAT: settings.py sets
    `SESSION_ENGINE = 'django.contrib.sessions.backends.cache'` with
    `SESSION_CACHE_ALIAS = 'default'`, so sessions and this cache are the same
    Redis. Filling it either evicts sessions (chapter-wide logout, under an
    `allkeys-*` maxmemory policy) or makes Redis refuse writes — and the write
    that fails is the session write, so login breaks.

    The fix validates the version against the changelogs directory before
    touching the cache, so the key space is bounded by the number of files that
    exist however many URLs are requested.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @staticmethod
    def _detail_keys():
        backend = getattr(cache, '_cache', {})
        return [k for k in backend if 'changelog_detail_v1:' in k]

    def test_unknown_versions_create_no_cache_entries(self):
        client = Client()
        for n in range(25):
            client.get(f'/changelog/junk{n}deadbeef/')
        self.assertEqual(
            self._detail_keys(), [],
            'an anonymous visitor minted cache entries for versions that do '
            'not exist — this is the session-Redis fill vector',
        )

    def test_unknown_version_is_a_404_not_a_cached_200(self):
        response = Client().get('/changelog/nosuchversion/')
        self.assertEqual(response.status_code, 404)

    def test_a_real_version_is_still_cached(self):
        """The bound must not cost the saving it was added to protect."""
        response = Client().get('/changelog/v3.17.3/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(cache.get('changelog_detail_v1:v3.17.3'))

    def test_known_versions_come_from_the_directory(self):
        from src.view.changelog import known_versions

        versions = known_versions()
        self.assertIn('v3.17.3', versions)
        self.assertNotIn('junk0deadbeef', versions)
        # Bounded by what is on disk, which is the whole point.
        on_disk = {p.stem for p in (SRC.parent / 'changelogs').glob('*.md')}
        self.assertEqual(set(versions), on_disk)

    def test_no_raw_exception_text_is_rendered_on_the_public_pages(self):
        """
        `changelog_html` is rendered through `|safe` on public pages, so a raw
        `str(e)` there showed anonymous visitors the server's absolute BASE_DIR
        — and cached it for 30 minutes. Same shape as the CSV export leak fixed
        07-28-26.
        """
        source = (SRC / 'view' / 'changelog.py').read_text(encoding='utf-8')
        for line_no, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            self.assertNotIn(
                'str(e)', stripped,
                f'src/view/changelog.py:{line_no} interpolates exception text '
                f'into a page rendered with |safe',
            )
