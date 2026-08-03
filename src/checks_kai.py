"""
Deploy-time guard: can anybody actually reach the Kai module?

⚠️ v3.18.2 — WHY THIS CHECK EXISTS.

Before v3.18.2, `_get_kai_access()` opened with `if user.is_admin or ...`, so
every site admin had full Kai access — every permission including both party
identities — without holding a `KaiMemberPermission` row. v3.18.2 removed that
(the standing v3.16.2 rule: *being an admin is an operational role, not a grant
of judicial access*), which is right, and which has one sharp operational edge:

**an admin who was reaching Kai through `is_admin` loses access the moment this
ships, silently, with a "you do not have permission" redirect.**

That is exactly the failure mode this codebase has been bitten by twice —
v3.16.2's calendar Subscribe button (invisible because a flag was never
seeded: no error, no log, no failing test) and the three `/guide/` routes that
500'd for a month. A permission change that empties a page is indistinguishable
from an empty page.

So: `manage.py check` — which is already a deploy gate — now says so out loud
when the Kai committee has nobody who can open it. It is a WARNING, not an
ERROR, because a chapter between officer terms may legitimately be in that
state for a day, and a hard failure would block an unrelated deploy.

If you see `src.W001`, the fix is one of:

  * add the incoming Kai chair(s) to the committee's `chairs` (the normal path
    — chairs get full access), or
  * grant a `KaiMemberPermission` row in the app, or
  * `manage.py kai_break_glass grant --user <id> --reason "..."` for temporary
    access while you sort the above out.
"""

from django.core.checks import Warning as CheckWarning, register


@register()
def kai_has_a_reachable_operator(app_configs, **kwargs):
    """WARN if the Kai committee exists but nobody can open it."""
    try:
        from src.models import Committee
        from src.models.kai import KaiMemberPermission
    except Exception:
        return []

    try:
        committee = Committee.objects.filter(is_kai_committee=True).first()
        if committee is None:
            # No Kai committee at all is a different situation (a fresh DB, or
            # a chapter that has not seeded one) and not this check's business.
            return []

        if committee.chairs.exists():
            return []
        if KaiMemberPermission.objects.filter(committee=committee).exists():
            return []
    except Exception:
        # DB not ready — a fresh clone running `check` before `migrate`. A
        # deploy guard that crashes the deploy it guards is worse than absent.
        return []

    return [
        CheckWarning(
            'The Kai committee has no chairs and no KaiMemberPermission rows, '
            'so nobody can open the Kai module.',
            hint=(
                'Since v3.18.2 `is_admin` alone does not grant Kai access '
                '(admin is an operational role, not a judicial one). Add the '
                'Kai chair(s) to the committee, or grant a KaiMemberPermission '
                'row in the app. For temporary access: '
                'manage.py kai_break_glass grant --user <id> --reason "..."'
            ),
            id='src.W001',
        )
    ]
