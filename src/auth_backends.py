"""
Authentication backend that loads `request.user` without the profile columns.

THE PROBLEM
-----------
Every authenticated request loads the session user — that part is unavoidable,
it is how `request.user` exists at all, and it is a single indexed primary-key
lookup. What is avoidable is its *width*.

`ParliamentUser` is a wide table (~43 columns) because it carries the whole
member profile: a bio, four JSON lists, six social handles, house assignment,
initiation chapters. None of that appears in `base.html`, the nav, or on any
ordinary page — it is used by `profile`, `directory`, `house_map`, the chat
member card, and the admin-v2 profile editor. Yet it was being read on every
request, for every user, including the JSON columns.

THE FIX
-------
Defer the profile-only columns when loading the session user. Ordinary pages
stop reading them entirely. The handful of pages that genuinely need the
logged-in user's own profile pay one extra query when they touch a deferred
field — which is the right place for that cost, and dev mode's object inspector
labels it explicitly ("DEFERRED — reading this fires a query").

WHAT IS DELIBERATELY NOT DEFERRED
---------------------------------
* `onboarding_data` — `components/onboarding_checklist.html` is included on both
  home layouts, so deferring it would add a query to the most-visited page.
* `profile_picture` — the nav avatar reads it on every page.
* Anything used for authorization or identity (`member_type`, `is_admin`,
  `member_status`, `name`, `preferred_name`, `username`, `email`). Deferring an
  authorization field would turn every permission check into a query and is a
  good way to create a subtle security bug.

If you add a profile field, add it here too. If you start using one of these on
a common page, remove it here — otherwise you have traded bytes for a query on
that page.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from src.models.users import MEMBER_PROFILE_FIELDS


class DeferredProfileModelBackend(ModelBackend):
    """ModelBackend, but `get_user` skips the profile-only columns."""

    #: Columns not read on ordinary pages. See the module docstring before editing.
    #:
    #: v3.17.3: derived from MEMBER_PROFILE_FIELDS rather than retyped. The two
    #: lists were maintained by hand and had already drifted — this one listed
    #: `initiation_chapters` twice — and the reasoning for each difference lived
    #: in a docstring rather than in the code, which is a poor place for a rule
    #: that a `defer()` typo turns into a site-wide login outage (Django raises
    #: FieldError on an unknown defer name, and this runs on every authenticated
    #: request). `test_dev_mode.DeferredProfileFieldTests` now asserts every name
    #: resolves to a real field.
    #:
    #: The two documented differences from MEMBER_PROFILE_FIELDS:
    #:
    #: * `big_brother` is deferred HERE but is not a "profile column" for the
    #:   purposes of a joined member display, so it isn't in the shared list.
    #: * `onboarding_data` is in the shared list but must NOT be deferred here —
    #:   components/onboarding_checklist.html is included on both home layouts,
    #:   so deferring it adds a query to the most-visited page.
    DEFERRED_FIELDS = tuple(
        dict.fromkeys(  # order-preserving de-duplication
            [f for f in MEMBER_PROFILE_FIELDS if f != 'onboarding_data']
            + ['big_brother']
        )
    )

    def get_user(self, user_id):
        user_model = get_user_model()
        try:
            user = (
                user_model._default_manager
                .defer(*self.DEFERRED_FIELDS)
                .get(pk=user_id)
            )
        except user_model.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None


#: Dotted path to the backend above.
#:
#: Several places log a user in explicitly — passkey login, 2FA recovery,
#: login-as, the admin user switcher — and each hardcoded
#: 'django.contrib.auth.backends.ModelBackend'. Django stores that string in the
#: session and, on the next request, logs the user out if it is no longer listed
#: in AUTHENTICATION_BACKENDS. Import this constant instead of retyping a path.
AUTH_BACKEND_PATH = 'src.auth_backends.DeferredProfileModelBackend'
