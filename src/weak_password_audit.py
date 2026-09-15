"""
Weekly weak-password audit — detects accounts whose CURRENT password matches
a known-weak value, as opposed to src.validators.KnownWeakPasswordValidator,
which only stops a NEW weak password from being set in the first place.

Why this needs to exist separately from the set-time validator
----------------------------------------------------------------
The validator only runs when a user chooses their own password through a
form (password reset, change password, forced password change). Two things
in this codebase set a password WITHOUT going through it, by design:

  - `manage.py reset_all_passwords` / `manage.py reset_user_password` set
    active members to a predictable `[first_initial][lastname][user_id]`
    password directly via `user.set_password()` (e.g. "mkimball73"), then
    set `force_password_change=True` / `has_default_password=True` so the
    member is prompted to change it on next login. If that member doesn't
    log in for a while — or logs in on a device where the prompt is somehow
    missed — the account sits on a genuinely guessable password (anyone who
    knows a member's name and roster ID can compute it) for as long as that
    gap lasts.
  - Any account whose password was set before KnownWeakPasswordValidator
    existed is grandfathered in — the validator only ever sees a password
    at the moment someone tries to set it.

Both are cases where the account's password is bad RIGHT NOW and nothing
would notice on its own. Since passwords are hashed, the only way to check
an existing one against a candidate is `user.check_password(candidate)` —
this module does that, deliberately narrowly, against active accounts only.

Performance note — please read before widening the candidate list
-------------------------------------------------------------------
`check_password()` runs the real configured password hasher (PBKDF2 by
default), which is intentionally slow — that's the entire point of a
password hasher. Every (user, candidate) pair costs real CPU time, so this
audit is O(active users × candidates checked), and that cost is paid on a
worker, weekly. Two things keep it bounded:

  1. `COMMON_WEAK_PASSWORDS` (src/validators.py) is short on purpose — see
     the comment there. Resist the urge to import a 10,000-line breach list
     here; that turns a few-second weekly task into a multi-hour one.
  2. A user with an OPEN weak_password LoginAlert from a previous run is
     skipped entirely on subsequent runs (see `_already_flagged`) — once
     flagged, they drop out of the check pool until an officer resolves the
     alert (presumably because the password was rotated). This means the
     steady-state cost shrinks over time rather than re-paying the full
     check for every active member every single week forever.
"""
import logging

from django.utils import timezone

from src.validators import COMMON_WEAK_PASSWORDS

logger = logging.getLogger(__name__)


def _default_reset_password_guess(user):
    """
    Mirrors the exact password format `manage.py reset_all_passwords` and
    `manage.py reset_user_password` generate: first letter of first name +
    last name + user_id, all lowercase (e.g. Mason Kimball, ID 73 ->
    "mkimball73"). Only meaningful to try against accounts that actually
    went through one of those commands, which is why the caller only adds
    this candidate when `has_default_password` is True — trying it against
    everyone else would just be extra hashing cost for a guess that can't
    possibly be right (their password was never set this way).

    Returns None if the name doesn't parse (matches reset_all_passwords.py's
    own fallback condition — that command falls back to a random string in
    that case rather than a guessable one, so there's nothing to check).
    """
    if not user.name or not user.name.strip():
        return None
    name_parts = user.name.strip().split()
    if len(name_parts) < 1:
        return None
    first_initial = name_parts[0][0].lower()
    last_name = name_parts[-1].lower().replace('.', '').replace(' ', '')
    return f"{first_initial}{last_name}{user.user_id}"


def _candidates_for(user):
    """Build the bounded per-user candidate list described in the module docstring."""
    candidates = list(COMMON_WEAK_PASSWORDS)
    if getattr(user, 'has_default_password', False):
        guess = _default_reset_password_guess(user)
        if guess:
            candidates.append(guess)
    return candidates


def _already_flagged_user_ids(alert_model):
    return set(
        alert_model.objects.filter(
            alert_type='weak_password',
            status__in=['new', 'investigating'],
        ).values_list('user_id', flat=True)
    )


def audit_active_users_for_weak_passwords(dry_run=False):
    """
    Check every active (is_active=True) user's CURRENT password against the
    known-weak candidate list and file a LoginAlert for any match.

    Scoped to `is_active=True` rather than `member_status='Active'` —
    member_status is a chapter-role concept (Active/Inactive/Alumni); any
    account that can currently authenticate is a live attack surface
    regardless of that role, so an Alumni account with login enabled and a
    weak password is exactly as worth catching as a current member's.

    Does NOT record which candidate matched anywhere (not in the LoginAlert,
    not in logs) — the finding is "this account's password is known-weak",
    and nothing downstream needs the literal guessed string. Contrast with
    the admin-initiated force-password-reset flow (src/view/admin_v2.py),
    which does store the new plaintext password in LoginAlert.resolution_notes
    — that's a deliberate, different case: an officer generated that password
    specifically to relay it to the member, immediately, once. Nothing here
    needs the same treatment, so it doesn't get it.

    Returns a summary dict: {'checked', 'flagged', 'already_flagged_skipped'}.
    """
    from src.models import ParliamentUser, LoginAlert

    summary = {'checked': 0, 'flagged': 0, 'already_flagged_skipped': 0}

    already_flagged = _already_flagged_user_ids(LoginAlert)

    users = ParliamentUser.objects.filter(is_active=True)

    for user in users:
        if user.pk in already_flagged:
            summary['already_flagged_skipped'] += 1
            continue

        summary['checked'] += 1

        try:
            matched = False
            for candidate in _candidates_for(user):
                if user.check_password(candidate):
                    matched = True
                    break
        except Exception as exc:
            # Fail per-user, not for the whole run — one account with a
            # corrupt hash or a hasher error shouldn't stop everyone else
            # from being checked. Matches the fail-safe style used
            # throughout src/tasks/ (try/except per unit of work, logged).
            logger.error(f"[weak_password_audit] check failed for user {user.pk}: {exc}")
            continue

        if not matched:
            continue

        summary['flagged'] += 1
        if dry_run:
            continue

        LoginAlert.objects.create(
            user=user,
            login_history=None,
            alert_type='weak_password',
            severity='high',
            status='new',
            title=f'Weak password detected — {user.name}',
            description=(
                f"The weekly password audit found that {user.name}'s current "
                f"password matches a known-weak value (either a commonly "
                f"used weak password, or — for accounts still marked as "
                f"having a system-assigned default password — the "
                f"predictable [initial][lastname][id] reset format). "
                f"Recommend resetting this account's password "
                f"(manage.py reset_user_password {user.pk}) and asking the "
                f"member to set a new one on next login."
            ),
        )

    logger.info(
        f"[weak_password_audit] checked={summary['checked']} "
        f"flagged={summary['flagged']} "
        f"already_flagged_skipped={summary['already_flagged_skipped']} "
        f"dry_run={dry_run} at {timezone.now().isoformat()}"
    )
    return summary
