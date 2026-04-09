"""
Backfill geolocation data for existing HoneypotAccess records that don't have it yet.

Usage:
    python manage.py backfill_honeypot_geo
    python manage.py backfill_honeypot_geo --limit 100   # only process 100 records
"""
import time
import requests
from django.core.management.base import BaseCommand
from django.core.cache import cache
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

        # Deduplicate IPs so we don't call the API more than once per IP
        seen_ips = {}

        for record in records:
            ip = record.ip_address

            if any(ip.startswith(p) for p in private_prefixes):
                skipped += 1
                continue

            if ip in seen_ips:
                geo = seen_ips[ip]
            else:
                cache_key = f'geo_{ip}'
                geo = cache.get(cache_key)
                if geo is None:
                    try:
                        resp = requests.get(
                            f'http://ip-api.com/json/{ip}',
                            params={'fields': 'status,country,countryCode,regionName,city,zip,lat,lon,isp,org,as,query'},
                            timeout=5,
                        )
                        data = resp.json()
                        if data.get('status') == 'success':
                            geo = {
                                'country': data.get('country', ''),
                                'country_code': data.get('countryCode', ''),
                                'region': data.get('regionName', ''),
                                'city': data.get('city', ''),
                                'zip': data.get('zip', ''),
                                'lat': data.get('lat'),
                                'lon': data.get('lon'),
                                'isp': data.get('isp', ''),
                                'org': data.get('org', ''),
                                'as': data.get('as', ''),
                            }
                            cache.set(cache_key, geo, 86400)
                        else:
                            geo = {'geo_error': data.get('message', 'lookup failed')}
                    except Exception as e:
                        geo = {'geo_error': str(e)}
                        errors += 1
                        self.stdout.write(self.style.WARNING(f'  Error for {ip}: {e}'))

                    # ip-api.com free tier: 45 req/min — stay well under
                    time.sleep(1.5)

                seen_ips[ip] = geo

            record.additional_data['geo'] = geo
            record.save(update_fields=['additional_data'])
            processed += 1

            if processed % 10 == 0:
                self.stdout.write(f'  {processed}/{total} done...')

        self.stdout.write(self.style.SUCCESS(
            f'Done. Processed: {processed}, Skipped (private IPs): {skipped}, Errors: {errors}'
        ))
