"""
v3.27.0 — SongCategory.get_tabs_data() caching.

`/songbook/` ran the category-tabs query (categories + counts) on every
single page load, unconditionally — regardless of the current search or
category filter, which only affect the song list below the tabs. Categories
and their song counts change rarely (adding, editing, or hiding a song; an
officer/chorister renaming or recoloring a category), so this is exactly the
"reads constantly, changes rarely" shape the rest of this codebase already
caches (`LandingPageContent`, `SystemLockdown`, `SiteSetting`) with a TTL as a
backstop and correctness coming from post_save/post_delete invalidation.

WHY NOT `{% cache %}`: that template tag only skips re-rendering already-
fetched context — the two queries below ran in the VIEW, before the template
ever executes, so wrapping the rendered HTML in `{% cache %}` would not have
removed a single query. Caching the query result itself (this module) is what
actually does.
"""
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.models import ParliamentUser, Song, SongCategory


def make_member(uid='songcache-member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Song Cache Member', username=uid, member_type='Member',
    )


class GetTabsDataCachingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.hymns = SongCategory.objects.create(name='Hymns', color='blue', display_order=1)
        self.drinking = SongCategory.objects.create(name='Drinking Songs', color='red', display_order=2)
        Song.objects.create(title='Amazing Grace', lyrics='...', category=self.hymns, is_active=True)
        Song.objects.create(title='Another Hymn', lyrics='...', category=self.hymns, is_active=True)
        Song.objects.create(title='Old Drinking Song', lyrics='...', category=self.drinking, is_active=True)
        Song.objects.create(
            title='Hidden Song', lyrics='...', category=self.hymns, is_active=False,
        )  # inactive — must not count anywhere

    def test_returns_correct_categories_and_counts(self):
        categories, category_counts = SongCategory.get_tabs_data()

        by_name = {c.name: c.song_count for c in categories}
        self.assertEqual(by_name['Hymns'], 2)
        self.assertEqual(by_name['Drinking Songs'], 1)
        self.assertEqual(category_counts['all'], 3)  # excludes the inactive song

    def test_second_call_costs_zero_queries(self):
        SongCategory.get_tabs_data()  # prime the cache

        with CaptureQueriesContext(connection) as captured:
            SongCategory.get_tabs_data()

        self.assertEqual(
            len(captured), 0,
            'get_tabs_data() hit the database on a warm cache — the whole '
            'point of caching this is that a page opened constantly and '
            "changed rarely shouldn't pay for the query every time.",
        )

    def test_adding_a_song_invalidates_the_cache(self):
        categories, counts = SongCategory.get_tabs_data()
        self.assertEqual(counts['all'], 3)

        Song.objects.create(title='Brand New Song', lyrics='...', category=self.hymns, is_active=True)

        categories, counts = SongCategory.get_tabs_data()
        self.assertEqual(
            counts['all'], 4,
            'A new song was invisible to the tab counts — the cache was not '
            'invalidated on Song creation.',
        )
        by_name = {c.name: c.song_count for c in categories}
        self.assertEqual(by_name['Hymns'], 3)

    def test_deleting_a_song_invalidates_the_cache(self):
        song = Song.objects.get(title='Old Drinking Song')
        SongCategory.get_tabs_data()  # prime with the song present

        song.delete()

        _, counts = SongCategory.get_tabs_data()
        self.assertEqual(counts['all'], 2)

    def test_deactivating_a_song_invalidates_the_cache(self):
        song = Song.objects.get(title='Amazing Grace')
        SongCategory.get_tabs_data()

        song.is_active = False
        song.save()

        categories, counts = SongCategory.get_tabs_data()
        self.assertEqual(counts['all'], 2)
        by_name = {c.name: c.song_count for c in categories}
        self.assertEqual(by_name['Hymns'], 1)

    def test_renaming_a_category_invalidates_the_cache(self):
        SongCategory.get_tabs_data()  # prime with the old name

        self.hymns.name = 'Sacred Songs'
        self.hymns.save()

        categories, _ = SongCategory.get_tabs_data()
        names = {c.name for c in categories}
        self.assertIn('Sacred Songs', names)
        self.assertNotIn('Hymns', names)

    def test_deleting_a_category_invalidates_the_cache(self):
        SongCategory.get_tabs_data()

        self.drinking.delete()

        categories, _ = SongCategory.get_tabs_data()
        names = {c.name for c in categories}
        self.assertNotIn('Drinking Songs', names)

    def test_a_stale_cached_value_is_not_mistaken_for_a_miss(self):
        """
        `get_tabs_data` checks `cached is not None`, not `if cached`, on
        purpose — a chapter with zero categories caches a falsy-but-valid
        `([], {'all': 0})`, and `if cached:` would treat that as a miss and
        re-query on every single request, silently defeating the cache for
        exactly the empty case.
        """
        SongCategory.objects.all().delete()
        Song.objects.all().delete()

        categories, counts = SongCategory.get_tabs_data()
        self.assertEqual(categories, [])
        self.assertEqual(counts, {'all': 0})

        with CaptureQueriesContext(connection) as captured:
            SongCategory.get_tabs_data()
        self.assertEqual(len(captured), 0)


class SongbookViewReflectsCacheTests(TestCase):
    """Integration coverage through the actual /songbook/ page."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.member = make_member()
        self.client.force_login(self.member)
        self.hymns = SongCategory.objects.create(name='Hymns', color='blue')
        Song.objects.create(title='Amazing Grace', lyrics='...', category=self.hymns, is_active=True)

    def test_a_newly_added_song_shows_up_without_a_cache_clear(self):
        response = self.client.get(reverse('songbook'))
        self.assertContains(response, '>1<')  # the Hymns count badge

        Song.objects.create(title='Second Hymn', lyrics='...', category=self.hymns, is_active=True)

        response = self.client.get(reverse('songbook'))
        self.assertContains(response, '>2<')
