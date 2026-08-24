"""
v3.23.0 — help text only. **No schema change, no data change.**

`ParliamentUser.user_id` gained a `help_text` saying what it now is: a permanent
internal identifier that is not the roll number. Django records `help_text` in
migration state, so the file has to exist; the column is byte-for-byte what it
was. Separate from `0021` deliberately — that one touches data and this one
cannot, and a reviewer should be able to tell which is which at a glance.
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0021_backfill_role_numbers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parliamentuser',
            name='user_id',
            field=models.CharField(help_text='Permanent internal identifier. Never changes, never reused, and NOT the roll number — see role_number for that.', max_length=30, primary_key=True, serialize=False, unique=True),
        ),
    ]
