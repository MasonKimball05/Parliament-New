from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class SrcConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src'

    def ready(self):
        import src.models
        import src.admin
        import src.middleware.activity_logging  # Load activity logging signals
        import src.signals  # Load security signals

        from django.conf import settings
        if not getattr(settings, 'CRYPTOGRAPHY_KEY', None):
            raise ImproperlyConfigured(
                "CRYPTOGRAPHY_KEY is not set. "
                "Run 'python3 generate_encryption_key.py' and add it to your environment."
            )