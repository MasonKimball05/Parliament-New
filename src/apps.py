from django.apps import AppConfig


class SrcConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src'

    def ready(self):
        import src.models
        import src.admin
        import src.middleware.activity_logging  # Load activity logging signals
        import src.signals  # Load security signals