"""
Management command to update song lyrics from the fraternity's default songbook.

The lyrics live in `<FRATERNITY_CONTENT_DIR>/songs.json` (09-28-26, multi-chapter:
they were a 1,000-line dict in this file). See src/fraternity_content.py; new
chapters create the songs themselves with `seed_default_songs`.

Usage:
    python manage.py update_song_lyrics
    python manage.py update_song_lyrics --dry-run  # Preview without changes
    python manage.py update_song_lyrics --fix-creator "Mason Kimball"  # Update creator
"""
from django.core.management.base import BaseCommand
from src.models import Song, ParliamentUser


def _song_lyrics():
    """{title: lyrics} from the fraternity's default songbook."""
    from src.fraternity_content import load_default_songs
    return {s['title']: s['lyrics'] for s in load_default_songs()['songs']}


class Command(BaseCommand):
    help = "Update song lyrics from the fraternity's default songbook (fraternity_content/)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be updated without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update even if lyrics already exist',
        )
        parser.add_argument(
            '--fix-creator',
            type=str,
            help='Update created_by to specified user (by full name, e.g., "Mason Kimball")',
        )
        parser.add_argument(
            '--fix-creator-only',
            action='store_true',
            help='Only fix the creator field, do not update lyrics (use with --fix-creator)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        fix_creator = options.get('fix_creator')
        fix_creator_only = options.get('fix_creator_only')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # Handle fix-creator option
        new_creator = None
        if fix_creator:
            # Try to find user by full name (name field)
            try:
                new_creator = ParliamentUser.objects.get(name__iexact=fix_creator.strip())
                self.stdout.write(self.style.SUCCESS(f'Found user: {new_creator.name} (ID: {new_creator.user_id})'))
            except ParliamentUser.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'User not found: {fix_creator}'))
                return
            except ParliamentUser.MultipleObjectsReturned:
                self.stdout.write(self.style.ERROR(f'Multiple users found with name: {fix_creator}'))
                return

        # Handle fix-creator-only mode
        if fix_creator_only:
            if not new_creator:
                self.stdout.write(self.style.ERROR('--fix-creator-only requires --fix-creator'))
                return

            songs = Song.objects.filter(is_active=True)
            updated_count = 0
            self.stdout.write(self.style.MIGRATE_HEADING('Fixing song creators...\n'))

            for song in songs:
                if dry_run:
                    self.stdout.write(f'  Would update creator for: {song.title}')
                else:
                    song.created_by = new_creator
                    song.save(update_fields=['created_by'])
                    self.stdout.write(self.style.SUCCESS(f'  Updated creator: {song.title}'))
                updated_count += 1

            self.stdout.write(self.style.MIGRATE_HEADING(f'\nUpdated creator for {updated_count} songs'))
            if dry_run:
                self.stdout.write(self.style.WARNING('DRY RUN - No changes were made.'))
            return

        SONG_LYRICS = _song_lyrics()

        # Title aliases for database titles that don't match SONG_LYRICS keys
        TITLE_ALIASES = {
            "As Beta Now We Meet": "As Betas Now We Meet",
            "Banquet Song": "The Banquet Hall",
            "The Beta Postscipt": "The Beta Postscript",
        }

        songs = Song.objects.filter(is_active=True)
        updated = 0
        not_found = 0
        skipped = 0

        self.stdout.write(self.style.MIGRATE_HEADING('Updating song lyrics...\n'))

        for song in songs:
            title = song.title

            # Check for title aliases first
            if title in TITLE_ALIASES:
                title = TITLE_ALIASES[title]

            # Try exact match first
            if title in SONG_LYRICS:
                lyrics = SONG_LYRICS[title]
            else:
                # Try without "The " prefix
                alt_title = title.replace('The ', '').strip()
                if alt_title in SONG_LYRICS:
                    lyrics = SONG_LYRICS[alt_title]
                # Try with "The " prefix
                elif f"The {title}" in SONG_LYRICS:
                    lyrics = SONG_LYRICS[f"The {title}"]
                else:
                    # Check for partial matches
                    lyrics = None
                    for key in SONG_LYRICS.keys():
                        if title.lower() in key.lower() or key.lower() in title.lower():
                            lyrics = SONG_LYRICS[key]
                            break

            if lyrics is None:
                self.stdout.write(f'  Not found: {song.title}')
                not_found += 1
                continue

            # Skip placeholder lyrics
            if '[Lyrics not available' in lyrics:
                self.stdout.write(f'  No lyrics available: {song.title}')
                skipped += 1
                continue

            # Check if lyrics already have content (unless force flag is set)
            if not force and song.lyrics and not song.lyrics.startswith('[Lyrics'):
                # Check if existing lyrics are garbled (many short lines indicate bad extraction)
                lines = [l for l in song.lyrics.split('\n') if l.strip()]
                if len(lines) > 5:
                    # Lyrics exist and seem substantial, skip unless force
                    self.stdout.write(f'  Already has lyrics: {song.title} (use --force to overwrite)')
                    skipped += 1
                    continue

            if dry_run:
                self.stdout.write(self.style.SUCCESS(f'  Would update: {song.title}'))
                preview = lyrics[:100].replace('\n', ' ')
                self.stdout.write(f'    Preview: {preview}...')
                if new_creator:
                    self.stdout.write(f'    Would set creator to: {new_creator.get_full_name()}')
            else:
                song.lyrics = lyrics.strip()
                if new_creator:
                    song.created_by = new_creator
                song.save()
                self.stdout.write(self.style.SUCCESS(f'  Updated: {song.title}'))

            updated += 1

        self.stdout.write(self.style.MIGRATE_HEADING('\nSummary:'))
        self.stdout.write(f'  Updated: {updated}')
        self.stdout.write(f'  Not found: {not_found}')
        self.stdout.write(f'  Skipped: {skipped}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were made. Run without --dry-run to update.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nLyrics update complete!'))
