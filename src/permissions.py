"""
v3.19.6 — authorisation PREDICATES, as distinct from authorisation DECORATORS.

WHY THIS MODULE EXISTS
----------------------
`src/decorators.py` holds view wrappers. Each one answers a question, records
the answer through `_gate()` so dev mode's Perms panel can show it, and then
allows or refuses the request. `test_every_authz_decorator_routes_through_the_gate_helper`
enforces exactly that: **every function in `src/decorators.py` must call
`_gate`.** It is a good guard and it caught this file being born in the wrong
place.

v3.19.6 needed the same questions answered *without* a request. The eight
ownership-aware file views in `src/view/serve_private_upload.py` have to decide
"may this user read this file?" using the identical rule the host page applies —
and the alternative was a second copy of `is_officer or member_type == CHAIR` in
another module. **Two copies of an authorisation rule is one copy plus a latent
divergence, and the file is the half nobody looks at, so it is the half that
drifts.** That is the whole finding v3.19.6 exists to fix, one level down.

So the rule stays in one place and the two callers share it:

    predicate  (here)          — pure, no request, no telemetry, testable alone
    decorator  (decorators.py) — calls the predicate, records `_gate`, redirects
    file view  (serve_private_upload.py) — calls the predicate, raises Http404

`_gate` deliberately does NOT move here. It records a decision about a *request*
and these functions do not have one; putting a telemetry call in a pure
predicate would either lie about which request it belonged to or force a request
argument that most callers do not have.

⚠️ ANYTHING ADDED HERE MUST STAY PURE. If a new predicate needs the request, it
belongs in `decorators.py` as a decorator instead — and then the `_gate` guard
applies to it, which is the point.
"""
from src.constants import MemberType


def user_is_officer_or_chair(user):
    """
    True for officers, chairs and admins. Excludes advisors and pledges.

    The predicate behind `officer_required`. `is_officer` is itself a property
    that already folds in `is_admin` (see `ParliamentUser.is_officer`), so this
    is the complete rule and not a subset of it.

    ⚠️ Do NOT substitute `ParliamentUser.can_manage_events`, which is the same
    boolean expression today. It means something else — whether a member may
    create events — and the two are only equal by coincidence. Reading the
    excuse-document gate off an events permission would survive review and break
    the first time either rule moved.
    """
    return bool(user.is_officer or user.member_type == MemberType.CHAIR)


def user_is_vpp(user):
    """
    True for the Vice President of Programming, and for admins.

    The predicate behind `vpp_required`, which gates the Service Hours officer
    pages.

    ⚠️ The `is_admin` branch is part of the rule, not a shortcut around it.
    `vpp_required` has always granted admins, so a file view that did not would
    refuse an admin a document he can already read on the page it hangs off —
    and that kind of mismatch gets "fixed" by widening whichever side was
    noticed second.
    """
    if user.is_admin:
        return True
    return user.roles.filter(code__iexact='VPP').exists()
