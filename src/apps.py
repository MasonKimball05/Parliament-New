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
    'EDUCATION': 'is_education_committee',
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
        # v3.18.2 — deploy guard: warns (src.W001) when nobody can reach the
        # Kai module, which is the silent failure mode of removing the
        # `is_admin` shortcut. See src/checks_kai.py.
        import src.checks_kai  # noqa: F401  (registers a system check)
        # v3.19.7 — deploy guard: warns (src.W003) when a changelog that git
        # says is committed still claims it is not, or has no DEPLOYED.md row.
        # Those two lines record facts that do not exist until after the commit,
        # so they are always written stale — five releases running. See
        # src/checks_ledger.py, and note it says nothing about DEPLOYMENT.
        import src.checks_ledger  # noqa: F401  (registers a system check)

        from django.db.models.signals import post_migrate
        post_migrate.connect(_set_committee_flags, sender=self)

        from django.conf import settings
        if not getattr(settings, 'CRYPTOGRAPHY_KEY', None):
            raise ImproperlyConfigured(
                "CRYPTOGRAPHY_KEY is not set. "
                "Run 'python3 generate_encryption_key.py' and add it to your environment."
            )