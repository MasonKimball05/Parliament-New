"""
Admin impersonation — one place that knows what "logged in as" means.

⚠️ WHY THIS MODULE EXISTS (v3.21.3). The impersonation session key was read by
name in three places: the 2FA middleware, a context processor, and the view that
sets it. Each read is a small decision about what impersonation *bypasses*, and
spreading those decisions across files is how one of them gets forgotten —
which is exactly what happened. 2FA was exempted; the forced-password-change
middleware was not, so an admin logging in as a user with
`force_password_change` set was bounced to a change-password screen for an
account he does not own the password to. He could not proceed and could not
help.

CLAUDE.md records this shape nine times over: *a rule stated correctly, a helper
written to enforce it, then one call site left outside the helper.* So the check
is a function, the list of what it covers is written down here, and
`src/test_impersonation_bypasses.py` fails the build if any module reads the raw
session key again.

## What impersonation bypasses, and why

**Account-setup interstitials — bypassed.** These exist to make a *user* finish
setting up his own account, and an impersonating admin can neither complete them
honestly nor get past them:

* **2FA enforcement** — the admin does not have the user's authenticator.
* **Forced password change** — the admin does not know the user's password, and
  setting one on his behalf would lock the user out of his own account and hand
  the admin a working credential. Both outcomes are worse than the screen it
  replaces.

Onboarding is not in this list because it is only forced at login
(`login_view`), and impersonation calls `login()` directly rather than going
through that view — so it never blocks an impersonated session.

**Restrictions on the account or the site — NOT bypassed, deliberately:**

* **quarantine** (`QuarantineEnforcementMiddleware`),
* **emergency lockdown** (`EmergencyLockdownMiddleware`),
* **maintenance mode** (`MaintenanceModeMiddleware`).

Those three are not setup steps a user has failed to finish; they are decisions
somebody made *about* an account or the whole site, usually in response to a
problem. "I am an admin" is not a reason to walk through a quarantine — if an
admin wants to use a quarantined account he should lift the quarantine, which is
a deliberate, logged act. **A bypass that also disables the controls you reach
for in an incident is a bypass that works against you exactly when it matters.**

If that trade is ever revisited, revisit it here, in the docstring, and add the
middleware to the list below — do not scatter another session-key read.
"""

#: Set by `login_as_view`. Read only through the helpers in this module.
SESSION_ORIGINAL_ID = '_impersonating_original_user_id'
SESSION_ORIGINAL_NAME = '_impersonating_original_user_name'


#: v4.4.15 (pentest attack-surface item 3) — who may START impersonating.
#: The same single-account pin as `bug_admin_required` (src/decorators.py) and
#: `_is_owner_session` (src/middleware/security.py): deliberately an id, not a
#: role an admin could hand out. On `multi-chapter` this becomes
#: `src.permissions.is_platform_owner`; swap the body when main is merged in.
PLATFORM_OWNER_USER_ID = '73'


def may_impersonate(actor, target):
    """
    True only for the platform owner, never toward their own account.

    Before v4.4.15 any `is_admin` could sign in as any member, including other
    admins and the owner, from a plain GET link. Being an admin is an
    operational role (CLAUDE.md's confidentiality boundary); signing in as
    someone else reaches everything that person can see, Kai and ballots
    included, so it belongs to the one account that already can.
    """
    return bool(
        actor is not None and getattr(actor, 'is_authenticated', False)
        and getattr(actor, 'is_admin', False)
        and str(getattr(actor, 'user_id', '')) == PLATFORM_OWNER_USER_ID
        and target is not None and target.pk != actor.pk
    )


def is_impersonating(request):
    """
    True when this request is an admin acting as another user.

    Tolerant of a missing or unusual session, because it is called from
    middleware that runs on every request including ones where the session
    machinery has not produced a normal object.
    """
    session = getattr(request, 'session', None)
    if session is None:
        return False
    try:
        return bool(session.get(SESSION_ORIGINAL_ID))
    except (AttributeError, TypeError):
        return False


def original_user_id(request):
    """The impersonating admin's user id, or None."""
    session = getattr(request, 'session', None)
    if session is None:
        return None
    try:
        return session.get(SESSION_ORIGINAL_ID)
    except (AttributeError, TypeError):
        return None


def original_user_name(request, default='Admin'):
    """The impersonating admin's display name, or `default`."""
    session = getattr(request, 'session', None)
    if session is None:
        return default
    try:
        return session.get(SESSION_ORIGINAL_NAME, default)
    except (AttributeError, TypeError):
        return default
