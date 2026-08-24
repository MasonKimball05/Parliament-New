"""
v3.23.0 — give every existing brother a `role_number` (data only, no schema).

⚠️ WHY THIS IS NEEDED, AND WHY IT IS THE ONLY DATA CHANGE IN THIS RELEASE.

Until now, initiation changed a member's **primary key** from `P-C7JKZY` to his
roll number, so for everyone initiated before today `user_id` *is* the roll
number. `role_number` was set at the same time — but only by the initiation
path, so anyone created directly as a Member (an officer adding an existing
brother, a CSV import, the seeds) has `user_id = '173'` and `role_number = NULL`.

v3.23.0 stops moving the primary key, which means `role_number` becomes the only
place the roll number lives. Without this backfill those members' roll numbers
would render blank across the 32 templates that show `#{{ role_number }}` —
data that was never lost, just never written to the column that now matters.

⚠️ SAFE, AND HERE IS THE ARGUMENT RATHER THAN THE ADJECTIVE:

* **No schema change.** No column is added, dropped, retyped or re-indexed.
* **It only ever fills a blank.** The `role_number__isnull=True` /
  `role_number=''` filter means a member who already has one is untouched, so
  this cannot overwrite a correction an officer made by hand.
* **Pledges are excluded.** A pledge has no roll number yet — that is the whole
  point of initiation — and copying `P-C7JKZY` into `role_number` would invent
  one. `member_type='Pledge'` is skipped explicitly.
* **`role_number` is `unique=True`**, and the source values come from a column
  that is itself unique, so the backfill cannot collide. The exception is a
  member whose `user_id` happens to equal *another* member's existing
  `role_number` — handled below rather than assumed away, because "cannot
  happen" is how the last few findings started.

Reverse is a no-op on purpose. Un-setting a roll number is not a rollback, it is
data loss: after this runs, an officer may have corrected one, and a reverse
migration cannot tell his value from ours.
"""
from django.db import migrations


def backfill_role_numbers(apps, schema_editor):
    ParliamentUser = apps.get_model('src', 'ParliamentUser')

    # Every roll number already in use, so the loop below cannot create a
    # duplicate against a member it is not touching.
    taken = set(
        ParliamentUser.objects
        .exclude(role_number__isnull=True).exclude(role_number='')
        .values_list('role_number', flat=True)
    )

    candidates = (
        ParliamentUser.objects
        .exclude(member_type='Pledge')
        .filter(role_number__isnull=True)
    ) | (
        ParliamentUser.objects
        .exclude(member_type='Pledge')
        .filter(role_number='')
    )

    filled, skipped = 0, []
    for member in candidates.distinct():
        value = (member.user_id or '').strip()
        if not value or value in taken:
            # Leave it blank and say so. A wrong roll number is worse than a
            # missing one: missing renders as nothing, wrong renders as somebody
            # else's number, and only one of those is obviously broken.
            skipped.append(member.user_id)
            continue
        member.role_number = value
        member.save(update_fields=['role_number'])
        taken.add(value)
        filled += 1

    if skipped:
        # Printed rather than logged: migrations run on a terminal somebody is
        # watching during a deploy, which is the one moment this is readable.
        print(
            f'\n  0021: filled {filled} role_number(s); '
            f'{len(skipped)} left blank because the value was already taken '
            f'or empty: {", ".join(skipped[:10])}'
            + (' …' if len(skipped) > 10 else '')
            + '\n  Set those by hand from Manage Members.'
        )


def noop_reverse(apps, schema_editor):
    """Deliberately does nothing. See the module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0020_nullable_ip_columns'),
    ]

    operations = [
        migrations.RunPython(backfill_role_numbers, noop_reverse),
    ]
