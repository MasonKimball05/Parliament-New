"""
v3.19.3 — draft attachments get opaque names and remember the author's.

⚠️ HAND-WRITTEN. Run `manage.py makemigrations --check` after pulling (the CI
gate from v3.18.1). If it reports pending changes, delete this file and run
`makemigrations` — do not edit the models to match.

TWO OPERATIONS, AND NEITHER TOUCHES EXISTING FILES ON DISK.

`AlterField` on `document` changes `upload_to` from the string
`'legislation_drafts/'` to the callable `legislation_draft_upload_path`.
`upload_to` is consulted at SAVE time only, so this affects new uploads and
re-uploads and leaves every stored path exactly where it is. Existing drafts
keep their slugified, guessable names.

**That is deliberate, and it is safe, because the random name was never the
access control.** `serve_legislation_draft_document` is, and it applies to every
draft equally from the moment it deploys — old rows included. The uuid is
defence in depth for the case where a path leaks some other way (a copied link,
a screenshot, a proxy log). Renaming files on disk in a migration means moving
bytes inside a transaction and leaving a half-migrated directory if it fails
partway, which is a materially worse trade than "the twelve drafts that exist
today keep a name nobody can reach anyway."

If you would rather rotate them, do it as a separate `manage.py` command after
deploying, where a failure is re-runnable and nothing is holding a lock.

`document_original_name` is added blank. `LegislationDraft.document_display_name`
falls back to the stored basename when it is empty, which is exactly right for
pre-v3.19.3 rows: their stored name IS the author's name.
"""
from django.db import migrations, models

import src.models.legislation
import src.storage


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0015_governing_document_foreword_and_ordering'),
    ]

    operations = [
        migrations.AddField(
            model_name='legislationdraft',
            name='document_original_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='legislationdraft',
            name='document',
            field=models.FileField(
                blank=True, null=True,
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.legislation.legislation_draft_upload_path,
                validators=[src.models.legislation.validate_legislation_file],
            ),
        ),
    ]
