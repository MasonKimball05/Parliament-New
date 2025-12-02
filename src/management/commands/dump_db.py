from django.core.management.base import BaseCommand
from django.apps import apps

class Command(BaseCommand):
    help = "Dump all data from every model in the database"

    def handle(self, *args, **kwargs):
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
                    value = getattr(obj, field.name, None)
                    self.stdout.write(f"  {field.name}: {value}")
                self.stdout.write("-" * 30)