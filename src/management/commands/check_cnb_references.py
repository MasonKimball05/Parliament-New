"""
Report cross-reference problems in the live Constitution & Bylaws (09-25-26).

    python manage.py check_cnb_references            # report, exit 0
    python manage.py check_cnb_references --strict   # exit 1 if any ERROR

See src/cnb_crossrefs.py. The same findings are shown to the C&B chair on the
C&B manager dashboard. Fixing the text is a chapter resolution.
"""
from django.core.management.base import BaseCommand, CommandError

from src.cnb_crossrefs import Structure, check


class Command(BaseCommand):
    help = 'Report C&B cross-references that point to missing or mismatched sections'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true', help='Exit non-zero if any ERROR is found')

    def handle(self, *args, **options):
        findings = check(Structure.from_db())
        if not findings:
            self.stdout.write(self.style.SUCCESS('No cross-reference problems found.'))
            return
        errors = 0
        for f in findings:
            style = self.style.ERROR if f.level == 'error' else self.style.WARNING
            errors += f.level == 'error'
            self.stdout.write(style(f'{f.level.upper():7} {f.source}: "{f.ref_text}"'))
            self.stdout.write(f'        {f.message}')
        self.stdout.write(f'\n{errors} error(s), {len(findings) - errors} warning(s).')
        if options['strict'] and errors:
            raise CommandError(f'{errors} cross-reference error(s)')
