"""
The roles and committees the CODE depends on (multi-chapter, slice 3e, 09-29-26).

Roles and committees are database rows a chapter can rename, add to and
reorganise (docs/MULTI_CHAPTER_PLAN.md, "Roles and committees"). What a chapter
must NOT do is delete or re-code the handful the code looks up, because every
one of those lookups degrades silently or 500s:

  * a missing role code (e.g. `VPP`) just means "nobody has it": service-hours
    officer pages quietly become admin-only;
  * special committees are found with `Committee.objects.get(<flag>=True)` in
    ~20 places, so ZERO flagged committees raises DoesNotExist and TWO raise
    MultipleObjectsReturned — a 500 on the Kai pages, the exec-board sync, etc.

`structure_problems()` lists what's wrong; `src.W006` (src/checks_platform.py)
reports it at `manage.py check` / `preflight`, and the guard test
`test_required_role_codes` fails if new code starts keying on a role code that
isn't registered below.
"""

# Role code -> what breaks without it. Every role-code lookup (by `code`,
# `roles__code`, `code__in`) in src/ must use a code listed here (or in
# SELF_HEALING_ROLE_CODES); the guard test enforces that.
REQUIRED_ROLE_CODES = {
    'President': 'exec-board chair sync and slating admin default (signals.py); landing-page contact',
    'EVP': 'exec-board chair + admin sync (signals.py)',
    'VPB': 'exec-board membership sync (signals.EXEC_ROLE_CODES)',
    'VPR': 'exec-board sync; landing-page recruitment contact',
    'VPE': 'exec-board membership sync',
    'VPP': 'service-hours officer pages (vpp_required, permissions.py, service dashboards, home)',
    'VPF': 'exec-board membership sync',
    'VPA': 'exec-board membership sync; officer transition checklist',
    'VPRM': 'exec-board membership sync',
    'CNB': 'Constitution & Bylaws editing (ParliamentUser.has_cnb_permission)',
}

# Looked up with get_or_create, so a chapter that lacks them gets them created
# on first use. Not required.
SELF_HEALING_ROLE_CODES = {'CHOIR', 'HIST'}

# Committee flag -> (expected default code, what uses it). Each must be set on
# EXACTLY ONE committee. `is_slating_committee` is deliberately absent: every
# slating period creates its own ad hoc committee with that flag.
REQUIRED_COMMITTEE_FLAGS = {
    'is_exec_board': ('EXEC', 'exec-board sync; officer home links by code'),
    'is_kai_committee': ('KAI', 'every Kai page and permission check'),
    'is_chapter_committee': ('CHAPTER', 'chapter documents; filing election results'),
    'is_recruitment_committee': ('RECRUIT', 'recruitment pages'),
    'is_education_committee': ('EDUCATION', 'education pages and quizzes'),
}

# Templates link to these by committee code (`{% url 'committee_minutes_list' 'EXEC' %}`).
REQUIRED_COMMITTEE_CODES = {'EXEC'}


def structure_problems():
    """Human-readable problems with this chapter's roles/committees; [] when fine.

    Returns [] on an empty database (no roles AND no committees): a fresh clone
    or the test database isn't a misconfigured chapter.
    """
    from src.models import Committee, Role

    role_codes = set(Role.objects.values_list('code', flat=True))
    committees = list(Committee.objects.values('code', 'name', *REQUIRED_COMMITTEE_FLAGS))
    if not role_codes and not committees:
        return []

    problems = []
    missing_roles = sorted(set(REQUIRED_ROLE_CODES) - role_codes)
    for code in missing_roles:
        problems.append(f'no role with code {code!r} (needed for: {REQUIRED_ROLE_CODES[code]})')

    for flag, (code, used_by) in REQUIRED_COMMITTEE_FLAGS.items():
        flagged = [c['name'] for c in committees if c[flag]]
        if not flagged:
            problems.append(f'no committee has {flag}=True (normally {code!r}; needed for: {used_by})')
        elif len(flagged) > 1:
            problems.append(f'{len(flagged)} committees have {flag}=True ({", ".join(sorted(flagged))}); '
                            f'lookups use .get() and will 500 — exactly one may')

    codes = {c['code'] for c in committees}
    for code in sorted(REQUIRED_COMMITTEE_CODES - codes):
        problems.append(f'no committee with code {code!r} (templates link to it by code)')
    return problems
