"""
Historical `upload_to` callables for migration `0029_kai_accommodations`
ONLY — kept as plain functions, not restored as a full module.

v3.28.8 built this feature under the wrong name ("accommodation" instead
of "commendation" — Mason's own wording mistake, corrected same-day) and
migration `0029_kai_accommodations` had already applied against a real
database before the correction happened in this working tree. Renumbering
or rewriting an already-applied migration is not safe — Django resolves a
migration's `upload_to=` callable by import path, frozen at the moment
`makemigrations` wrote it, so `0029_kai_accommodations.py` will forever
try to `import src.models.kai_accommodations` and read
`.kai_accommodation_attachment_path` / `.kai_accommodation_response_file_path`
off it, no matter what the models look like today.

The actual `KaiAccommodationRequest` model class is gone — renamed to
`KaiCommendation` (see `src/models/kai_commendations.py`) by migration
`0030_rename_kai_accommodation_to_commendation`, which runs immediately
after this one. Restoring the class here would re-register a second,
dead model in the app registry the moment this module is imported (which
migration 0029 does, unconditionally) — so only the two functions survive.
Do not add a model class back to this file.
"""
from src.storage import uuid_upload_path


def kai_accommodation_attachment_path(instance, filename):
    """`kai_accommodations/<uuid>.<ext>` — see `uuid_upload_path` in src/storage.py."""
    return uuid_upload_path('kai_accommodations')(instance, filename)


def kai_accommodation_response_file_path(instance, filename):
    """`kai_accommodations/custom_fields/<uuid>.<ext>` — see `uuid_upload_path`."""
    return uuid_upload_path('kai_accommodations/custom_fields')(instance, filename)
