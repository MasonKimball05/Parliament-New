# Load Celery app when Django starts so shared_task decorators work correctly.
from .celery import app as celery_app  # noqa: F401

__all__ = ('celery_app',)
