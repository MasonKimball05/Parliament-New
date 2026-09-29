"""
Seed the fraternity's default songbook into this chapter's database
(multi-chapter, 09-28-26).

Reads `<FRATERNITY_CONTENT_DIR>/songs.json` (src/fraternity_content.py) and:

  * creates any missing SongCategory rows (by name);
  * creates any default song whose title isn't already in the songbook
    (case-insensitive), in its category;
  * skips songs whose lyrics are the "[Lyrics not available…]" placeholder.

It never edits or deletes an existing song. A chapter's own songs (added in
the app) and any edits a chorister made to a default song are left alone. It
is safe to re-run; `update_song_lyrics` refreshes lyrics of existing songs.

Usage:
    python manage.py seed_default_songs
    python manage.py seed_default_songs --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from src.fraternity_content import FraternityContentError, has_lyrics, load_default_songs
from src.models import Song, SongCategory


class Command(BaseCommand):
    help = "Create the fraternity's default songs and categories that this chapter doesn't have yet"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would be created')
        parser.add_argument('--source', default='', help='songs.json to use instead of FRATERNITY_CONTENT_DIR/songs.json')

    def handle(self, *args, **options):
        try:
            book = load_default_songs(options['source'] or None)
        except FraternityContentError as e:
            raise CommandError(str(e)) from e
        dry = options['dry_run']
        with transaction.atomic():
            existing_cats = {c.name: c for c in SongCategory.objects.all()}
            new_cats = 0
            for c in book.get('categories', []):
                if c['name'] in existing_cats:
                    continue
                new_cats += 1
                if not dry:
                    existing_cats[c['name']] = SongCategory.objects.create(
                        name=c['name'], color=c.get('color', 'blue'),
                        display_order=c.get('display_order', 0))
            have = {t.lower() for t in Song.objects.values_list('title', flat=True)}
            created = skipped_existing = skipped_placeholder = 0
            for s in book['songs']:
                if s['title'].lower() in have:
                    skipped_existing += 1
                    continue
                if not has_lyrics(s):
                    skipped_placeholder += 1
                    continue
                created += 1
                if not dry:
                    Song.objects.create(title=s['title'], lyrics=s['lyrics'],
                                        category=existing_cats.get(s.get('category')))
            if dry:
                transaction.set_rollback(True)
        verb = 'Would create' if dry else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {new_cats} categor{"y" if new_cats == 1 else "ies"} and {created} song(s); '
            f'{skipped_existing} already in the songbook, {skipped_placeholder} without lyrics skipped.'))
