"""
One-time (re-runnable) cleanup: canonicalize stored pledge-class free-text.

Colors and the class list are COMPUTED at render time (src/pledge_classes.py),
so nothing here is required for badges to work — existing "spring 24" already
renders as Gamma. This command just tidies the *stored* values so the roster
data is consistent: it rewrites each member's pledge_class to the canonical
label ("Spring 2024") and fills pledge_class_greek from the registry.

Safe to run anytime; idempotent. Members whose text can't be matched are left
untouched and listed at the end for manual review.

    python manage.py normalize_pledge_classes            # preview only (dry run)
    python manage.py normalize_pledge_classes --apply    # write changes
"""
from django.core.management.base import BaseCommand

from src.models import ParliamentUser
from src.pledge_classes import normalize


class Command(BaseCommand):
    help = 'Canonicalize stored pledge_class / pledge_class_greek values.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually write changes (default is a dry-run preview).')

    def handle(self, *args, **options):
        apply = options['apply']
        changed, unmatched = [], []

        members = ParliamentUser.objects.exclude(
            pledge_class='').exclude(pledge_class__isnull=True)
        for m in members:
            # Resolve from the semester text, or fall back to the greek text.
            c = normalize(m.pledge_class) or normalize(m.pledge_class_greek)
            if c is None:
                unmatched.append(m)  # can't map to a known class — leave it
                continue
            if (m.pledge_class, m.pledge_class_greek) == (c['label'], c['greek']):
                continue  # already canonical
            changed.append((m, (m.pledge_class, m.pledge_class_greek),
                            (c['label'], c['greek'])))
            if apply:
                m.pledge_class, m.pledge_class_greek = c['label'], c['greek']
                m.save(update_fields=['pledge_class', 'pledge_class_greek'])

        verb = 'Updated' if apply else 'Would update'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {len(changed)} member(s):'))
        for m, old, new in changed:
            self.stdout.write(
                f'  {m.name}: "{old[0]}"/"{old[1]}" -> "{new[0]}"/"{new[1]}"')

        if unmatched:
            self.stdout.write(self.style.WARNING(
                f'\n{len(unmatched)} member(s) had unrecognized classes '
                f'(left as-is, review manually):'))
            for m in unmatched:
                self.stdout.write(f'  {m.name}: "{m.pledge_class}"')

        if not apply and changed:
            self.stdout.write(self.style.NOTICE(
                '\nDry run — re-run with --apply to write these changes.'))
