from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from src.models import Attendance

class Command(BaseCommand):
    help = 'Clears attendance records older than 3 hours'

    def handle(self, *args, **kwargs):
        cutoff = timezone.now() - timedelta(hours=3)
        deleted_count, _ = Attendance.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(f"Deleted {deleted_count} expired attendance records.")
