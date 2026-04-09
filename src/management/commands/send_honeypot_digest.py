"""
Management command to send a daily honeypot activity digest email.

Summarises all honeypot hits from the last 24 hours in a single email
rather than sending one email per hit. Serious/escalated hits (coordinated
multi-honeypot attacks, POST credential probes) are still emailed immediately
by the honeypot view itself — this digest only covers routine scanner noise.

Usage:
    python manage.py send_honeypot_digest

Cron example (runs every evening at 8pm server time):
    0 20 * * * cd /path/to/project && /path/to/venv/bin/python manage.py send_honeypot_digest

Or every morning at 8am:
    0 8 * * * cd /path/to/project && /path/to/venv/bin/python manage.py send_honeypot_digest
"""

from django.core.management.base import BaseCommand
from src.security_notifications import send_honeypot_digest
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send a daily digest email summarising honeypot activity from the last 24 hours.'

    def handle(self, *args, **options):
        self.stdout.write('Sending honeypot digest...')
        sent = send_honeypot_digest()
        if sent:
            self.stdout.write(self.style.SUCCESS('Digest email sent.'))
        else:
            self.stdout.write(self.style.WARNING('No email sent (no hits in last 24h, or no email configured).'))
