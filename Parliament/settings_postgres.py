"""
DEPRECATED (v3.12.0): settings were unified into Parliament/settings.py.

This shim exists so anything still pointing DJANGO_SETTINGS_MODULE at
'Parliament.settings_postgres' (old systemd units, shell scripts, muscle
memory) keeps working during the transition. Update those references to
'Parliament.settings' — this file will be removed in a future release.
"""
from .settings import *  # noqa: F401,F403
