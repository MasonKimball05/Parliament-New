"""
v3.17.4 — `Attendance.date`: `auto_now_add=True` -> `default=timezone.localdate`.

WHY
    `auto_now_add` populates a DateField from `datetime.date.today()`, i.e. the
    server-local (Central) date, while every caller looked the row up with
    `timezone.now().date()`, i.e. the UTC date. Those differ from 19:00 Central
    until midnight, so `update_or_create()` could not find the row it had just
    written and inserted a duplicate instead — every evening, which is when the
    meetings that produce attendance actually happen. `auto_now_add` also made
    the field non-editable, so the explicit `date=` the callers passed was
    discarded on insert; that is why the callers alone could not be fixed.

SAFETY / COST
    Nothing about the column changes — still `date NOT NULL`, no type change, no
    new constraint. A Django `default` lives in Python, not in the DDL, so on
    PostgreSQL this migration emits NO SQL AT ALL: no rewrite, no lock, instant,
    and safe to run against the live table at any size. (Verified by building
    the statement with the Postgres schema editor: it produced nothing.) SQLite
    rebuilds the table, as it does for every AlterField — irrelevant on prod.

    It does NOT backfill. Existing rows keep their stored value, which is already
    the Central date `date.today()` wrote, so converging the readers on
    `localdate()` is what makes history read correctly. Converging on UTC would
    have misdated every row already in the table.

    Duplicate rows this bug already created are NOT cleaned up here — deleting
    attendance is a judgement call, not a migration. See the deploy notes for the
    query that finds them.
"""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0009_activitylog_object_id_charfield'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attendance',
            name='date',
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
    ]
