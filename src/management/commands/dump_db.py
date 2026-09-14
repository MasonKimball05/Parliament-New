from django.core.management.base import BaseCommand
from django.apps import apps

from src.dump_redaction import redacted_field_value

class Command(BaseCommand):
    help = "Dump all data from every model in the database"

    def handle(self, *args, **kwargs):
        # v3.29.35 — this loop used to print every field of every row
        # straight from the ORM, with no filtering at all: Kai case
        # content, Slating interview notes, API tokens, WebAuthn
        # credentials, password hashes, everything. See
        # `src/dump_redaction.py` for what's redacted and why — this
        # command just applies it per field instead of printing `value`
        # directly.
        self.stdout.write("🧪 Dumping all model data from the database...\n")
        for model in apps.get_models():
            model_name = model.__name__
            self.stdout.write(f"📦 Model: {model_name}")
            objects = model.objects.all()
            if not objects:
                self.stdout.write("  (No records found)")
                continue
            for obj in objects:
                for field in obj._meta.fields:
                    value = redacted_field_value(obj, field)
                    self.stdout.write(f"  {field.name}: {value}")
                self.stdout.write("-" * 30)