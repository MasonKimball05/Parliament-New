"""
Management command to check environment variables
"""
from django.core.management.base import BaseCommand
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Check environment variable configuration'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.SUCCESS('='*80))
        self.stdout.write(self.style.SUCCESS('ENVIRONMENT VARIABLES CHECK'))
        self.stdout.write(self.style.SUCCESS('='*80))

        # Check BASE_DIR
        self.stdout.write(f"\nBASE_DIR: {settings.BASE_DIR}")

        # Check if .env file exists
        env_path = os.path.join(settings.BASE_DIR, '.env')
        env_exists = os.path.exists(env_path)
        self.stdout.write(f".env file path: {env_path}")
        self.stdout.write(f".env file exists: {env_exists}")

        if env_exists:
            self.stdout.write(f".env file size: {os.path.getsize(env_path)} bytes")

        # Check critical environment variables
        self.stdout.write(f"\n{'Environment Variable':<30} {'Set?':<10} {'Value (masked)'}")
        self.stdout.write('-'*80)

        env_vars = [
            'DJANGO_SECRET_KEY',
            'DJANGO_DEBUG',
            'ENCRYPTION_KEY',
            'ADMIN_V2_SECRET_KEY',
            'DB_NAME',
            'DB_USER',
            'DB_HOST',
        ]

        for var in env_vars:
            value = os.getenv(var)
            is_set = 'YES' if value else 'NO'

            # Mask sensitive values
            if value:
                if 'KEY' in var or 'PASSWORD' in var:
                    masked = f"{value[:8]}...{value[-8:]}" if len(value) > 16 else "***"
                else:
                    masked = value
            else:
                masked = "NOT SET"

            self.stdout.write(f"{var:<30} {is_set:<10} {masked}")

        # Check Django settings
        self.stdout.write(f"\n{'Django Setting':<30} {'Value'}")
        self.stdout.write('-'*80)

        self.stdout.write(f"{'settings.DEBUG':<30} {settings.DEBUG}")

        # Check CRYPTOGRAPHY_KEY
        has_crypto_key = hasattr(settings, 'CRYPTOGRAPHY_KEY') and settings.CRYPTOGRAPHY_KEY
        self.stdout.write(f"{'settings.CRYPTOGRAPHY_KEY':<30} {'SET' if has_crypto_key else 'NOT SET'}")

        if has_crypto_key:
            key_len = len(settings.CRYPTOGRAPHY_KEY)
            self.stdout.write(f"{'  Key length':<30} {key_len} bytes")

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))

        # Final verdict
        if not has_crypto_key and not settings.DEBUG:
            self.stdout.write(self.style.ERROR(
                '❌ CRYPTOGRAPHY_KEY is NOT SET in production mode!\n'
                '   This will cause encryption errors.\n'
                '   Check that ENCRYPTION_KEY is in your .env file.'
            ))
        elif has_crypto_key:
            self.stdout.write(self.style.SUCCESS(
                '✓ CRYPTOGRAPHY_KEY is properly configured!'
            ))
