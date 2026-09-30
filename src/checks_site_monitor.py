"""
src.W005 (v3.35.3) — SITE_MONITOR_TOKEN is set but too short to be used.

`src.site_monitor.configured_token()` ignores a token under 32 characters, so a
short one silently does nothing: the monitor gets banned again and nobody is
told why. Say so at `manage.py check` / `preflight` instead.
"""
from django.conf import settings
from django.core.checks import Warning as CheckWarning, register


@register()
def site_monitor_token_length(app_configs, **kwargs):
    from src.site_monitor import MIN_TOKEN_LENGTH
    token = getattr(settings, 'SITE_MONITOR_TOKEN', '') or ''
    if token and len(token) < MIN_TOKEN_LENGTH:
        return [CheckWarning(
            f'SITE_MONITOR_TOKEN is {len(token)} characters; it must be at least '
            f'{MIN_TOKEN_LENGTH}, so it is being IGNORED and the site monitor will be '
            f'banned by the honeypot like any scanner.',
            hint='python -c "import secrets; print(secrets.token_urlsafe(32))" and set it in '
                 '.env and in the monitor\'s GitHub Actions secret.',
            id='src.W005',
        )]
    return []
