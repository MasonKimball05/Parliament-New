# 09-25-26 — seed SectionRevision history from resolutions that already passed.
#
# Each applied ResolutionAmendment knows the section's text when the amendment
# was DRAFTED (`original_text_snapshot`) and the resolution knows when it
# passed. That is a good-but-approximate "text in force until the resolution
# passed": if the section was edited between drafting and passage, the edit is
# not visible here. Each row says so in `note`. Idempotent; reversible.

from django.db import migrations

NOTE = ("Reconstructed from the resolution's draft-time snapshot; edits made "
        "between drafting and passage are not reflected.")


def forwards(apps, schema_editor):
    Amendment = apps.get_model('src', 'ResolutionAmendment')
    Revision = apps.get_model('src', 'SectionRevision')
    for a in Amendment.objects.filter(applied=True).select_related('resolution', 'section'):
        if Revision.objects.filter(section=a.section, resolution=a.resolution, source='backfill').exists():
            continue
        Revision.objects.create(
            section=a.section,
            title=a.section.title or '',
            content=a.original_text_snapshot or '',
            was_active=True,
            replaced_at=a.resolution.passed_at or a.added_at,
            source='backfill',
            resolution=a.resolution,
            note=NOTE,
        )


def backwards(apps, schema_editor):
    apps.get_model('src', 'SectionRevision').objects.filter(source='backfill').delete()


class Migration(migrations.Migration):
    dependencies = [('src', '0052_section_history')]
    operations = [migrations.RunPython(forwards, backwards)]
