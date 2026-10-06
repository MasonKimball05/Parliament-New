"""
Tag the original 1800s chapter founders (roll #1–#43) so they get the
Beta Blue "Original Founder" class badge (src/pledge_classes.py).

Sets pledge_class (the semester field) = 'Original Founders' on each member
whose roll number is 1–43. They weren't all one semester; this is a nod, not
a record of when each was initiated. pledge_class_greek is left untouched.

Safety: a roll-numbered member whose class already resolves to a modern
semester (Fall 2022 onward) is skipped and listed — that's a numbering clash,
not a founder. Idempotent.

    python manage.py mark_original_founders                  # preview (dry run)
    python manage.py mark_original_founders --apply          # write changes
"""
import re

from django.core.management.base import BaseCommand

from src.models import ParliamentUser
from src.pledge_classes import (
    ORIGINAL_FOUNDERS_LABEL, ORIGINAL_FOUNDERS_ROLLS, normalize,
)


def _roll(value):
    """'7', '07', '#7' -> 7; anything else -> None."""
    m = re.fullmatch(r'#?\s*0*(\d+)', (value or '').strip())
    return int(m.group(1)) if m else None


class Command(BaseCommand):
    help = 'Give roll #1–#43 (the 1800s founders) the Original Founder badge.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually write changes (default is a dry-run preview).')

    def handle(self, *args, **options):
        apply = options['apply']
        changed, unchanged, clashes = [], [], []

        candidates = ParliamentUser.objects.exclude(
            role_number__isnull=True).exclude(role_number='')
        founders = sorted(
            (m for m in candidates if _roll(m.role_number) in ORIGINAL_FOUNDERS_ROLLS),
            key=lambda m: _roll(m.role_number))

        for m in founders:
            if normalize(m.pledge_class):
                clashes.append(m)
                continue
            old = m.pledge_class
            if old == ORIGINAL_FOUNDERS_LABEL:
                unchanged.append(m)
                continue
            changed.append((m, old))
            if apply:
                m.pledge_class = ORIGINAL_FOUNDERS_LABEL
                m.save(update_fields=['pledge_class'])

        verb = 'Updated' if apply else 'Would update'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {len(changed)} member(s):'))
        for m, old in changed:
            self.stdout.write(
                f'  #{m.role_number} {m.name}: "{old}" -> "{ORIGINAL_FOUNDERS_LABEL}"')
        if unchanged:
            self.stdout.write(f'{len(unchanged)} already tagged.')

        found = {_roll(m.role_number) for m in founders}
        missing = [n for n in ORIGINAL_FOUNDERS_ROLLS if n not in found]
        if missing:
            self.stdout.write(self.style.WARNING(
                f'\nNo member with roll number: '
                f'{", ".join(f"#{n}" for n in missing)}'))

        if clashes:
            self.stdout.write(self.style.WARNING(
                f'\n{len(clashes)} member(s) in roll #1–#43 already belong to '
                f'a modern class (skipped — check the numbering):'))
            for m in clashes:
                self.stdout.write(
                    f'  #{m.role_number} {m.name}: "{m.pledge_class}"')

        if not apply and changed:
            self.stdout.write(self.style.NOTICE(
                '\nDry run — re-run with --apply to write these changes.'))
