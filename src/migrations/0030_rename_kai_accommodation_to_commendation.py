# Hand-written, not machine-generated — see the module docstring below for
# why (Django's makemigrations autodetector, run non-interactively in this
# environment, could not be gotten to answer its own rename questions, and
# a delete+create pair for these models would drop the confidential
# attachments/notes on any row that existed under the old names).

"""
Renames the v3.28.8 "accommodation request" feature to what Mason
actually asked for — "commendation" — on top of a database where
`0029_kai_accommodations` had already applied for real before the
naming mistake was caught same-day. See `changelogs/v3.28.9.md`.

⚠️ `0029_kai_accommodations.py` and `src/models/kai_accommodations.py`
(now stripped to two `upload_to` functions only) are NOT to be deleted or
rewritten again — they're load-bearing history for anyone who applied
0029 before this migration existed. This migration is what corrects that
history forward; it does not un-happen it.

Three renames (RenameModel/RenameField) so any rows that exist under the
old names keep their data, plus:
- `KaiFormField.form_type` rows of `'accommodation'` → `'commendation'`
  (data, not just the choices list — a stale value here would make an
  existing custom field invisible to both forms).
- `KaiCommendation.status` re-mapped from the old five-value vocabulary
  to the new three-value one. No exact mapping exists for `'in_review'`
  vs `'approved'` vs `'denied'`/`'closed'` — see `forwards_relabel_data`
  for the chosen mapping and reasoning.
- `commended_member` added **nullable** even though the model/form treat
  it as required for new submissions (`blank` was never set to `True`,
  so ModelForm still requires it) — a row that predates this migration
  has no honoree recorded anywhere and there is no way to reconstruct
  one; a NOT NULL column would either crash this migration on any
  pre-existing row or force inventing a wrong answer. Matches this
  codebase's standing "leave it blank rather than guess" convention
  (v3.23.0's role-number backfill, and others).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import src.models.kai_commendations
import src.storage


def forwards_relabel_data(apps, schema_editor):
    KaiFormField = apps.get_model('src', 'KaiFormField')
    KaiFormField.objects.filter(form_type='accommodation').update(form_type='commendation')

    KaiCommendation = apps.get_model('src', 'KaiCommendation')
    # Old workflow: pending / in_review / approved / denied / closed.
    # New workflow: pending / acknowledged / archived. There's no exact
    # correspondence — 'in_review' stays 'pending' (still awaiting
    # committee action under either vocabulary); 'approved' (the old
    # workflow's one clearly positive resolution) becomes 'acknowledged';
    # 'denied' and 'closed' (both "no longer active," for different old
    # reasons) both become 'archived', the new workflow's only terminal
    # non-positive state.
    KaiCommendation.objects.filter(status='in_review').update(status='pending')
    KaiCommendation.objects.filter(status='approved').update(status='acknowledged')
    KaiCommendation.objects.filter(status__in=['denied', 'closed']).update(status='archived')


def backwards_relabel_data(apps, schema_editor):
    # Not meaning-preserving in reverse — 'acknowledged' could have come
    # from 'approved' only, but 'archived' collapses two different old
    # values into one and there's no way to tell them apart here. Maps
    # each new value to the single old value it's closest to, on the
    # same "best-effort, not a promise" basis this codebase already uses
    # for irreversible data migrations.
    KaiFormField = apps.get_model('src', 'KaiFormField')
    KaiFormField.objects.filter(form_type='commendation').update(form_type='accommodation')

    KaiCommendation = apps.get_model('src', 'KaiCommendation')
    KaiCommendation.objects.filter(status='acknowledged').update(status='approved')
    KaiCommendation.objects.filter(status='archived').update(status='closed')


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0029_kai_accommodations'),
    ]

    operations = [
        # --- Renames: model, then fields on/pointing at each renamed model ---
        migrations.RenameModel('KaiAccommodationRequest', 'KaiCommendation'),
        migrations.RenameModel('KaiAccommodationFieldResponse', 'KaiCommendationFieldResponse'),
        migrations.RenameModel('KaiAccommodationRequestActivity', 'KaiCommendationActivity'),

        # ⚠️ Must drop the OLD constraint before renaming the field it
        # covers — 0029's UniqueConstraint still names `request_number`
        # at this point in the state, and SQLite rebuilds the whole
        # table (rather than ALTERing in place) to remove a constraint,
        # which fails if the constraint's own field reference no longer
        # exists. The replacement constraint goes back on after the
        # rename, further down.
        migrations.RemoveConstraint(
            model_name='kaicommendation',
            name='uniq_kai_accommodation_request_number',
        ),

        migrations.RenameField('kaicommendation', 'requester', 'submitted_by'),
        migrations.RenameField('kaicommendation', 'resolved_at', 'reviewed_at'),
        migrations.RenameField('kaicommendation', 'resolved_by', 'reviewed_by'),
        migrations.RenameField('kaicommendation', 'request_number', 'commendation_number'),
        migrations.RenameField('kaicommendationfieldresponse', 'request', 'commendation'),
        migrations.RenameField('kaicommendationactivity', 'request', 'commendation'),

        # --- Data: relabel rows under the old vocabulary before anything
        #     that depends on the new one (the choices AlterField below is
        #     cosmetic/Python-only and order doesn't matter for it, but
        #     doing the data pass right after the structural renames keeps
        #     the migration readable in the order things actually change) ---
        migrations.RunPython(forwards_relabel_data, backwards_relabel_data),

        # --- New fields ---
        migrations.AddField(
            model_name='kaicommendation',
            name='commended_member',
            # ⚠️ null=True at the DB level ONLY — `blank` is deliberately
            # left at its default (False), so ModelForm still treats this
            # as required for every new submission. Nullable purely so
            # this AddField can't fail (or force-guess an honoree) against
            # any row that predates this migration. See the module
            # docstring.
            field=models.ForeignKey(
                null=True,
                help_text='The member being commended.',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='kai_commendations_received',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='kaicommendation',
            name='is_submitter_anonymous',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "If checked, the submitter has asked not to be named if the "
                    "committee relays this commendation to the person it's about."
                ),
            ),
        ),

        # --- Choice/vocabulary changes (Python-level only; no DB effect,
        #     but needed so `makemigrations --check` sees the model and the
        #     migration history agree) ---
        migrations.AlterField(
            model_name='kaiformfield',
            name='form_type',
            field=models.CharField(
                choices=[('discipline', 'Discipline Report'), ('commendation', 'Commendation')],
                db_index=True,
                default='discipline',
                help_text='Which Kai form this field appears on.',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='status',
            field=models.CharField(
                choices=[('pending', 'Pending Review'), ('acknowledged', 'Acknowledged'), ('archived', 'Archived')],
                default='pending',
                max_length=20,
            ),
        ),

        # --- Cosmetic parity: help_text / related_name / upload_to on
        #     fields that kept their name but whose surrounding metadata
        #     changed when the model was renamed ---
        migrations.AlterField(
            model_name='kaicommendation',
            name='title',
            field=models.CharField(help_text='Brief summary of what this commendation is for', max_length=255),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='description',
            field=models.TextField(help_text='What did they do? Be specific.'),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='attachment',
            field=models.FileField(
                blank=True,
                null=True,
                help_text='Optional supporting file (e.g. a photo, a screenshot of positive feedback)',
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.kai_commendations.kai_commendation_attachment_path,
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='assigned_to',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text='Committee member handling this commendation.',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='kai_commendations_assigned',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='submitted_by',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='kai_commendations_submitted',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='reviewed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='kai_commendations_reviewed',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='committee_notes',
            field=models.TextField(
                blank=True,
                help_text='Internal committee notes — not shown outside the committee.',
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendation',
            name='commendation_number',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='Per-year identifier, e.g. COM-2026-014. Assigned automatically.',
                max_length=20,
            ),
        ),

        migrations.AddConstraint(
            model_name='kaicommendation',
            constraint=models.UniqueConstraint(
                condition=models.Q(('commendation_number', ''), _negated=True),
                fields=('commendation_number',),
                name='uniq_kai_commendation_number',
            ),
        ),

        migrations.AlterField(
            model_name='kaicommendationfieldresponse',
            name='commendation',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='custom_responses',
                to='src.kaicommendation',
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendationfieldresponse',
            name='field',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='commendation_responses',
                to='src.kaiformfield',
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendationfieldresponse',
            name='file_value',
            field=models.FileField(
                blank=True,
                null=True,
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.kai_commendations.kai_commendation_response_file_path,
            ),
        ),
        migrations.AlterUniqueTogether(
            name='kaicommendationfieldresponse',
            unique_together={('commendation', 'field')},
        ),

        migrations.AlterField(
            model_name='kaicommendationactivity',
            name='commendation',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='activity_log',
                to='src.kaicommendation',
            ),
        ),
        migrations.AlterField(
            model_name='kaicommendationactivity',
            name='action',
            field=models.CharField(
                choices=[
                    ('created', 'Commendation Submitted'),
                    ('status_changed', 'Status Changed'),
                    ('assigned', 'Assigned'),
                    ('notes_updated', 'Committee Notes Updated'),
                    ('reviewed', 'Reviewed'),
                ],
                max_length=30,
            ),
        ),

        migrations.AlterModelOptions(
            name='kaicommendation',
            options={
                'ordering': ['-submitted_at'],
                'verbose_name': 'Kai Commendation',
                'verbose_name_plural': 'Kai Commendations',
            },
        ),
        migrations.AlterModelOptions(
            name='kaicommendationactivity',
            options={
                'ordering': ['-timestamp'],
                'verbose_name': 'Kai Commendation Activity',
                'verbose_name_plural': 'Kai Commendation Activities',
            },
        ),
        migrations.AlterModelOptions(
            name='kaicommendationfieldresponse',
            options={
                'verbose_name': 'Kai Commendation Field Response',
                'verbose_name_plural': 'Kai Commendation Field Responses',
            },
        ),
    ]
