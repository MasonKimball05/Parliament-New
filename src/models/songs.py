from django.db import models
from src.storage import DualLocationStorage


class SongCategory(models.Model):
    """Categories for organizing songs in the songbook (Hymns, Drinking Songs, etc.)"""
    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(
        max_length=20,
        default='blue',
        help_text='Badge color: blue, green, red, yellow, purple, pink, gray'
    )
    description = models.TextField(blank=True)
    display_order = models.IntegerField(default=0, help_text='Lower numbers appear first')
    created_at = models.DateTimeField(auto_now_add=True)

    #: v3.27.0 — cached the same way `LandingPageContent`/`SystemLockdown` are:
    #: TTL is a backstop, correctness comes from the post_save/post_delete
    #: receivers at the bottom of this module. Read on every `/songbook/`
    #: page load (the category tabs + counts), which almost never changes —
    #: an officer/chorister adding, editing or hiding a song is a rare event
    #: compared to how often the page is opened.
    CACHE_KEY = 'songbook_category_tabs'
    CACHE_TTL = 3600  # backstop only; correctness comes from invalidation

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = "Song Categories"

    def __str__(self):
        return self.name

    @classmethod
    def get_tabs_data(cls):
        """
        `(categories, category_counts)` for the songbook's category tabs —
        the same two queries `songbook_list` used to run on every request,
        now cached. `categories` is a list of `SongCategory` instances each
        annotated with `song_count`; `category_counts['all']` is the total
        active-song count.

        ⚠️ THE QUERY MUST NOT RUN AT ALL ON A CACHE HIT — that is the actual
        point. This is called from the VIEW (not wrapped in a template
        `{% cache %}` block), because a `{% cache %}` block only skips
        re-rendering HTML; the two queries below would still run in the view
        before the template ever saw a cache hit. Caching the query result
        itself, invalidated by the signal at the bottom of this module, is
        what actually removes the queries from the request — the same
        reasoning `LandingPageContent`'s docstring already recorded.
        """
        from django.core.cache import cache

        cached = cache.get(cls.CACHE_KEY)
        if cached is not None:
            return cached

        from django.db.models import Count, Q
        from src.models.songs import Song  # local import: avoid a cycle at module load

        categories = list(
            cls.objects
            .annotate(song_count=Count('songs', filter=Q(songs__is_active=True)))
            .order_by('display_order', 'name')
        )
        category_counts = {
            'all': Song.objects.filter(is_active=True).count(),
        }
        result = (categories, category_counts)
        cache.set(cls.CACHE_KEY, result, cls.CACHE_TTL)
        return result

    @classmethod
    def invalidate_tabs_cache(cls):
        from django.core.cache import cache
        cache.delete(cls.CACHE_KEY)


class Song(models.Model):
    """Songs in the chapter songbook with lyrics and optional audio"""
    title = models.CharField(max_length=200)
    lyrics = models.TextField(help_text='Full song lyrics')
    audio_file = models.FileField(
        upload_to='songbook/audio/',
        storage=DualLocationStorage(),
        blank=True,
        null=True,
        help_text='Optional: Audio file (MP3, WAV, M4A)'
    )
    category = models.ForeignKey(
        SongCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='songs'
    )
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='songs_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide song')

    class Meta:
        ordering = ['title']

    def __str__(self):
        if self.category:
            return f"{self.title} ({self.category.name})"
        return self.title

    def has_audio(self):
        """Check if song has an audio file"""
        return bool(self.audio_file)


# ---------------------------------------------------------------------------
# v3.27.0 — cache invalidation for SongCategory.get_tabs_data().
#
# Both models invalidate the SAME cache key: a Song's `is_active` flag or
# category assignment changes the counts, and a SongCategory's name/color/
# display_order changes the rendered tab itself, so either one becoming stale
# is the same bug from the tab's point of view. Receivers rather than a
# `cache.delete()` inside each view, for the reason already recorded next to
# `LandingPageContent`'s identical pattern: the editing view is not the only
# writer — `admin.py`/`admin_extra.py` can edit either model from `/admin/`,
# and a `manage.py shell` fix during a handoff would too. A receiver covers
# all of them; a delete call in `song_edit`/`manage_categories` would have
# covered only the one path someone remembered to add it to.
#
# post_delete matters as much as post_save — deleting a song or category is
# exactly the kind of change that makes a cached count wrong.
# ---------------------------------------------------------------------------
from django.db.models.signals import post_delete, post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_save, sender=Song)
@receiver(post_delete, sender=Song)
@receiver(post_save, sender=SongCategory)
@receiver(post_delete, sender=SongCategory)
def _invalidate_songbook_tabs_cache(sender, instance, **kwargs):
    SongCategory.invalidate_tabs_cache()
