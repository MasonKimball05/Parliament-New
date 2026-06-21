from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


# Committee code → field name for flags that must be auto-set after migration.
# Add entries here whenever a new special-committee flag is introduced.
COMMITTEE_FLAG_DEFAULTS = {
    'EXEC': 'is_exec_board',
    'KAI': 'is_kai_committee',
    'SLATING': 'is_slating_committee',
    'CHAPTER': 'is_chapter_committee',
    'RECRUIT': 'is_recruitment_committee',
}


def _set_committee_flags(sender, **kwargs):
    """
    Post-migrate signal: ensure known committee flags are set correctly.
    Runs after every `manage.py migrate` — idempotent and safe to re-run.
    """
    try:
        from src.models import Committee
        for code, field in COMMITTEE_FLAG_DEFAULTS.items():
            try:
                c = Committee.objects.get(code=code)
                if not getattr(c, field):
                    setattr(c, field, True)
                    c.save(update_fields=[field])
            except Committee.DoesNotExist:
                pass
    except Exception:
        pass  # DB may not be ready yet (e.g. first migrate on a fresh DB)


class SrcConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src'

    def ready(self):
        import src.models
        import src.admin
        import src.middleware.activity_logging  # Load activity logging signals
        import src.signals  # Load security signals

        from django.db.models.signals import post_migrate
        post_migrate.connect(_set_committee_flags, sender=self)

        from django.conf import settings
        if not getattr(settings, 'CRYPTOGRAPHY_KEY', None):
            raise ImproperlyConfigured(
                "CRYPTOGRAPHY_KEY is not set. "
                "Run 'python3 generate_encryption_key.py' and add it to your environment."
            )