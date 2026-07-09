"""
Management command to seed default officer transition checklist items.
Run with: python manage.py seed_transition_checklist

Idempotent — matches on (role, text); re-running never duplicates items.
Items are meant to be edited/extended in the admin afterward.
"""
from django.core.management.base import BaseCommand

from src.models import Role, TransitionChecklistItem


# (role_code_or_None, text) — role None = applies to every role.
DEFAULT_ITEMS = [
    # Global items (every role)
    (None, 'Meet with your predecessor for a full handoff conversation'),
    (None, 'Receive all account credentials and update the shared password record'),
    (None, 'Read the officer guide section for your position'),
    (None, 'Confirm access to your position\'s email, drive folders, and tools'),
    (None, 'Review your position\'s budget and outstanding expenses with VPF'),
    (None, 'Transfer ownership of recurring calendar events you now run'),
    (None, 'Review open action items your predecessor left unfinished'),
    (None, 'Verify your Parliament role, permissions, and committee memberships are correct'),
    # Role-specific examples (edit/extend in admin)
    ('President', 'Schedule introductions with the chapter advisor and university contacts'),
    ('President', 'Review the risk management plan and emergency procedures'),
    ('VPF', 'Get signature authority transferred on the chapter bank account'),
    ('VPF', 'Review outstanding dues, payment plans, and the budget spreadsheet'),
    ('VPA', 'Confirm you can administer Parliament (user management, roles, feature flags)'),
    ('VPRM', 'Review incident reports from the previous term and open follow-ups'),
    ('VPE', 'Review the current pledge program status and education calendar'),
    ('VPR', 'Get access to the recruitment pipeline and candidate records'),
]


class Command(BaseCommand):
    help = 'Seeds default officer transition checklist items (idempotent)'

    def handle(self, *args, **kwargs):
        roles_by_code = {r.code: r for r in Role.objects.all()}
        created = skipped = missing_role = 0

        for i, (role_code, text) in enumerate(DEFAULT_ITEMS):
            role = None
            if role_code is not None:
                role = roles_by_code.get(role_code)
                if role is None:
                    self.stdout.write(self.style.WARNING(
                        f'  Skipping (no role with code {role_code!r}): {text}'
                    ))
                    missing_role += 1
                    continue

            _, was_created = TransitionChecklistItem.objects.get_or_create(
                role=role, text=text,
                defaults={'order': i * 10, 'is_active': True},
            )
            if was_created:
                created += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. Created {created}, already existed {skipped}'
            + (f', skipped {missing_role} (missing role)' if missing_role else '') + '.'
        ))
