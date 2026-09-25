"""
src.W004 — another chapter's deployment is still pinned to the default owner id (09-25-26).

PLATFORM_OWNER_USER_ID defaults to '73' because that is Mason's id in the
original chapter's database. In any other chapter's database '73' is whoever
happens to hold it, and that person would get the bug tracker, the feedback
board's admin side and protection from the admin sync. So: if this deployment
has its own CHAPTER_DOMAIN but never set the owner id explicitly (and set no
email second factor), warn. Fix: set PLATFORM_OWNER_USER_ID (empty = nobody)
or PLATFORM_OWNER_EMAIL.
"""
from django.conf import settings
from django.core.checks import Warning as CheckWarning, register


@register()
def platform_owner_pin(app_configs, **kwargs):
    if (not getattr(settings, 'CHAPTER_IS_DEFAULT', True)
            and getattr(settings, 'PLATFORM_OWNER_USER_ID_IS_DEFAULT', False)
            and getattr(settings, 'PLATFORM_OWNER_USER_ID', '')
            and not getattr(settings, 'PLATFORM_OWNER_EMAIL', '')):
        return [CheckWarning(
            f"This deployment ({settings.CHAPTER_DOMAIN}) has its own chapter but still pins "
            f"the platform owner to the default user_id '{settings.PLATFORM_OWNER_USER_ID}'. "
            f"In this database that id belongs to someone else.",
            hint='Set PLATFORM_OWNER_USER_ID in .env (empty to pin nobody), or set '
                 'PLATFORM_OWNER_EMAIL so the email must match too.',
            id='src.W004',
        )]
    return []
