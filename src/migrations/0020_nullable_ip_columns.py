"""
v3.21.7 — three `inet` columns learn to say "no address", and one CharField
stops promising CIDR support it never had.

⚠️ SAFE TO RUN ON A LIVE DATABASE, and here is why, because "AlterField" is not
by itself reassuring:

* The three `GenericIPAddressField` changes are **NOT NULL → NULL**. That is a
  widening. No existing row can violate it, nothing is rewritten, no backfill is
  needed, and it cannot fail on data.
* The `IPBlacklist` change is **metadata only** — `validators` and `help_text`.
  Django emits no schema change a `CharField(max_length=45, unique=True)` did
  not already have. Validators do not run on `objects.create()`; they run on
  ModelForms and `full_clean()`, i.e. on the admin form where a human types.

⚠️ WHAT THIS MIGRATION DELIBERATELY DOES **NOT** DO: convert
`IPBlacklist.ip_address` to `GenericIPAddressField`. That would be
`ALTER COLUMN ... TYPE inet USING ip_address::inet`, which **hard-fails
mid-deploy** on any existing row PostgreSQL cannot cast — and the reason this
release exists is that we know non-address values could be written there. Run
`manage.py preflight`, which now counts them, and do the conversion once it
reports zero.

Reversible: `migrate src 0019` restores NOT NULL, which will fail if any NULL
rows have been written by then. That is correct and is the point of the column.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0019_education_analysis_and_absences'),
    ]

    operations = [
        migrations.AlterField(
            model_name='honeypotaccess',
            name='ip_address',
            field=models.GenericIPAddressField(blank=True, help_text='Client address, or empty when none could be resolved. A hit with no address is still evidence that the hit happened.', null=True),
        ),
        migrations.AlterField(
            model_name='ipblacklist',
            name='ip_address',
            field=models.CharField(help_text='Single IP address to block. CIDR ranges are NOT supported — matching is exact, so a range would block nothing.', max_length=45, unique=True, validators=[django.core.validators.validate_ipv46_address]),
        ),
        migrations.AlterField(
            model_name='loginlockout',
            name='ip_address',
            field=models.GenericIPAddressField(blank=True, help_text='IP address that was locked out, or empty for a username-only lockout with no resolvable address.', null=True),
        ),
        migrations.AlterField(
            model_name='quarantinedaccount',
            name='ip_address',
            field=models.GenericIPAddressField(blank=True, help_text='IP address that triggered quarantine, or empty if none could be resolved.', null=True),
        ),
    ]
