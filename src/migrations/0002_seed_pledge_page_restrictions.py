# Hand-authored data migration — seeds default-block PledgePageRestriction rows.
# (Ported from pre-consolidation migration 0218_seed_pledge_page_restrictions;
# the full pre-07-05-26 migration history is archived in
# Claude/Backups/src-migrations-pre-consolidation-07-05-26.tar.gz and on prod.)
#
# The @pledge_page_allowed decorator defaults to OPEN when no restriction row
# exists, which would silently unblock pledges from sensitive pages on a fresh
# deploy. These rows restore the intended default: pledges are blocked unless
# the VPE explicitly grants access via the education dashboard.

from django.db import migrations


PAGES = [
    ('chapter_documents', 'Chapter Documents'),
    ('slating_apply',     'Slating — Apply'),
    ('slating_results',   'Slating — Results'),
]


def seed_restrictions(apps, schema_editor):
    PledgePageRestriction = apps.get_model('src', 'PledgePageRestriction')
    for url_name, display_name in PAGES:
        PledgePageRestriction.objects.get_or_create(
            url_name=url_name,
            defaults={
                'display_name': display_name,
                'allowed_phases': [],   # empty = blocked for all phases
                'updated_by': None,
            },
        )


def remove_restrictions(apps, schema_editor):
    PledgePageRestriction = apps.get_model('src', 'PledgePageRestriction')
    PledgePageRestriction.objects.filter(
        url_name__in=[p[0] for p in PAGES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_restrictions, reverse_code=remove_restrictions),
    ]
