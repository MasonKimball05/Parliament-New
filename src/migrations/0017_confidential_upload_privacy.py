"""
v3.19.6 — opaque names for the four confidential upload directories.

⚠️ HAND-WRITTEN. Run `manage.py makemigrations --check` after pulling (the CI
gate from v3.18.1). If it reports pending changes, delete this file and run
`makemigrations` — do not edit the models to match.

FOUR `AlterField`s. NO DATA STEP. NOTHING ON DISK MOVES.

Each changes `upload_to` from a directory string to a module-level callable
returning `<directory>/<uuid>.<ext>`:

    KaiReport.attachment                  kai_reports/
    KaiReportFieldResponse.file_value     kai_reports/custom_fields/
    SlatingApplication.gpa_screenshot     slating/gpa_screenshots/
    SlatingApplicationResponse.file_value slating/application_files/

`upload_to` is consulted at SAVE time only, so this affects new uploads and
re-uploads and leaves every stored path exactly where it is.

⚠️ THE SAME REASONING AS `0016`, AND IT IS ONLY VALID FOR THE SAME REASON.
`0016` declined to rename existing draft files on the stated grounds that *"the
random name was never what was protecting them."* v3.19.5 then discovered that
sentence had been false for two days, because the `/media/` route the drafts
were reachable through had never been closed — so for that window the guessable
name WAS the protection, and `0016`'s reasoning was retroactively wrong.

It is correct here, and the reason is stated so nobody has to re-derive it: the
access controls land in the SAME RELEASE as this migration. All four directories
enter `PRIVATE_MEDIA_PREFIXES` and gain ownership-aware views in
`src/view/serve_private_upload.py`, and `serve_media` refuses them on the
resolved path. Old rows and new rows are protected identically from the moment
this deploys. **If any part of that is reverted, this migration's premise goes
with it.**

WHY NO RENAME, GIVEN THAT THE EXISTING NAMES ARE GUESSABLE
----------------------------------------------------------
The files already on disk are `slugify()` of whatever the uploader called them —
`img-4471.jpeg`, `screenshot.png`, `transcript.png`, `doctors-note.pdf`. Those
names do not change here, so the guessable population stops growing and is not
retired.

That is the deliberate trade. Renaming means moving real evidence inside a
transaction and leaving a half-migrated directory if it fails partway; the
downside it buys off is a name that, with the routes shut, nobody can reach.
Same call `0016` made, made again with the routes actually shut this time.

If you would rather rotate them, do it as a separate `manage.py` command after
deploying, where a failure is re-runnable and nothing is holding a lock.

⚠️ `excuse_documents/`, `service_hours/` and `bug_reports/` deliberately do NOT
get uuid names. They are closed by their routes like everything else; the uuid
is defence in depth and was scoped to the four directories where a leaked path
would be worst. Adding them later is another `AlterField` and nothing more.
"""
from django.db import migrations, models

import src.models.kai
import src.models.slating
import src.storage


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0016_draft_document_privacy'),
    ]

    operations = [
        migrations.AlterField(
            model_name='kaireport',
            name='attachment',
            field=models.FileField(
                blank=True, null=True,
                help_text='Optional file attachment',
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.kai.kai_report_attachment_path,
            ),
        ),
        migrations.AlterField(
            model_name='kaireportfieldresponse',
            name='file_value',
            field=models.FileField(
                blank=True, null=True,
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.kai.kai_response_file_path,
            ),
        ),
        migrations.AlterField(
            model_name='slatingapplication',
            name='gpa_screenshot',
            field=models.FileField(
                blank=True, null=True,
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.slating.slating_gpa_screenshot_path,
            ),
        ),
        migrations.AlterField(
            model_name='slatingapplicationresponse',
            name='file_value',
            field=models.FileField(
                blank=True, null=True,
                storage=src.storage.DualLocationStorage(),
                upload_to=src.models.slating.slating_response_file_path,
            ),
        ),
    ]
