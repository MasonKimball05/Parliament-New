"""
Apple App Review demo account(s).

App Review needs a working sign-in. The account it gets is an ordinary
``Member`` row created by ``manage.py create_app_review_account`` and named in
``settings.APP_REVIEW_USERNAMES`` (env ``APP_REVIEW_USERNAMES``, comma-separated,
default empty). No migration: the list of usernames IS the flag.

What being a review account means, and where each rule lives:

* **No second factor** — ``Enforce2FAMiddleware.user_requires_2fa`` returns
  False for it, so the app's password sign-in issues a device without
  ``mfa_setup_required``. Only while it is a plain ``Member`` with no admin flag
  (``is_2fa_exempt_review_account``): promote it and the policy applies again.
  A review account that enrols a TOTP device anyway is still asked for it.
* **No one else's contact details** — ``redact_contact`` / ``hides_contact_for``,
  applied to the directory card (web popup, chat, house map, app), the app and
  web directory lists, the directory export and the app's search rows.
* **Not counted** — ``exclude_review_accounts(qs)`` on the directory, house map,
  event attendance rosters (``Event.required_members``), the voting-attendance
  roster, service-hours rosters, slating attendance/participation, and the home
  page member count. ``src.ballots`` also refuses its ballots, so a reviewer
  pressing "Vote" never changes a chapter outcome.

Everything here is a set lookup on settings — no queries — and reads settings at
call time so ``override_settings`` works in tests.
"""
from django.conf import settings
from django.db.models import Q


def review_usernames():
    """The configured review usernames, lower-cased (a frozenset; may be empty)."""
    names = getattr(settings, 'APP_REVIEW_USERNAMES', None) or ()
    if isinstance(names, str):   # tolerate a plain string in an override
        names = names.split(',')
    return frozenset(n.strip().lower() for n in names if n and n.strip())


def is_app_review_account(user):
    """True when ``user`` is one of the configured App Review accounts."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    username = (getattr(user, 'username', '') or '').strip().lower()
    return bool(username) and username in review_usernames()


def is_2fa_exempt_review_account(user):
    """
    The 2FA exemption: a review account that is still a plain, non-admin
    ``Member``. Narrower than ``is_app_review_account`` on purpose — if anyone
    ever gives the account a role, the chapter's 2FA policy applies to it again.
    """
    return (is_app_review_account(user)
            and not getattr(user, 'is_admin', False)
            and getattr(user, 'member_type', None) == 'Member')


def hides_contact_for(viewer):
    """True when ``viewer`` must not see other members' email or phone."""
    return is_app_review_account(viewer)


#: Keys blanked by ``redact_contact`` — every name the card/list/search
#: serializers use for an address or a number.
CONTACT_KEYS = ('email', 'other_email', 'phone', 'phone_number')


def redact_contact(data, viewer, member_id=None):
    """
    Blank the contact keys of one serialized member dict in place (and return
    it) when ``viewer`` is a review account looking at someone else.
    """
    if not hides_contact_for(viewer):
        return data
    if member_id is not None and str(member_id) == str(getattr(viewer, 'pk', '')):
        return data   # their own row
    for key in CONTACT_KEYS:
        if key in data:
            data[key] = ''
    return data


def exclude_review_accounts(qs):
    """``qs`` without the review accounts (unchanged when none are configured)."""
    names = review_usernames()
    if not names:
        return qs
    # Case-insensitive, like the comparison in is_app_review_account — one
    # iexact per configured name (in practice one).
    q = Q()
    for name in names:
        q |= Q(username__iexact=name)
    return qs.exclude(q)
