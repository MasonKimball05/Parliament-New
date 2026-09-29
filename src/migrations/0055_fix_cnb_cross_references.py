"""
09-27-26 — correct four cross-references in the C&B text (Mason: "just fix
it"). These are the `check_cnb_references` findings; wording only, no rule
changes. The seed text (src/management/data/cnb_data.py) got the same edits.

SAFE ON ANY DATABASE:
  * Each fix replaces an exact phrase ONLY IF that phrase is present in that
    section. A section that a resolution has already reworded (or that does
    not exist) is left alone, so re-running or running on a fresh DB is a no-op.
  * Each change first writes a SectionRevision holding the outgoing text
    (source 'direct_edit', with a note), exactly like an edit in the C&B
    manager, so the history page and `?as_of=` stay correct.
  * No schema change.
"""
from django.db import migrations
from django.utils import timezone

NOTE = 'Cross-reference correction (09-27-26, migration 0055)'

# (doc_type, article, section, wrong phrase, corrected phrase)
FIXES = [
    ('constitution', 'V', '1',
     'Article VII, Section 1 (a) (vi-x) be met',
     'Article VII, Section 1 (a) (vi-x) of the Bylaws be met'),
    ('bylaws', 'VI', '2',
     'Operations of the Kai Committee can be found in Article VI, Section 1 (a) of the Bylaws',
     'Operations of the Kai Committee can be found in Article VII, Section 1 (a) of the Bylaws'),
    ('bylaws', 'VII', '10',
     'See Article VI of the Bylaws for amendments to the Bylaws. See Article VII of the Constitution for amendments to the Constitution',
     'See Article X of the Bylaws for amendments to the Bylaws. See Article VI of the Constitution for amendments to the Constitution'),
]


def apply_fixes(apps, schema_editor=None, reverse=False):
    Section = apps.get_model('src', 'Section')
    SectionRevision = apps.get_model('src', 'SectionRevision')
    changed = 0
    for doc_type, art, sec, wrong, right in FIXES:
        old, new = (right, wrong) if reverse else (wrong, right)
        section = Section.objects.filter(
            article__document__doc_type=doc_type, article__number=art, number=sec,
        ).first()
        if section is None or old not in section.content:
            continue
        SectionRevision.objects.create(
            section=section, title=section.title or '', content=section.content,
            was_active=section.is_active, replaced_at=timezone.now(),
            source='direct_edit', note=(NOTE + (' — reversed' if reverse else ''))[:300],
        )
        section.content = section.content.replace(old, new)
        section.save(update_fields=['content'])
        changed += 1
    return changed


def forwards(apps, schema_editor):
    apply_fixes(apps, schema_editor)


def backwards(apps, schema_editor):
    apply_fixes(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0054_meeting_agenda'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
