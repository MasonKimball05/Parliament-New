"""
Copy settings.CHAPTER onto the default Chapter row (multi-chapter step 4a).

In step 4a `get_chapter()` still reads settings, and the Chapter table must
match it (src.W007 warns when it doesn't). Run this after changing a CHAPTER_*
env var. `--dry-run` shows the differences only.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from src.models import Chapter


class Command(BaseCommand):
    help = 'Copy settings.CHAPTER onto the default Chapter row'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        try:
            values = Chapter.values_from_settings()
        except ValueError as e:
            raise CommandError(f'settings.CHAPTER is invalid: {e}') from e
        row = Chapter.objects.default()
        if row is None:
            if options['dry_run']:
                self.stdout.write('Would create the default chapter from settings.')
                return
            Chapter.objects.create(slug=slugify(values['chapter_name'])[:50] or 'chapter',
                                   is_default=True, **values)
            self.stdout.write(self.style.SUCCESS('Created the default chapter from settings.'))
            return
        drift = row.drift_from_settings()
        if not drift:
            self.stdout.write(self.style.SUCCESS('The default chapter already matches settings.'))
            return
        for field, (have, want) in drift.items():
            self.stdout.write(f'  {field}: {have!r} → {want!r}')
        if options['dry_run']:
            return
        for field, (_, want) in drift.items():
            setattr(row, field, want)
        row.save(update_fields=list(drift))
        self.stdout.write(self.style.SUCCESS(f'Updated {len(drift)} field(s).'))
