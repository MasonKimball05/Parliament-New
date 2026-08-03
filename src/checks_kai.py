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

    # ⚠️ v3.18.3 — THE MISSING-TABLE CASE IS REPORTED, NOT SWALLOWED.
    #
    # This used to wrap everything below in `except Exception: return []`, on
    # the reasonable grounds that a deploy guard which crashes the deploy it
    # guards is worse than no guard. The effect on 08-02-26 was that v3.18.2
    # shipped to prod without `migrate` being run, `manage.py check` reported
    # "no issues", and the admin dashboard then 500'd with
    # `relation "src_kaibreakglassgrant" does not exist` — because
    # `_get_kai_access` consults the break-glass table on every Kai permission
    # resolution, and `redact_kai_logs` calls it from the dashboard's activity
    # panel.
    #
    # The check was silent about a schema it never queried, which is the same
    # failure it exists to prevent one level up. So: an unapplied migration now
    # reports as `src.W002`, and only genuinely unexpected errors stay quiet.
    from django.db.utils import DatabaseError, OperationalError, ProgrammingError

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
    except (ProgrammingError, OperationalError) as exc:
        # A missing relation is the signature of "code deployed, migrate not
        # run". Warn rather than error: a fresh clone legitimately runs `check`
        # before its first `migrate`, and failing there would be obstructive.
        return [
            CheckWarning(
                f'Kai tables are not queryable — the schema looks out of date '
                f'with the code ({exc.__class__.__name__}).',
                hint=(
                    'If this is a deployed environment, `python manage.py '
                    'migrate` has probably not been run for the release you '
                    'just shipped. v3.18.2 added migration 0013 '
                    '(KaiBreakGlassGrant), and `_get_kai_access` consults that '
                    'table on every Kai permission resolution — including from '
                    "the admin dashboard's activity panel, so a missing "
                    'migration 500s a page you would go to for diagnosis. '
                    'On a fresh clone before the first migrate, ignore this.'
                ),
                id='src.W002',
            )
        ]
    except DatabaseError:
        # Anything else DB-shaped: stay quiet rather than block a deploy on a
        # guard's own failure.
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
