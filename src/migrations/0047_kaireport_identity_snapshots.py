# 09-21-26 — Kai submitter/accused identity survives ParliamentUser.anonymize().
#
# See KaiReport.submitter_display_name / accused_display_name (src/models/kai.py)
# for the full reasoning. Two additive CharFields, plus a data migration that
# backfills them for every existing report whose party isn't ALREADY
# anonymized — a party anonymized before this migration ran has no earlier
# snapshot to recover; nothing here can bring that name back. This only
# prevents the loss going forward and for every party still intact today.

from django.db import migrations, models


def backfill_identity_snapshots(apps, schema_editor):
    """
    Mirrors KaiReport.save()'s sync logic, but against the historical model
    (no custom save()/properties available here) and in bulk rather than
    row-by-row — this table is small (chapter-scale Kai case volume), so a
    single pass with select_related is cheap and simple.
    """
    KaiReport = apps.get_model('src', 'KaiReport')
    ParliamentUser = apps.get_model('src', 'ParliamentUser')

    reports = list(
        KaiReport.objects
        .select_related('submitted_by', 'targeted_to')
        .only(
            'id', 'submitted_by_name_snapshot', 'targeted_to_name_snapshot',
            'submitted_by__name', 'submitted_by__is_anonymized',
            'targeted_to__name', 'targeted_to__is_anonymized',
        )
    )
    to_update = []
    for report in reports:
        changed = False
        submitter = report.submitted_by
        if submitter is not None and not submitter.is_anonymized:
            if report.submitted_by_name_snapshot != submitter.name:
                report.submitted_by_name_snapshot = submitter.name
                changed = True
        accused = report.targeted_to
        if accused is not None and not accused.is_anonymized:
            if report.targeted_to_name_snapshot != accused.name:
                report.targeted_to_name_snapshot = accused.name
                changed = True
        if changed:
            to_update.append(report)

    if to_update:
        KaiReport.objects.bulk_update(
            to_update, ['submitted_by_name_snapshot', 'targeted_to_name_snapshot'],
            batch_size=500,
        )


def noop_reverse(apps, schema_editor):
    # Deliberately a no-op. Un-setting the snapshot on reverse would throw
    # away real, currently-recoverable data for no reason — the forward
    # migration is purely additive/protective, so there is nothing to undo
    # that reversing the AddField operations below doesn't already cover.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0046_parliamentuser_is_anonymized_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='kaireport',
            name='submitted_by_name_snapshot',
            field=models.CharField(
                blank=True, default='', max_length=150,
                help_text=(
                    "Submitter's display name as of the last time this report was "
                    "saved while their account was intact. Falls back to this once "
                    "ParliamentUser.anonymize() has scrubbed the live name. See "
                    "KaiReport.submitter_display_name."
                ),
            ),
        ),
        migrations.AddField(
            model_name='kaireport',
            name='targeted_to_name_snapshot',
            field=models.CharField(
                blank=True, default='', max_length=150,
                help_text="Same as submitted_by_name_snapshot, for targeted_to. See accused_display_name.",
            ),
        ),
        migrations.RunPython(backfill_identity_snapshots, noop_reverse),
    ]
