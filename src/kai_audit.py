"""
Kai confidentiality for the site-wide audit log (`ActivityLog`).

⚠️ v3.18.2 — WHY THIS MODULE EXISTS. READ THIS BEFORE CHANGING ANYTHING IN IT.

`ActivityLog` is the **eleventh** surface that emits Kai party identities, and
it is the first one no enumeration could have caught. Found 08-02-26.

Four releases in a row audited Kai confidentiality — v3.16.2, v3.16.3, v3.17.7,
v3.18.1 — and every one of them enumerated *templates and views*. This model is
none of those things:

  * it is not a Kai model and does not live in `src/models/kai.py`;
  * it is not rendered by any `templates/kai/` file;
  * it has no `submitted_by` or `targeted_to` field to redact.

It just happens to store both identities in a `TextField` called `description`,
plus a third copy in the row's own `user` FK. `docs/CONFIDENTIALITY_MATRIX.md`
even had a cell pointing straight at this page — `KaiReport.case_number` →
*Activity* → "✅ in ActivityLog descriptions, officer-only" — so someone looked
at it, correctly decided an officer may see a case *number*, and did not look
at the words on either side of the number.

**What was exposed.** Every officer and chair in the chapter (`@officer_required`
admits officers, all chairs and admins, and consults no `KaiMemberPermission`
anywhere) could read, at `/activity-logs/`:

  * `"<Name> submitted Kai case #12"` — written with `user=request.user`, and
    on a submission `request.user` **is** the reporter. So the description and
    the row's own User column both name them, beside the case number.
  * `"<Name> filed an appeal on Kai case KAI-2026-007"` — `file_appeal` fetches
    with `targeted_to=user`, so **only the accused can ever write this row**.
    Its existence is an assertion of who the accused is.
  * `"<Name> recused <Names> from Kai case KAI-2026-007"` — `_sync_recusals`
    auto-recuses the accused whenever they hold a seat.

…with a one-click *Kai Committee* category chip, a CSV export carrying the same
Description column, an `/admin/` search box over `description`, and a per-member
drill-down in admin-v2 that turns it into "which cases did this person report?".

That defeats the promise CLAUDE.md names as *the* Kai guarantee — **the accused
never learns who reported them** — with less effort than the v3.18.1 search
oracle needed, because the accused in a chapter judicial case is very often an
officer themselves.

THE GENERAL LESSON, worth more than the fix
-------------------------------------------
**When you enumerate the surfaces that render a confidential field, enumerate
the MODELS that can store it first.** Prose is storage. An audit description, a
notification body, an email subject and a log line are all places a field's
value can come to rest under a different name, and none of them appear in a
grep for the field.

This is the second time the miss was a *place* rather than a *rule*:
`CalendarSubscription` escaped v3.16.0's admin coverage pass for the same
reason — it lived outside `src/models/`, so the enumeration never saw it.

WHAT THIS MODULE DOES
---------------------
Two things, and the split between them is the v3.16.3/v3.18.1 rule applied
deliberately rather than discovered again:

* **Redaction**, for surfaces where the row should still be visible — the
  officer activity log and its CSV. Officers have a legitimate need to see that
  Kai activity is happening; they have no need to know who. `redact_kai_logs`
  attaches `display_actor` / `display_description` and templates must render
  those, never the raw fields.
* **Exclusion**, for surfaces where a hit is *itself* the disclosure — the
  admin-v2 per-member drill (the filter is on the author, so the row's presence
  under a member's name is the leak) and `/admin/`'s `ActivityLogAdmin` (the
  standing v3.16.2 boundary: an admin is an operational role, not a judicial
  one). `exclude_kai_logs` is that half.

And because **a filter predicate is a join key** (v3.16.3), `audit_search_q`
removes Kai rows from the two identity-bearing search columns. Redacting the
output while still filtering on the input is exactly how v3.16.3 and v3.18.1
both went wrong; it is not going to happen a third time in the same quarter.

Legacy rows matter more than new ones
-------------------------------------
The four writers no longer interpolate names. That fixes nothing on its own —
**every row already in the database still contains them**, and those are the
rows an officer would read today. The name substitution below is the actual
fix; the writer changes just stop the problem growing. Same reasoning as
`_redact_activity_log`'s legacy handling in v3.18.1.
"""

from django.db.models import Q


#: `ActivityLog.action_category` for everything Kai. Set by `log_activity`'s
#: category map from `action_type='kai_action'`; there is no other producer.
KAI_CATEGORY = 'kai'

#: What a redacted actor reads as. Deliberately the same two words the Kai
#: templates use, so the two surfaces look like one another.
ANONYMOUS = 'Anonymous'
REDACTED = 'Redacted'


#: Attribute names for the per-viewer memo below. Private by convention; the
#: only supported way to clear them is `reset_kai_identity_cache`.
_FLAGS_ATTR = '_kai_identity_flags_memo'
_PARTY_ATTR = '_kai_party_case_ids_memo'


def reset_kai_identity_cache(viewer):
    """
    Drop the memo on `viewer`. For tests that change a viewer's Kai permissions
    and then re-render with the SAME user object — see the contract below.
    """
    for attr in (_FLAGS_ATTR, _PARTY_ATTR):
        if hasattr(viewer, attr):
            delattr(viewer, attr)


def viewer_kai_identity_flags(viewer):
    """
    `(may_see_submitter, may_see_accused)` for `viewer`, resolved once.

    ⚠️ v3.18.4 — MEMOISED ON THE VIEWER OBJECT, AND THE SCOPE IS THE POINT.

    This ran `Committee.objects.filter(is_kai_committee=True).first()` plus a
    full `_get_kai_access` (a `KaiMemberPermission` fetch, and for an admin
    without one a `KaiBreakGlassGrant` fetch on top) on **every call**, and
    every consumer in this module calls it. A searched `/activity-logs/` load
    goes through `audit_search_q` and then `redact_kai_logs`, so it paid the
    whole resolution twice; the admin-v2 member page calls `exclude_kai_logs`
    twice (list, then count) and paid it twice there.

    The memo lives on the `ParliamentUser` instance, which is request-scoped in
    every real caller — `request.user` is one object for the life of a request
    and is discarded at the end of it. **That is the cache's entire lifetime
    contract**, and it is why this is an attribute rather than a process-level
    cache keyed on pk: a break-glass grant that expires, or a permission row
    that changes, is picked up on the next request, which is the same freshness
    the un-memoised version gave (it could not observe a mid-request change
    either — nothing re-reads permissions between the search and the render).

    A caller holding a user object across permission changes — a management
    command, or a test — must call `reset_kai_identity_cache(viewer)`.

    Reads the same `_get_kai_access` every Kai surface reads, so there is one
    definition of "may this person see who reported a case" and this module
    cannot drift from the Kai module. A viewer with no Kai committee, no
    permission row, or no chapter Kai committee at all gets `(False, False)` —
    fails closed.

    ⚠️ **These flags are only the FIRST axis.** They answer "may this user read
    this field *at all*". They do not answer "is this the one case where the
    answer is no anyway" — see `viewer_party_case_ids`.
    """
    # Imported lazily: this module is imported by `admin.py` and the view
    # layer, and `src.view.kai_reports` pulls in a large slice of the app.
    from src.models import Committee
    from src.view.kai_reports import _get_kai_access

    if not getattr(viewer, 'pk', None):
        return False, False

    cached = getattr(viewer, _FLAGS_ATTR, None)
    if cached is not None:
        return cached

    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None:
        # Not memoised: a chapter with no Kai committee is a fixture state, and
        # caching `(False, False)` for it would outlive the seeding that fixes
        # it inside a single test method.
        return False, False

    access = _get_kai_access(viewer, committee)
    flags = (
        bool(access.get('can_view_submitter_identity')),
        bool(access.get('can_view_accused_identity')),
    )
    try:
        setattr(viewer, _FLAGS_ATTR, flags)
    except AttributeError:
        pass  # `__slots__` or a proxy — correctness does not depend on the memo.
    return flags


def viewer_party_case_ids(viewer):
    """
    Case pks `viewer` is the accused on — the SECOND axis, and the one this
    module shipped without.

    ⚠️ v3.18.3 — FOUND BY `test_kai_party_safe_surfaces.py` ON THE DAY THAT
    MODULE WAS WRITTEN, IN CODE ADDED EARLIER THE SAME DAY.

    v3.18.2 gated this module on `viewer_kai_identity_flags` alone. Those flags
    are *committee-level*: for a Kai reviewer holding both grants they read
    `(True, True)`, and the module therefore did no redaction and no search
    narrowing at all for that viewer. Which is correct — **except on a case
    that viewer is the accused on**, where their committee grants mean nothing
    and `_case_access` withdraws every permission.

    So a fully-permissioned reviewer who was the accused could search their own
    reporter's surname in the audit log and watch their own case's rows appear.
    **That is the v3.18.1 oracle exactly, reproduced in the module written to
    fix the v3.18.1 oracle's eleventh surface** — which is a good argument for
    the property test that caught it, and a better one for the rule below.

    `_kai_search_q` already carries this second axis as `redacted_case_ids`.
    This is the same list, from the same helper, for the same reason.

    **The rule: permission is not the only thing that redacts.** Any surface
    that gates on committee-level Kai flags must also ask whether *this
    particular row* is one the viewer is a party to.
    """
    from src.view.kai_reports import _recused_case_ids

    if not getattr(viewer, 'pk', None):
        return []

    cached = getattr(viewer, _PARTY_ATTR, None)
    if cached is not None:
        return cached

    ids = list(_recused_case_ids(viewer) or ())
    try:
        setattr(viewer, _PARTY_ATTR, ids)  # v3.18.4 — see the memo contract above.
    except AttributeError:
        pass
    return ids


def _report_ids(logs):
    """
    The `KaiReport` pks referenced by these log rows.

    Kai rows carry the case either as `object_id` (when `object_type` is
    `KaiReport`) or as `metadata['report_id']` (appeals and recusals, whose
    `object_type` names the *other* model). Both are checked; `object_id` is a
    CharField since v3.17.3, so it is coerced.
    """
    ids = set()
    for log in logs:
        if log.action_category != KAI_CATEGORY:
            continue
        raw = None
        if log.object_type == 'KaiReport':
            raw = log.object_id
        if raw is None and isinstance(log.metadata, dict):
            raw = log.metadata.get('report_id')
        if raw is None:
            continue
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def _party_index(report_ids):
    """
    `{report_pk: (submitter_id, accused_id, submitter_name, accused_name)}`.

    One query for the whole page, not one per row — the panel this replaces is
    rendered 50 rows at a time.
    """
    if not report_ids:
        return {}
    from src.models import KaiReport

    rows = KaiReport.objects.filter(pk__in=report_ids).values_list(
        'pk', 'submitted_by_id', 'targeted_to_id',
        'submitted_by__name', 'targeted_to__name',
    )
    return {
        pk: (sub_id, acc_id, sub_name or '', acc_name or '')
        for pk, sub_id, acc_id, sub_name, acc_name in rows
    }


def _log_report_id(log):
    if log.object_type == 'KaiReport':
        raw = log.object_id
    elif isinstance(log.metadata, dict):
        raw = log.metadata.get('report_id')
    else:
        raw = None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def rows_for_cases_q(case_ids):
    """
    A `Q` matching the `ActivityLog` rows that reference any of `case_ids`.

    ⚠️ v3.18.4 — THE ORM SIDE OF `_log_report_id`, AND IT MUST STAY THAT WAY.

    `_log_report_id` (Python, one row at a time) reads a row's case from
    `object_id` when `object_type` is `KaiReport`, **or** from
    `metadata['report_id']` otherwise — appeals and recusals set `object_type`
    to `KaiAppeal` / `KaiRecusal` and carry the report only in `metadata`
    (`kai_user_dashboard.file_appeal`, `kai_reports.appoint_standin`).

    `audit_search_q` used to build its own version of this that matched
    `object_id` and `object_repr` only, so the rows the redactor could resolve
    and the rows the search predicate could narrow were **different sets**.
    That is the module's own asymmetry — *output redacted, input not* — at one
    remove: it was not live, because post-v3.18.2 appeal descriptions carry no
    names and the legacy ones name the accused (who is the viewer), but the
    next writer to interpolate anything into an appeal description makes it
    live without touching `kai_audit.py` at all.

    So: one resolution rule, two consumers. If you teach `_log_report_id` a
    third place a case id can hide, teach it here in the same commit.

    `object_type` is constrained deliberately. `object_id` is a shared
    `CharField` across every model in the schema, so an unconstrained
    `object_id__in` also matched, say, an `Event` whose pk happened to equal a
    case pk — silently dropping unrelated rows from a viewer's search results.
    """
    case_ids = [c for c in (case_ids or ()) if c is not None]
    if not case_ids:
        return Q(pk__in=[])  # matches nothing, and composes cleanly with `~`
    return (
        Q(object_type='KaiReport', object_id__in=[str(c) for c in case_ids])
        | Q(metadata__report_id__in=[int(c) for c in case_ids])
    )


def redact_kai_logs(logs, viewer):
    """
    Attach `display_actor`, `display_actor_id` and `display_description` to
    every row in `logs`, redacting Kai party identities `viewer` may not see.

    Applied to ALL rows, not just Kai ones, so templates have a single code
    path and cannot render the raw field on a non-Kai row by habit and then
    inherit it into a Kai one. Non-Kai rows pass straight through.

    `logs` is materialised — pass a page's worth, not a queryset you intended
    to slice later.

    ⚠️ Templates must render `display_actor` / `display_description`. Never
    `log.user.get_display_name` or `log.description`.
    `test_no_template_renders_raw_audit_fields` fails if one does.
    """
    logs = list(logs)

    show_submitter, show_accused = viewer_kai_identity_flags(viewer)

    # v3.18.3 — the second axis. A viewer holding a committee flag still gets
    # redaction on cases they are the accused on, where that flag does not
    # apply (`_case_access` withdraws every permission for a party).
    #
    # ⚠️ v3.18.4 — THE GUARD USED TO READ `if (show_submitter and show_accused)`,
    # and that was wrong in a way worth keeping written down, because the
    # comment justifying it sounded right: *"only fetched when the flags would
    # otherwise short-circuit everything, because that is the only case where
    # it changes an answer."*
    #
    # It is not the only case. `party_cases` is consumed PER FLAG, twenty lines
    # below, as `show_X and report_id not in party_cases`. With flags
    # `(True, False)` — a reviewer who may learn who reported a case but not
    # who it is about, which is the natural grant for triaging intake — the
    # conjunction is False, `party_cases` stayed empty, and `report_id not in
    # set()` was then vacuously true on every row. So that reviewer read their
    # own reporter's name on the case they were the accused on: the exact bug
    # v3.18.3 was written to close, surviving in the branch beside it.
    #
    # **The rule: the second axis applies to each flag separately, not to their
    # conjunction.** A partially permissioned viewer is still a viewer, and a
    # case they are a party to withdraws whatever they hold. Found 08-03-26.
    party_cases = set(viewer_party_case_ids(viewer)) if (show_submitter or show_accused) else set()
    # Resolve the case index whenever ANY row might need redacting.
    parties = (
        _party_index(_report_ids(logs))
        if not (show_submitter and show_accused) or party_cases
        else {}
    )

    for log in logs:
        actor = log.user
        log.display_actor = actor.get_display_name() if actor else 'System'
        log.display_actor_id = getattr(actor, 'user_id', '') if actor else ''
        log.display_description = log.description or ''

        if log.action_category != KAI_CATEGORY:
            continue

        report_id = _log_report_id(log)

        # v3.18.3 — PER-ROW flags, and they must be per-row. On a case the
        # viewer is a party to, `_case_access` withdraws every permission, so
        # the committee-level flags do not apply to this row however generous
        # they are. (Written as locals deliberately: assigning to the outer
        # `show_submitter` / `show_accused` here would leak one row's recusal
        # onto every row after it, which over-redacts silently and would look
        # like the redaction working.)
        row_show_submitter = show_submitter and report_id not in party_cases
        row_show_accused = show_accused and report_id not in party_cases
        if row_show_submitter and row_show_accused:
            continue

        entry = parties.get(report_id)
        if entry is None:
            # Case not resolvable — deleted, or a row that never named one.
            # Fail closed on the actor: a Kai row whose case we cannot check
            # might be a submission, and a submission's author is the reporter.
            # The description is left alone because with no case in hand there
            # are no names to substitute, and blanking it would destroy audit
            # rows that never carried an identity in the first place.
            if actor is not None:
                log.display_actor = ANONYMOUS
                log.display_actor_id = ''
            continue

        submitter_id, accused_id, submitter_name, accused_name = entry

        # -- the row's author -------------------------------------------
        if actor is not None:
            if actor.pk == submitter_id and not row_show_submitter:
                log.display_actor = ANONYMOUS
                log.display_actor_id = ''
            elif actor.pk == accused_id and not row_show_accused:
                log.display_actor = REDACTED
                log.display_actor_id = ''

        # -- names interpolated into the description --------------------
        # Legacy rows only; the four writers stopped doing this in v3.18.2.
        # A plain substring swap, for the same reason `_redact_activity_log`
        # uses one: the names sit in free text with no structure to parse.
        text = log.display_description
        if text:
            if submitter_name and not row_show_submitter:
                text = text.replace(submitter_name, ANONYMOUS)
            if accused_name and not row_show_accused:
                text = text.replace(accused_name, REDACTED)
            log.display_description = text

    return logs


def exclude_kai_logs(queryset, viewer):
    """
    Drop Kai rows from `queryset` unless `viewer` may see both identities.

    For surfaces where **the row's presence is itself the disclosure**, which
    is the case whenever the surface is reached by filtering on the author:

    * `admin_v2`'s per-member activity drill — `ActivityLog.objects.filter(
      user=user)`. Redacting the actor there would be pointless; the member
      whose page it is *is* the actor, so a Kai submission row appearing on
      Zebediah's page says Zebediah reported that case no matter what the row
      renders as.
    * `/admin/`'s `ActivityLogAdmin` — the standing v3.16.2 rule. All seven Kai
      models are unregistered so an admin cannot read case material; this model
      carries the same identities in prose and stayed registered. Its
      `search_fields` include `description`, which would be an oracle even with
      the output redacted.

    This is the exclusion half of the v3.18.1 rule: *exclusion protects the
    filters for free; redaction does not.* Use it where a hit is a disclosure,
    and `redact_kai_logs` where the row should be visible but anonymous.
    """
    show_submitter, show_accused = viewer_kai_identity_flags(viewer)
    if show_submitter and show_accused:
        return queryset
    return queryset.exclude(action_category=KAI_CATEGORY)


def audit_search_q(search_query, viewer):
    """
    The activity-log search predicate, with Kai rows removed from the two
    identity-bearing columns.

    **A filter predicate is a join key** (v3.16.3, and again in v3.18.1). The
    original predicate was:

        Q(description__icontains=q) | Q(user__name__icontains=q)
        | Q(object_repr__icontains=q) | Q(ip_address__icontains=q)

    `description` is where the names were interpolated and `user__name` is the
    row's author, so with the output redacted and the input unchanged the
    search box recovers exactly what the page refuses to print — the same
    oracle v3.18.1 closed in `_kai_search_q`, on a different page.

    `object_repr` and `ip_address` stay open for Kai rows. `object_repr` is
    `Case #12` / the case number, which the confidentiality matrix already
    records as acceptable at officer level, and neither carries a name.

    Deliberately conservative: a viewer holding one identity flag but not the
    other loses Kai rows from BOTH columns rather than one. Splitting it would
    mean deciding, per row, which name a match came from — and a match on a
    description that contains both names cannot be attributed to one of them.
    Over-restricting a search box is cheap; under-restricting it is the bug.
    """
    open_columns = (
        Q(object_repr__icontains=search_query)
        | Q(ip_address__icontains=search_query)
    )
    identity_columns = (
        Q(description__icontains=search_query)
        | Q(user__name__icontains=search_query)
    )

    show_submitter, show_accused = viewer_kai_identity_flags(viewer)
    if not (show_submitter and show_accused):
        return open_columns | (identity_columns & ~Q(action_category=KAI_CATEGORY))

    # ⚠️ v3.18.3 — THE SECOND AXIS, and this module shipped without it.
    #
    # A viewer holding both committee flags reached the line above and got the
    # unnarrowed predicate — correct for every case except the ones they are
    # the accused on, where `_case_access` withdraws every permission and the
    # page redacts accordingly. So the output was redacted and the input was
    # not: **the v3.18.1 oracle, reproduced inside the module written to close
    # the v3.18.1 oracle's eleventh surface.** Caught by
    # `test_kai_party_safe_surfaces.py` the day it was written.
    #
    # Their own cases are matchable on the open columns only, exactly as
    # `_kai_search_q` does it with `redacted_case_ids`.
    party_cases = viewer_party_case_ids(viewer)
    if not party_cases:
        return open_columns | identity_columns

    # ⚠️ v3.18.4 — `rows_for_cases_q`, NOT a predicate written here.
    #
    # This used to build its own case matcher — `object_id__in` plus one
    # `object_repr=<case_number>` term per case — which resolved a row's case
    # by different rules than `_log_report_id` does eighty lines up. It missed
    # every appeal and recusal row (those carry the report in `metadata` only),
    # and its unconstrained `object_id__in` swept up unrelated non-Kai rows
    # whose pk collided. Two resolutions of the same question is the "second
    # copy" pattern this codebase keeps paying for; there is now one.
    party_rows = rows_for_cases_q(party_cases)

    return open_columns | (identity_columns & ~party_rows)
