"""
Export this chapter's active songbook as songs.json (multi-chapter, 09-28-26),
in the format `seed_default_songs --source` reads.

Use it to refresh `fraternity_content/<fraternity>/songs.json` from a
deployment whose songbook has been curated (categories fixed, lyrics
corrected), or to hand one chapter's songs to another.

Usage:
    python manage.py export_songs -o fraternity_content/beta_theta_pi/songs.json
"""
import json

from django.core.management.base import BaseCommand, CommandError

from src.fraternity_content import validate_songbook
from src.models import Song, SongCategory


class Command(BaseCommand):
    help = 'Export the active songbook as importable JSON'

    def add_arguments(self, parser):
        parser.add_argument('-o', '--output', default='', help='Write to this file instead of stdout')
        parser.add_argument('--source-note', default='', help='Optional "source" text to record in the file')

    def handle(self, *args, **options):
        cats = [{'name': c.name, 'color': c.color, 'display_order': c.display_order}
                for c in SongCategory.objects.order_by('display_order', 'name')]
        songs = [{'title': s.title, 'category': s.category.name if s.category else None, 'lyrics': s.lyrics}
                 for s in Song.objects.filter(is_active=True).select_related('category').order_by('title')]
        for s in songs:
            if s['category'] is None:
                del s['category']
        if not songs:
            raise CommandError('No active songs to export.')
        data = {'categories': cats, 'songs': songs}
        if options['source_note']:
            data = {'source': options['source_note'], **data}
        validate_songbook({k: v for k, v in data.items() if k != 'source'}, label='database export')
        text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
        if options['output']:
            with open(options['output'], 'w', encoding='utf-8') as f:
                f.write(text)
            self.stderr.write(self.style.SUCCESS(f"Wrote {len(songs)} song(s) to {options['output']}"))
        else:
            self.stdout.write(text, ending='')
