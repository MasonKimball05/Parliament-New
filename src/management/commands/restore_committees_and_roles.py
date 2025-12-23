"""
Management command to restore committees and roles after database reset.
This command recreates all committees and VP roles from the defaults defined in models.py.

The canonical source of truth is:
- Committee.DEFAULT_COMMITTEES in models.py
- Role.DEFAULT_ROLES in models.py

Usage:
    python manage.py restore_committees_and_roles
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from src.models import Committee, Role


class Command(BaseCommand):
    help = 'Restore committees and VP roles to database from defaults defined in models.py'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip committees/roles that already exist instead of updating them',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        skip_existing = options['skip_existing']

        self.stdout.write(self.style.MIGRATE_HEADING('Starting committee and role restoration...'))

        # Restore Committees
        self.stdout.write('\n' + self.style.MIGRATE_LABEL('Restoring Committees:'))
        committees_created = 0
        committees_updated = 0
        committees_skipped = 0

        for committee_id, code, name in Committee.DEFAULT_COMMITTEES:
            try:
                committee, created = Committee.objects.get_or_create(
                    id=committee_id,
                    defaults={'code': code, 'name': name}
                )

                if created:
                    committees_created += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'  ✓ Created: {code} - {name} (ID: {committee_id})')
                    )
                elif skip_existing:
                    committees_skipped += 1
                    self.stdout.write(
                        self.style.WARNING(f'  ⊘ Skipped: {code} - {name} (ID: {committee_id})')
                    )
                else:
                    # Update existing committee
                    committee.code = code
                    committee.name = name
                    committee.save()
                    committees_updated += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'  ↻ Updated: {code} - {name} (ID: {committee_id})')
                    )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Error with {code}: {str(e)}')
                )

        # Restore Roles
        self.stdout.write('\n' + self.style.MIGRATE_LABEL('Restoring VP Roles:'))
        roles_created = 0
        roles_updated = 0
        roles_skipped = 0

        for role_id, code, name in Role.DEFAULT_ROLES:
            try:
                role, created = Role.objects.get_or_create(
                    id=role_id,
                    defaults={'code': code, 'name': name}
                )

                if created:
                    roles_created += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'  ✓ Created: {code} - {name} (ID: {role_id})')
                    )
                elif skip_existing:
                    roles_skipped += 1
                    self.stdout.write(
                        self.style.WARNING(f'  ⊘ Skipped: {code} - {name} (ID: {role_id})')
                    )
                else:
                    # Update existing role
                    role.code = code
                    role.name = name
                    role.save()
                    roles_updated += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'  ↻ Updated: {code} - {name} (ID: {role_id})')
                    )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Error with {code}: {str(e)}')
                )

        # Summary
        self.stdout.write('\n' + self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(
            self.style.SUCCESS(
                f'  Committees: {committees_created} created, {committees_updated} updated, {committees_skipped} skipped'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'  Roles: {roles_created} created, {roles_updated} updated, {roles_skipped} skipped'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(f'\n✓ Restoration complete!')
        )
