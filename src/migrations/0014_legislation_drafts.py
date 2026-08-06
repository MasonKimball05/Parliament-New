"""
v3.19.0 — private legislation drafts + deferred availability notification.

⚠️ HAND-WRITTEN. Run `manage.py makemigrations --check` after pulling this: the
CI gate added in v3.18.1 exists precisely to catch a hand-written migration that
does not match what the models would generate. If it reports pending changes,
delete this file and run `makemigrations` — do not "fix" it by editing the
models to match.

The `RunPython` step is the load-bearing part; see its own comment.
"""
from django.db import migrations, models
import django.db.models.deletion

import src.models.legislation
import src.storage


def stamp_existing_legislation_as_announced(apps, schema_editor):
    """
    Backfill `availability_notified_at` on every row that predates this field.

    NULL means "the chapter has not been told about this bill yet", and
    `tasks.notify_available_legislation` claims NULL rows whose `available_at`
    has passed. Every bill in the table satisfies that — so without this step,
    the first beat tick after deploy would push one notification per historical
    bill to all 47 active members.

    `available_at` is used rather than `created_at` because it is the moment the
    announcement would have described, and it is non-null on every row (the
    field is required). Rows are stamped in bulk with a single UPDATE; there is
    no per-row work here and nothing to iterate.
    """
    Legislation = apps.get_model('src', 'Legislation')
    Legislation.objects.filter(availability_notified_at__isnull=True).update(
        availability_notified_at=models.F('available_at'),
    )


def unstamp(apps, schema_editor):
    """Reverse: clear the stamp. Harmless — the column is dropped after this."""
    Legislation = apps.get_model('src', 'Legislation')
    Legislation.objects.update(availability_notified_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0013_kai_break_glass_grant'),
    ]

    operations = [
        migrations.AddField(
            model_name='legislation',
            name='availability_notified_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When the chapter was notified this became available. '
                          'NULL means not yet announced.',
            ),
        ),
        migrations.RunPython(
            stamp_existing_legislation_as_announced,
            unstamp,
        ),
        migrations.CreateModel(
            name='LegislationDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(
                    blank=True,
                    help_text='Working text. Unlike Legislation, a draft may be empty — '
                              'the 20-character floor is enforced at publish, not while writing.',
                )),
                ('document', models.FileField(
                    blank=True, null=True,
                    storage=src.storage.DualLocationStorage(),
                    upload_to='legislation_drafts/',
                    validators=[src.models.legislation.validate_legislation_file],
                )),
                ('planned_available_at', models.DateTimeField(
                    blank=True, null=True,
                    help_text='When you intend to present this. Becomes available_at at publish.',
                )),
                ('planned_voting_ends_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(
                    blank=True,
                    help_text='Private notes. Never copied to the published bill.',
                )),
                ('vote_mode', models.CharField(
                    choices=[('percentage', 'Percentage'), ('piecewise', 'Piecewise'), ('plurality', 'Plurality')],
                    default='percentage', max_length=20,
                )),
                ('required_percentage', models.CharField(
                    choices=[('51', '51%'), ('60', '60%'), ('67', '67%'), ('75', '75%'), ('100', 'Unanimous')],
                    default='51', max_length=10,
                )),
                ('anonymous_vote', models.BooleanField(default=False)),
                ('allow_abstain', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('author', models.ForeignKey(
                    help_text='Only this member can see, edit or publish the draft.',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='legislation_drafts',
                    to='src.parliamentuser',
                )),
                ('published_legislation', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='source_draft',
                    to='src.legislation',
                )),
            ],
            options={
                'verbose_name': 'Legislation Draft',
                'verbose_name_plural': 'Legislation Drafts',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='legislationdraft',
            index=models.Index(fields=['author', '-updated_at'], name='src_legdraft_author_upd_idx'),
        ),
    ]
