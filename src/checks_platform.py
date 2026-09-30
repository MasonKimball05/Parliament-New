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
from django.core.checks import Error as CheckError, Warning as CheckWarning, register


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


@register()
def chapter_config_is_valid(app_configs, **kwargs):
    """src.E001 (09-28-26) — settings.CHAPTER must be loadable at startup.

    `get_chapter()` raises ValueError for an unknown key or a founding_semester
    other than 'Fall'/'Spring'. It runs on every page render (`{% chapter %}` in
    base.html, which the 500 page also extends), so a typo such as
    CHAPTER_FOUNDING_SEMESTER=fall took down every page including the error
    page while `manage.py check` passed. This turns it into a startup error
    that `check`, `preflight` and `runserver` all report.
    """
    from src.chapter import get_chapter
    try:
        get_chapter()
    except (ValueError, TypeError) as e:
        return [CheckError(
            f'settings.CHAPTER is invalid, so every page (including the 500 page) would fail: {e}',
            hint='Fix the CHAPTER_* values in .env (see .env.example). '
                 "founding_semester must be exactly 'Fall' or 'Spring'.",
            id='src.E001',
        )]
    # 09-29-26 (slice 3e) — the chapter's reference_documents.json is read on
    # every C&B viewer and reference-PDF request, and a bad one 500s ALL of
    # them (the fraternity PDFs too). Same failure class, same check.
    from src.chapter_content import ChapterContentError, load_reference_documents
    try:
        load_reference_documents()
    except ChapterContentError as e:
        return [CheckError(
            f'The chapter content is invalid, so the C&B page and every reference-PDF page would fail: {e}',
            hint='Fix <CHAPTER_CONTENT_DIR>/reference_documents.json (see src/chapter_content.py).',
            id='src.E001',
        )]
    return []


@register()
def chapter_structure(app_configs, **kwargs):
    """src.W006 (09-29-26) — the roles/committees the code depends on exist.

    See src/chapter_structure.py. A warning, not an error: a chapter mid-setup
    runs `check` before it has seeded anything, and an officer changeover can
    leave a flag briefly unset. Schema errors are left to src.W002.
    """
    from django.db.utils import DatabaseError
    from src.chapter_structure import structure_problems
    try:
        problems = structure_problems()
    except DatabaseError:
        return []
    if not problems:
        return []
    return [CheckWarning(
        'Roles/committees the code depends on are missing or ambiguous:\n  - ' + '\n  - '.join(problems),
        hint='Renaming roles and committees is fine; their codes and flags are what the code uses. '
             'Create missing defaults with `manage.py restore_committees_and_roles` (matches by code; --dry-run first), '
             'and set committee flags in the admin (exactly one committee per flag).',
        id='src.W006',
    )]


@register()
def chapter_row_matches_settings(app_configs, **kwargs):
    """src.W007 (multi-chapter 4a, 09-29-26) — the Chapter table matches settings.CHAPTER.

    In step 4a get_chapter() still reads settings; the table is what step 4b
    will switch to. If the two drift apart now, 4b would silently change the
    site's name, domain or crest on the day it ships.
    """
    from django.db.utils import DatabaseError
    try:
        from src.models import Chapter
        if not Chapter.objects.exists():
            return []   # fresh DB before migrate, or a DB migrate hasn't seeded
        row = Chapter.objects.default()
        if row is None:
            problem = 'there are Chapter rows but none is marked is_default'
        else:
            drift = row.drift_from_settings()
            if not drift:
                return []
            problem = 'the default chapter differs from settings.CHAPTER in: ' + ', '.join(sorted(drift))
    except (DatabaseError, ValueError):
        return []   # schema errors are src.W002's; a bad settings.CHAPTER is src.E001's
    return [CheckWarning(
        f'Chapter table out of sync: {problem}.',
        hint='Run `manage.py sync_chapter_from_settings` (use --dry-run to see the differences).',
        id='src.W007',
    )]
