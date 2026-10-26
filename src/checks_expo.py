"""
src.W020 (mobile plan, Phase 1) — EXPO_ACCESS_TOKEN is empty.

With Enhanced Push Security on for the Expo project, Expo refuses push sends
that don't carry the project's access token, so a leaked
``ExponentPushToken[...]`` can't be used to push to a member's phone. It must
be on, which means the token must be set, or every app push fails. Warn in
production always, and anywhere once a device has registered a push token.
"""
from django.conf import settings
from django.core.checks import Warning as CheckWarning, register


def _push_devices_exist():
    try:
        from src.models.mobile import MobileDevice
        return (MobileDevice.objects.filter(revoked_at__isnull=True)
                .exclude(push_token='').exists())
    except Exception:
        return False  # no table yet (fresh clone, pre-migrate)


def _is_production():
    # The test runner forces DEBUG off, so DEBUG alone would make every test
    # run "production"; test runs are recognisable by eager Celery.
    return not settings.DEBUG and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)


@register()
def expo_access_token_set(app_configs, **kwargs):
    if getattr(settings, 'EXPO_ACCESS_TOKEN', ''):
        return []
    if _is_production() or _push_devices_exist():
        return [CheckWarning(
            'EXPO_ACCESS_TOKEN is not set: mobile app push notifications will be '
            'refused by Expo once Enhanced Push Security is on (and it must be on).',
            hint='Expo dashboard → project → Credentials → enable Enhanced Push Security, '
                 'create an access token, and set EXPO_ACCESS_TOKEN in .env.',
            id='src.W020',
        )]
    return []
