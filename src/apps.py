from django.apps import AppConfig


class SrcConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src'

def ready(self):
    import src.models
    import src.signals.admin_logging
    import src.admin