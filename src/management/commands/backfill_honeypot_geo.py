"""
Backfill geolocation data for existing HoneypotAccess records that don't have it yet.

Usage:
    python manage.py backfill_honeypot_geo
    python manage.py backfill_honeypot_geo --limit 100   # only process 100 records
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from src import geo_utils
from src.geo_utils import get_ip_geo
from src.models import HoneypotAccess


class Command(BaseCommand):
    help = 'Backfill geolocation data for honeypot logs missing geo info.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Max records to process (0 = all)',
        )

    def handle(self, *args, **options):
        limit = options['limit']

        # Without the database every lookup "fails", and writing that failure
        # into each record would mark it done and stop a later run from
        # filling it in.
        if geo_utils._get_reader() is None:
            raise CommandError(
                f'GeoIP database unavailable at {settings.GEOIP_CITY_DB!r}; '
                f'see DEPLOYMENT.md "IP geolocation (GeoLite2)".')

        qs = HoneypotAccess.objects.all().order_by('-accessed_at')
        # Only records without geo data already
        records = [r for r in qs if 'geo' not in r.additional_data]

        if limit:
            records = records[:limit]

        total = len(records)
        if total == 0:
            self.stdout.write(self.style.SUCCESS('All records already have geo data.'))
            return

        self.stdout.write(f'Backfilling geo for {total} records...')

        private_prefixes = ('10.', '172.', '192.168.', '127.', '::1', 'unknown')
        processed = 0
        skipped = 0
        errors = 0

        # Deduplicate IPs so each is looked up once
        seen_ips = {}

        for record in records:
            ip = record.ip_address

            if any(ip.startswith(p) for p in private_prefixes):
                skipped += 1
                continue

            if ip in seen_ips:
                geo = seen_ips[ip]
            else:
                # Local GeoLite2 lookup (src/geo_utils.py) — no network call.
                geo = get_ip_geo(ip)
                if not geo:
                    geo = {'geo_error': 'private IP or lookup failed'}
                    errors += 1
                seen_ips[ip] = geo

            record.additional_data['geo'] = geo
            record.save(update_fields=['additional_data'])
            processed += 1

            if processed % 10 == 0:
                self.stdout.write(f'  {processed}/{total} done...')

        self.stdout.write(self.style.SUCCESS(
            f'Done. Processed: {processed}, Skipped (private IPs): {skipped}, Errors: {errors}'
        ))
