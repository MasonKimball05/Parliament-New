"""
Create the default committees and roles a chapter is missing.

The canonical defaults are `Committee.DEFAULT_COMMITTEES` and
`Role.DEFAULT_ROLES`; which of them the code depends on is registered in
`src/chapter_structure.py` (and reported by `src.W006`).

⚠️ Multi-chapter slice 3f (09-29-26): rows are matched by CODE, not by id.

This command used to `get_or_create(id=<n>)` and then overwrite the code and
name of whatever row had that id. On the database the defaults were written
for, id 4 is the Kai Committee, so that was harmless. On any other database
(a new chapter, or one where rows were added and deleted), id 4 can be any
committee, so running this would silently rename it to "Kai Committee" with
code KAI. It also caught per-row errors inside one atomic block, which on
PostgreSQL poisons every later statement.

Now:
  * a default whose code already exists is left alone (chapters may rename
    their committees and roles; only the codes are load-bearing);
  * a missing one is created with a database-assigned id;
  * if the default NAME is already taken by a row with another code, nothing is
    written for that default and it is reported as a conflict to fix by hand;
  * a special-committee flag (`is_kai_committee` …) is set on a newly created
    committee only if no committee already has it;
  * `--reset-names` puts the default name back on existing defaults;
  * `--dry-run` shows what would happen.

`--skip-existing` is still accepted; it is now the default behaviour.

Usage:
    python manage.py restore_committees_and_roles [--dry-run] [--reset-names]
"""
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from src.models import Committee, Role


class Command(BaseCommand):
    help = "Create the default committees and roles this chapter is missing (matched by code)"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report only; write nothing')
        parser.add_argument('--reset-names', action='store_true',
                            help='Also restore the default name on defaults that exist under another name')
        parser.add_argument('--skip-existing', action='store_true',
                            help='Accepted for compatibility; existing rows are always left alone now')

    def handle(self, *args, **options):
        dry, reset = options['dry_run'], options['reset_names']
        with transaction.atomic():
            c = self._restore(Committee, Committee.DEFAULT_COMMITTEES, 'Committees', reset)
            r = self._restore(Role, Role.DEFAULT_ROLES, 'Roles', reset)
            flags = self._set_flags(c['created_codes'])
            if dry:
                transaction.set_rollback(True)

        verb = 'Would create' if dry else 'Created'
        self.stdout.write(self.style.MIGRATE_HEADING('\nSummary:'))
        for label, s in (('Committees', c), ('Roles', r)):
            self.stdout.write(f"  {label}: {verb.lower()} {len(s['created_codes'])}, "
                              f"renamed {s['renamed']}, already present {s['present']}, conflicts {len(s['conflicts'])}")
        for flag, code in flags:
            self.stdout.write(f'  Set {flag} on {code}')
        conflicts = c['conflicts'] + r['conflicts']
        for msg in conflicts:
            self.stdout.write(self.style.ERROR(f'  ✗ {msg}'))

        if not dry:
            from src.chapter_structure import structure_problems
            problems = structure_problems()
            if problems:
                self.stdout.write(self.style.WARNING('\nStill missing or ambiguous (src.W006):'))
                for p in problems:
                    self.stdout.write(self.style.WARNING(f'  - {p}'))
            else:
                self.stdout.write(self.style.SUCCESS('\n✓ Every role and committee the code depends on is present.'))

    def _restore(self, model, defaults, label, reset):
        self.stdout.write(self.style.MIGRATE_LABEL(f'{label}:'))
        stats = {'created_codes': [], 'renamed': 0, 'present': 0, 'conflicts': []}
        for _, code, name in defaults:
            existing = model.objects.filter(code=code).first()
            if existing is not None:
                if reset and existing.name != name:
                    if model.objects.filter(name=name).exclude(pk=existing.pk).exists():
                        stats['conflicts'].append(
                            f'{label[:-1]} {code}: cannot rename to {name!r}, another row has that name')
                        continue
                    self.stdout.write(f'  ↻ {code}: {existing.name!r} → {name!r}')
                    existing.name = name
                    existing.save(update_fields=['name'])
                    stats['renamed'] += 1
                else:
                    stats['present'] += 1
                continue
            clash = model.objects.filter(name=name).first()
            if clash is not None:
                stats['conflicts'].append(
                    f'{label[:-1]} {code} is missing, but {name!r} already exists with code '
                    f'{clash.code!r}. If that row IS the {code} one, set its code to {code!r} in the admin.')
                continue
            try:
                with transaction.atomic():   # savepoint: one bad row can't poison the rest
                    model.objects.create(code=code, name=name)
            except IntegrityError as e:
                stats['conflicts'].append(f'{label[:-1]} {code}: {e}')
                continue
            self.stdout.write(self.style.SUCCESS(f'  ✓ {code} - {name}'))
            stats['created_codes'].append(code)
        return stats

    def _set_flags(self, created_codes):
        """Special flags go on a committee this run created, and only when unclaimed."""
        from src.apps import COMMITTEE_FLAG_DEFAULTS
        done = []
        for code, flag in COMMITTEE_FLAG_DEFAULTS.items():
            if code not in created_codes or Committee.objects.filter(**{flag: True}).exists():
                continue
            Committee.objects.filter(code=code).update(**{flag: True})
            done.append((flag, code))
        return done
