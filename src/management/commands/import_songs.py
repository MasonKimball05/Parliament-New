"""
Management command to import songs from exportable_media/Beta Songs folder.
Creates Song entries with links to the audio files.

Usage:
    python manage.py import_songs
    python manage.py import_songs --dry-run  # Preview without creating
"""
import os
import re
from django.core.management.base import BaseCommand
from django.conf import settings
from src.models import Song, SongCategory, ParliamentUser


# Song categorization mapping (song title -> category name)
SONG_CATEGORIES = {
    # Hymns / Formal Songs
    'Beta Hymn': 'Hymns',
    'Beta Doxology': 'Hymns',
    'The Loving Cup': 'Hymns',
    'Parting Song': 'Hymns',
    'We Gather Again': 'Hymns',
    'Thus Heart to Heart': 'Hymns',
    'Let All Stand Together': 'Hymns',
    'As Beta Now We Meet': 'Hymns',
    'Gemma Nostra': 'Hymns',

    # Sweetheart Songs
    'Beta Sweetheart': 'Sweetheart Songs',
    'Beta Sweetheart Song': 'Sweetheart Songs',
    'My Beta Girl': 'Sweetheart Songs',
    'I Love You, (Only You) Beta Girl': 'Sweetheart Songs',
    'Beta Rose': 'Sweetheart Songs',
    'Beta Lullaby': 'Sweetheart Songs',
    'In an Old Fashioned Garden': 'Sweetheart Songs',

    # Drinking / Fun Songs
    'The Jolly Greeks': 'Drinking Songs',
    'Ti-de-i-de-o': 'Drinking Songs',
    'The Crow Song': 'Drinking Songs',
    'I Took My Girl Out Walking': 'Drinking Songs',
    "We'll Always Hang Together": 'Drinking Songs',
    'Good Betas Sing Forever': 'Drinking Songs',
    'Ring the Bells of Old Miami': 'Drinking Songs',

    # Wooglin / Pledge Songs
    'Wooglin to the Pledge': 'Wooglin Songs',
    'Wooglin Forever!': 'Wooglin Songs',
    'To the Pledge': 'Wooglin Songs',
    'The Sons of the Dragon': 'Wooglin Songs',

    # Chapter / Brotherhood Songs
    'The Beta Shrine': 'Brotherhood Songs',
    'The Beta Stars': 'Brotherhood Songs',
    "Beta's Emblems": 'Brotherhood Songs',
    'The Beta Postscript': 'Brotherhood Songs',
    'For the Staunchest': 'Brotherhood Songs',
    'In the Old Porch Chairs': 'Brotherhood Songs',
    "There's a Scene": 'Brotherhood Songs',
    'The Banquet Hall': 'Brotherhood Songs',
    'Banquet Song': 'Brotherhood Songs',
    "The Alumni's Return": 'Brotherhood Songs',
    'Beta Day': 'Brotherhood Songs',
}

# Default category colors
CATEGORY_COLORS = {
    'Hymns': 'blue',
    'Sweetheart Songs': 'pink',
    'Drinking Songs': 'yellow',
    'Wooglin Songs': 'purple',
    'Brotherhood Songs': 'green',
    'Other': 'gray',
}

# Display order for categories
CATEGORY_ORDER = {
    'Hymns': 1,
    'Sweetheart Songs': 2,
    'Brotherhood Songs': 3,
    'Wooglin Songs': 4,
    'Drinking Songs': 5,
    'Other': 99,
}


class Command(BaseCommand):
    help = 'Import songs from exportable_media/Beta Songs folder'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be imported without making changes',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing songs before importing',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        clear = options['clear']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be made\n'))

        # Find the exportable_media folder
        base_dir = settings.BASE_DIR
        songs_dir = os.path.join(base_dir, 'exportable_media', 'Beta Songs')
        sheet_music_path = os.path.join(base_dir, 'exportable_media', 'BTP Sheet Music.pdf')

        if not os.path.exists(songs_dir):
            self.stdout.write(self.style.ERROR(f'Songs directory not found: {songs_dir}'))
            return

        # Get admin user for created_by
        admin_user = ParliamentUser.objects.filter(is_admin=True).first()
        if not admin_user and not dry_run:
            self.stdout.write(self.style.ERROR('No admin user found. Please create an admin user first.'))
            return

        # Clear existing songs if requested
        if clear and not dry_run:
            deleted_count = Song.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(f'Deleted {deleted_count} existing songs'))

        # Create categories
        self.stdout.write(self.style.MIGRATE_HEADING('Creating categories...'))
        categories = {}
        for name in set(SONG_CATEGORIES.values()) | {'Other'}:
            if dry_run:
                self.stdout.write(f'  Would create category: {name}')
                categories[name] = None
            else:
                cat, created = SongCategory.objects.get_or_create(
                    name=name,
                    defaults={
                        'color': CATEGORY_COLORS.get(name, 'gray'),
                        'display_order': CATEGORY_ORDER.get(name, 50),
                    }
                )
                categories[name] = cat
                if created:
                    self.stdout.write(self.style.SUCCESS(f'  Created category: {name}'))
                else:
                    self.stdout.write(f'  Category exists: {name}')

        # Import songs
        self.stdout.write(self.style.MIGRATE_HEADING('\nImporting songs...'))
        imported = 0
        skipped = 0

        for filename in sorted(os.listdir(songs_dir)):
            if not filename.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac')):
                continue

            # Parse title from filename (remove YouTube ID in brackets and extension)
            title = filename
            # Remove extension
            title = os.path.splitext(title)[0]
            # Remove YouTube ID in brackets like [QL7F1FbpyF8]
            title = re.sub(r'\s*\[[^\]]+\]\s*$', '', title)
            title = title.strip()

            # Get category
            category_name = SONG_CATEGORIES.get(title, 'Other')
            category = categories.get(category_name)

            # Relative path for the FileField
            relative_path = f'Beta Songs/{filename}'

            if dry_run:
                self.stdout.write(f'  Would import: "{title}" -> {category_name}')
                self.stdout.write(f'    Audio: {relative_path}')
                imported += 1
                continue

            # Check if song already exists
            if Song.objects.filter(title=title).exists():
                self.stdout.write(f'  Skipping (exists): {title}')
                skipped += 1
                continue

            # Create song
            song = Song.objects.create(
                title=title,
                lyrics=f'[Lyrics for "{title}" - to be added]',
                audio_file=relative_path,
                category=category,
                created_by=admin_user,
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS(f'  Imported: {title}'))
            imported += 1

        # Summary
        self.stdout.write(self.style.MIGRATE_HEADING('\nSummary:'))
        self.stdout.write(f'  Songs imported: {imported}')
        self.stdout.write(f'  Songs skipped: {skipped}')

        if os.path.exists(sheet_music_path):
            self.stdout.write(self.style.SUCCESS(f'\nSheet music found: {sheet_music_path}'))
            self.stdout.write('  This PDF contains sheet music for all songs.')
            self.stdout.write('  You can link to it from the songbook page.')
        else:
            self.stdout.write(self.style.WARNING('\nSheet music PDF not found'))

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were made. Run without --dry-run to import.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nImport complete!'))
            self.stdout.write('Note: Song lyrics are placeholders. Edit each song to add the actual lyrics.')
