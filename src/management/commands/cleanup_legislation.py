from django.core.management.base import BaseCommand
from django.utils import timezone
from src.models import Legislation
import os

class Command(BaseCommand):
    help = 'Delete legislation and files older than 30 days'

    def handle(self, *args, **kwargs):
        threshold = timezone.now() - timezone.timedelta(days=30)
        old_legislation = Legislation.objects.filter(created_at__lt=threshold)

        for leg in old_legislation:
            file_path = leg.document.path
            self.stdout.write(f"Deleting: {leg.title} ({file_path})")
            if os.path.exists(file_path):
                os.remove(file_path)
            leg.delete()

        self.stdout.write(self.style.SUCCESS("Old legislation cleanup complete."))
