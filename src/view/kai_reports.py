from django.db import DatabaseError
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.tasks import send_email
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import localtime
from django.http import HttpResponse
from django.core.exceptions import ValidationError
import csv
import logging
from datetime import datetime, timedelta
from src.models import KaiReport, Committee, ParliamentUser, KaiReportActivity, KaiReportTemplate, KaiFormField, KaiReportFieldResponse, KaiClosureRequest, ActivityLog, KaiMemberPermission, KaiRecusal, KaiAppeal, KaiBreakGlassGrant
from src.forms import KaiReportForm
from src.decorators import log_function_call
from src.feature_flag_decorators import require_feature_flag
from src.utils.file_validation import validate_uploaded_file
from src.models.users import member_defer

logger = logging.getLogger('src')

#: Most rows the Kai reviewer list will render at once. v3.18.1 — the list was
#: unbounded. The badges and the aging banner come from separate aggregates, so
#: they stay true past the cap; `reports_truncated` drives the notice. Same
#: shape as `view_all_reports`, which is the reference implementation.
KAI_LIST_LIMIT = 500


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def submit_kai_report(request):
    """Allow any logged-in user to submit a Kai report"""
    # Check if KaiReport table exists
    try:
        if request.method == 'POST':
            # Validate uploaded file if provided
            if 'supporting_document' in request.FILES:
                try:
                    validate_uploaded_file(request.FILES['supporting_document'])
                except ValidationError as e:
                    messages.error(request, f'File upload error: {str(e)}')
                    # Re-initialize form with templates
                    form = KaiReportForm()
                    # Migrations are consolidated + tracked (07-05-26), so the
                    # schema-probe fallback tiers (and their discarded
                    # list(queryset) force-evaluation, which doubled the query)
                    # are gone. (v3.15.6, 07-23 report item #3.)
                    form.fields['targeted_to'].queryset = (
                        ParliamentUser.objects.filter(member_status='Active').order_by('name'))
                    templates = KaiReportTemplate.objects.filter(is_active=True)
                    return render(request, 'kai/submit_report.html', {'form': form, 'templates': templates})

            form = KaiReportForm(request.POST, request.FILES)
            if form.is_valid():
                report = form.save(commit=False)
                report.submitted_by = request.user
                report.save()

                # Save custom field responses
                custom_fields = KaiFormField.objects.filter(is_active=True, is_builtin=False)
                for field in custom_fields:
                    field_key = f'custom_field_{field.id}'
                    value = request.POST.get(field_key, '').strip()
                    file_value = request.FILES.get(field_key)

                    # ⚠️ v3.19.7 — a custom-field file was assigned straight from
                    # request.FILES with no validation of any kind, while the
                    # report's OWN attachment three fields up goes through
                    # `KaiReportForm.clean_attachment` (extension allowlist +
                    # MIME sniff). Same form, same submitter, same directory —
                    # `kai_reports/custom_fields/` sits beside
                    # `kai_reports/` — and one half was checked.
                    #
                    # The file is dropped and the member is TOLD, rather than the
                    # whole POST being failed: by this point the report has been
                    # saved, and a rejected attachment must not take the
                    # allegation text down with it. A silent drop would be the
                    # worst of the three.
                    if file_value:
                        try:
                            validate_uploaded_file(file_value)
                        except ValidationError as exc:
                            messages.error(
                                request,
                                f'{field.label}: {"; ".join(exc.messages)}')
                            file_value = None

                    # Only save if there's a value
                    if value or file_value:
                        response_data = {
                            'report': report,
                            'field': field,
                        }

                        if field.field_type in ['text', 'textarea', 'email', 'date', 'select', 'radio']:
                            response_data['text_value'] = value
                        elif field.field_type == 'number':
                            try:
                                response_data['number_value'] = float(value) if value else None
                            except ValueError:
                                response_data['text_value'] = value
                        elif field.field_type in ['multiselect', 'checkbox']:
                            values = request.POST.getlist(field_key)
                            response_data['json_value'] = values if values else None
                        elif field.field_type == 'file' and file_value:
                            response_data['file_value'] = file_value
                        elif field.field_type == 'member_select':
                            response_data['text_value'] = value

                        KaiReportFieldResponse.objects.create(**response_data)

                # Log activity
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='created',
                    details=f'Report created with category: {report.get_category_display()}'
                )
                ActivityLog.log_activity(
                    action_type='kai_action',
                    user=request.user,
                    # ⚠️ v3.18.2 — NO NAME. On a submission `request.user` IS
                    # the reporter, so this string named them beside the case
                    # number, on a page every officer and chair can read. The
                    # row's `user` FK is the other half of the same disclosure
                    # and is kept deliberately (the audit trail is worth
                    # keeping) but redacted at render — see `src/kai_audit.py`.
                    # Do not put a name back in here.
                    description=f'A member submitted Kai case {report.display_number}',
                    request=request,
                    object_type='KaiReport',
                    object_id=report.id,
                    object_repr=report.display_number,
                    metadata={'action': 'submitted'},
                )

                # Send email notification to Kai committee chair(s) only (NOT targeted person yet)
                try:
                    kai_committee = Committee.objects.get(is_kai_committee=True)
                    kai_chairs = kai_committee.chairs.all()

                    # Collect Kai chair emails only
                    recipient_emails = []

                    # Add Kai chair emails
                    if kai_chairs.exists():
                        chair_emails = [chair.email for chair in kai_chairs if chair.email]
                        recipient_emails.extend(chair_emails)

                    if recipient_emails:
                        subject = f'New Kai Report: {report.title}'
                        message = f"""
A new Kai report has been submitted.

Title: {report.title}
Submitted by: {report.submitted_by.name}
Submitted at: {localtime(report.submitted_at).strftime('%B %d, %Y at %I:%M %p %Z')}
{f"Directed to: {report.targeted_to.name}" if report.targeted_to else ""}

Description:
{report.description}

Tags: {', '.join(report.get_tag_labels()) if report.tags else 'None'}

Please log in to the Kai Committee page to review this report.
                        """

                        import logging
                        kai_logger = logging.getLogger('src')
                        kai_logger.info(f"[KAI EMAIL] Sending notification to {len(recipient_emails)} recipients: {recipient_emails}")

                        send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_emails)
                        kai_logger.info(f"[KAI EMAIL] Email queued for report: {report.title}")
                    else:
                        import logging
                        kai_logger = logging.getLogger('src')
                        kai_logger.warning(f"[KAI EMAIL] No recipient emails found for Kai report notification")
                except Committee.DoesNotExist:
                    import logging
                    kai_logger = logging.getLogger('src')
                    kai_logger.warning(f"[KAI EMAIL] KAI committee not found - cannot send notification")
                except Exception as e:
                    # Log error but don't fail the submission
                    import logging
                    logger = logging.getLogger('src')
                    logger.error(f"[KAI EMAIL] Failed to send Kai report email: {e}")

                messages.success(request, 'Your Kai report has been submitted successfully! The Kai chair(s) have been notified.')
                return redirect('home')
        else:
            form = KaiReportForm()

        # Populate the targeted_to dropdown with active members.
        # The old three-tier schema-probe fallback (member_status → is_active →
        # all users), with its discarded list(queryset) force-evaluations that
        # doubled the member-table query per render, predates the migration
        # consolidation (07-05-26) — the test DB now has the real schema, so a
        # single queryset is correct. (v3.15.6, 07-23 report item #3.)
        form.fields['targeted_to'].queryset = (
            ParliamentUser.objects.filter(member_status='Active').order_by('name'))

        # Get active templates
        templates = KaiReportTemplate.objects.filter(is_active=True)

        # Get custom fields (non-builtin)
        custom_fields = KaiFormField.objects.filter(is_active=True, is_builtin=False).order_by('section', 'display_order')

        # Group custom fields by section
        custom_sections = {}
        for field in custom_fields:
            section = field.section or 'Additional Information'
            if section not in custom_sections:
                custom_sections[section] = []
            custom_sections[section].append(field)

        # Get all active members for member_select fields
        all_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

        return render(request, 'kai/submit_report.html', {
            'form': form,
            'templates': templates,
            'custom_fields': custom_fields,
            'custom_sections': custom_sections,
            'all_members': all_members,
        })
    except Exception as e:
        # Table doesn't exist yet
        import logging
        logger = logging.getLogger('function_calls')
        logger.error(f"Error in submit_kai_report: {e}")
        messages.warning(request, f'Kai Reports feature error: {str(e)}')
        return redirect('home')


def _is_kai_chair(user, committee):
    """
    True only if `user` is an actual CHAIR of the Kai committee.

    ⚠️ v3.18.1 — WHY THIS EXISTS INSTEAD OF `committee.is_chair(user)`.

    `Committee.is_chair()` (`src/models/committees.py:93`) returns True for any
    **member** of a committee flagged `is_exec_board`:

        if self.is_exec_board and self.members.filter(pk=user.pk).exists():
            return True

    That is a sensible convenience for ordinary exec committees and a hole in a
    judicial one. v3.18.0 rewrote both committee-page Kai previews specifically
    to escape it, and said so:

        "Should Kai ever be flagged exec-board, every exec member would read
         allegation bodies and both parties' identities without holding a
         single KaiMemberPermission."

    **But it rewrote them to call `_get_kai_access`, and `_get_kai_access` had
    the same bypass one level down** — so the previews were routed *through*
    the hole rather than around it. The stated property was not the property
    achieved. Found 08-01-26.

    Kai access is governed **only** by `KaiMemberPermission` grants (the
    standing v3.16.2 rule, and the reason all seven Kai models are unregistered
    from `/admin/`). A boolean on the committee row must not be able to grant
    it. So: real chairs only, `is_exec_board` deliberately ignored.

    `test_kai_exec_board_bypass` flips the flag and asserts a plain Kai member
    still gets nothing.
    """
    return committee.chairs.filter(pk=user.pk).exists()


def _get_kai_access(user, committee):
    """
    Return a dict of Kai permission flags for the given user.
    Chairs and site admins get full access. Other users get their KaiMemberPermission
    flags; users with no permission row get all False.

    "Chair" here means an actual chair — see `_is_kai_chair` for why this does
    not use `Committee.is_chair()`.

    NOTE — v3.16.2: this helper previously carried @login_required,
    @require_feature_flag and @log_function_call, orphaned from an earlier
    view when the helper was inserted above it (06-05-26). All three expect a
    *request* as the first positional arg and dereference `request.user`;
    this function receives a ParliamentUser, which has no `.user`, so every
    call raised AttributeError and 500'd the whole Kai review module. The
    decorators are also redundant here — all five call sites are views that
    already apply @login_required + @require_feature_flag('kai_reports').
    Do not re-add them to this helper.
    """
    FIELDS = [
        'can_view_report_list', 'can_view_report_details',
        'can_view_submitter_identity', 'can_view_accused_identity',
        'can_edit_open_cases', 'can_add_activity', 'can_close_cases',
    ]
    from src.dev_mode import record_permission

    # ⚠️ v3.18.2 — `user.is_admin` NO LONGER GRANTS KAI ACCESS.
    #
    # This branch used to read `if user.is_admin or _is_kai_chair(...)`, so one
    # boolean on the user row conferred every permission below including both
    # party-identity flags, with no `KaiMemberPermission` anywhere. Two things
    # this codebase had already decided say it should not:
    #
    #   * The standing v3.16.2 rule — *being a Django admin is an operational
    #     role, not a grant of judicial, deliberative or ballot-level access.*
    #     All seven Kai models are unregistered from /admin/ because of it. The
    #     app layer was doing what the admin layer had been stopped from doing.
    #   * `_is_kai_chair`'s own argument, ten lines up, added by v3.18.1: *a
    #     boolean on the committee row must not be able to grant Kai access.*
    #     That does not stop being true when the boolean is on the user row.
    #
    # The operational objection was real, though — someone has to be able to
    # unstick the module. So the answer is a break-glass rather than a
    # standing grant: see `KaiBreakGlassGrant`, granted only from a shell via
    # `manage.py kai_break_glass`, time-boxed, reason-required, audited at both
    # ends, and shown as a banner on the list page while it is live.
    #
    # Dispositioned by Mason 08-02-26 ("narrow, but keep a break-glass").
    if _is_kai_chair(user, committee):
        access = {f: True for f in FIELDS} | {
            'is_full_access': True, 'is_break_glass': False,
        }
        record_permission('kai_access', 'full', 'committee chair')
        return access

    # A `KaiMemberPermission` row is the ordinary path and is checked before
    # the break-glass, so an admin who holds a real grant is treated as an
    # ordinary reviewer and never trips the banner.
    try:
        perm = KaiMemberPermission.objects.get(committee=committee, user=user)
        access = {f: getattr(perm, f) for f in FIELDS} | {
            'is_full_access': False, 'is_break_glass': False,
        }
        record_permission(
            'kai_access',
            ', '.join(f for f in FIELDS if access[f]) or 'none granted',
            'KaiMemberPermission row',
        )
        return access
    except KaiMemberPermission.DoesNotExist:
        pass

    # Break-glass. Only reachable for an admin with no permission row, so the
    # extra query costs nothing for members, reviewers or chairs.
    if user.is_admin:
        grant = KaiBreakGlassGrant.active_for(user)
        if grant is not None:
            access = {f: True for f in FIELDS} | {
                'is_full_access': True,
                'is_break_glass': True,
                'break_glass_expires_at': grant.expires_at,
            }
            record_permission(
                'kai_access', 'full (BREAK-GLASS)',
                f'KaiBreakGlassGrant #{grant.pk}, expires {grant.expires_at:%Y-%m-%d %H:%M}',
            )
            return access
        record_permission(
            'kai_access', 'none',
            'is_admin, but admin alone does not grant Kai access since v3.18.2 '
            '— and no active break-glass grant',
        )
        return {f: False for f in FIELDS} | {
            'is_full_access': False, 'is_break_glass': False,
        }

    record_permission('kai_access', 'none', 'no KaiMemberPermission row')
    return {f: False for f in FIELDS} | {
        'is_full_access': False, 'is_break_glass': False,
    }


#: Every permission `_get_kai_access` returns. Kept here so `_case_access` can
#: zero all of them without naming them one at a time — a new permission added
#: to the helper must not silently escape recusal.
_KAI_PERMISSION_FIELDS = (
    'can_view_report_list', 'can_view_report_details',
    'can_view_submitter_identity', 'can_view_accused_identity',
    'can_edit_open_cases', 'can_add_activity', 'can_close_cases',
)


def _recusal_rows_for(user, report_ids):
    """
    `{report_id: [KaiRecusal, …]}` for `user` across many cases, in ONE query.

    v3.18.2. `_case_access` needs two things from `KaiRecusal` — whether the
    user has a manual recusal on the case, and whether they hold a stand-in
    grant on it — and it fetches each with its own query. That is correct for
    a single case and quadratic-ish for a list: the Kai list page's cross-case
    activity panel called it eight times and paid sixteen queries.

    Callers that hold a set of report ids up front build this map and hand it
    to `_case_access(..., recusal_rows=…)`. Callers that do not, don't — the
    single-case path is unchanged and still does its own lookups, because a
    detail page has one report and a map would be two queries where one does.

    The filter is `user OR replacement` because those are exactly the two roles
    `_case_access` asks about; anything else on the case is irrelevant to this
    viewer.
    """
    report_ids = {rid for rid in (report_ids or ()) if rid}
    if not report_ids or not getattr(user, 'pk', None):
        return {}
    rows = KaiRecusal.objects.filter(
        Q(report_id__in=report_ids) & (Q(user=user) | Q(replacement=user))
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row.report_id, []).append(row)
    return grouped


def _case_access(user, report, kai_access, recusal_rows=None):
    """
    `kai_access` narrowed for one specific case — the recusal chokepoint.

    `recusal_rows` (v3.18.2) is an optional `{report_id: [KaiRecusal, …]}` map
    from `_recusal_rows_for`, for callers narrowing many cases at once. When
    supplied, the two `KaiRecusal` queries below are read from it instead.
    **It is a performance argument only — it must not change the answer**, and
    `test_batched_case_access_matches_unbatched` asserts exactly that, case by
    case, against the unbatched path.

    v3.18.0 — WHY THIS EXISTS
    -------------------------
    The chapter bylaws (§ vi) require that "only the accused must temporarily
    recuse their seat for their trial." The app implemented no part of that:
    `_get_kai_access()` takes a user and a committee and **never sees the
    report**, so a Kai member who was the accused in an open case could open it,
    read the allegation against themselves, see who reported them, and — holding
    `can_close_cases` — close it. The same applied in reverse to a reviewer who
    was the submitter.

    Recusal is computed from the case itself (`KaiReport.is_party`), NOT from a
    `KaiRecusal` row, so it cannot be defeated by failing to record one. The
    `KaiRecusal` model records who filled the vacated seat; this function is
    what actually enforces the vacancy.

    Fails closed: every permission goes False, and `is_recused` / `recusal_reason`
    are added so a caller can explain the refusal rather than 404ing silently.
    """
    from src.dev_mode import record_permission

    if report is None:
        return {**kai_access, 'is_recused': False, 'recusal_reason': None, 'is_standin': False}

    # 1. THE ACCUSED is fully recused — the bylaws' actual requirement.
    #    Checked first so that appointing a party as a stand-in — which
    #    `eligible_standins` refuses, but which a hand-written row could still
    #    do — cannot resurrect their access.
    reason = report.recusal_reason(user)
    if reason == 'accused':
        record_permission(
            'kai_access', 'RECUSED — all permissions withdrawn',
            f'user is the accused on case {report.pk}',
        )
        return {
            **{field: False for field in _KAI_PERMISSION_FIELDS},
            'is_full_access': False,
            'is_recused': True,
            'recusal_reason': reason,
            'is_standin': False,
        }

    # 1b. THE SUBMITTER keeps sight of the case and loses the power to decide it.
    #
    #     ⚠️ CORRECTED 07-31-26, same day. The first cut of this treated the
    #     submitter exactly like the accused — every permission withdrawn, case
    #     hidden from the list, the counts and every export. That was wrong on
    #     two counts:
    #
    #       * **The bylaws say the opposite.** § vi: "…only the accused must
    #         temporarily recuse their seat for their trial." *Only* the
    #         accused. Submitter recusal was an inference, not the rule.
    #       * **It broke a real workflow immediately.** Mason filed three test
    #         reports as himself and the Kai list showed zero — no rows, and a
    #         count of 0 to match. In a chapter this size the head of Kai is
    #         often the person who files, and hiding a case from the person who
    #         reported it helps nobody.
    #
    #     What survives from the original reasoning is the narrow part: nobody
    #     should *adjudicate* a complaint they themselves filed. So the
    #     submitter keeps every read permission their seat grants and loses
    #     `can_edit_open_cases` and `can_close_cases` on this case only.
    if reason in ('submitter', 'self'):
        narrowed = {
            **kai_access,
            'can_edit_open_cases': False,
            'can_close_cases': False,
        }
        record_permission(
            'kai_access', 'read-only — cannot decide a case they are party to',
            f'user is the {reason} on case {report.pk}',
        )
        return {
            **narrowed,
            'is_recused': False,
            'recusal_reason': reason,
            'is_standin': False,
            'is_submitter_readonly': True,
        }

    # 1c. A MANUAL recusal — recorded by the head of Kai because the member is
    #     unavailable, has declared a conflict, or has stood themselves back.
    #
    #     ⚠️ ADDED 07-31-26. Until this, `_case_access` read only the party
    #     status computed from the case, so a `KaiRecusal` row created by hand
    #     **changed nothing**: the member kept every permission while the panel
    #     said they were recused. The record and the enforcement disagreed,
    #     which is the worst of the three possible states — it looks handled.
    #
    #     Withdrawn in full, same as the accused. Someone who has stood back
    #     from a case has stood back from all of it, including recusing others.
    # v3.18.2: read from the batched map when the caller supplied one. Same
    # predicate as the query below it replaces — `report`, and `user` is us.
    if recusal_rows is not None:
        manual = next(
            (r for r in recusal_rows.get(report.pk, ()) if r.user_id == user.pk),
            None,
        )
    else:
        manual = KaiRecusal.objects.filter(report=report, user=user).first()
    if manual is not None and manual.reason in KaiRecusal.MANUAL_REASONS:
        record_permission(
            'kai_access', 'RECUSED — all permissions withdrawn',
            f'manual recusal ({manual.reason}) on case {report.pk}',
        )
        return {
            **{field: False for field in _KAI_PERMISSION_FIELDS},
            'is_full_access': False,
            'is_recused': True,
            'recusal_reason': manual.reason,
            'is_standin': False,
        }

    # 2. Stand-in appointment (bylaws §§ vi–ix). A member with no committee
    #    grant of their own holds, for this case only, the snapshot taken from
    #    the seat they are filling. UNION with any access they already have,
    #    so appointing an existing committee member never *reduces* them.
    # v3.18.2: same batched read. `standin_grant` returns the snapshot dict or
    # None, and fails closed on an empty `granted_permissions` — reproduced
    # exactly here so the two paths cannot disagree.
    if recusal_rows is not None:
        _standin_row = next(
            (r for r in recusal_rows.get(report.pk, ()) if r.replacement_id == user.pk),
            None,
        )
        grant = None if _standin_row is None else (_standin_row.granted_permissions or {})
    else:
        grant = KaiRecusal.standin_grant(report, user)
    if grant is not None:
        merged = {
            field: bool(kai_access.get(field)) or bool(grant.get(field))
            for field in _KAI_PERMISSION_FIELDS
        }
        record_permission(
            'kai_access',
            ', '.join(f for f in _KAI_PERMISSION_FIELDS if merged[f]) or 'none granted',
            f'stand-in appointment on case {report.pk}',
        )
        return {
            **kai_access, **merged,
            'is_recused': False,
            'recusal_reason': None,
            'is_standin': True,
        }

    return {**kai_access, 'is_recused': False, 'recusal_reason': None, 'is_standin': False}


def _recused_case_ids(user):
    """
    Case pks `user` is the ACCUSED on — the cases whose content is withheld
    from them wherever it would otherwise be rendered.

    ⚠️ v3.18.1 — READ THIS BEFORE USING IT. The five callers do NOT all do the
    same thing with the list, and the difference is deliberate:

    * **Excluded entirely** — `global_search` (:324), the two committee-page
      previews (`committee_home`, `committee_detail`), and both CSV exports
      (`export_kai_reports_csv`, `bulk_actions_kai_reports`). On those surfaces
      a hit is itself a disclosure and there is nothing to gain by showing a
      redacted stub.
    * **Shown but redacted** — the reviewer list (`view_kai_reports`). v3.18.0
      moved it here on purpose: hiding the row protects nothing (the accused is
      notified and it is on their own dashboard) and excluding it made the
      counts disagree with the rows. The card withholds the allegation body and
      both identities instead.

    **The reviewer list therefore passes these ids to `_kai_search_q` as
    `redacted_case_ids`, and that is load-bearing.** A row that is displayed but
    redacted must not be reachable by a predicate over the fields it redacts, or
    the search box becomes an oracle over them — see `_kai_search_q`.

    The viewer still sees the case on their OWN dashboard as accused — that is
    notice, which they are entitled to, and the member-facing templates never
    render `submitted_by`.
    """
    if not getattr(user, 'pk', None):
        return []
    # ⚠️ CORRECTED 07-31-26: this was `Q(submitted_by=user) | Q(targeted_to=user)`
    # and hid every case the viewer had FILED as well as every case naming them.
    # The bylaws recuse "only the accused" (§ vi), and hiding a case from its own
    # reporter broke the list outright — three self-filed test reports rendered
    # as an empty queue with a count of 0. Accused only. A submitter still sees
    # their case; `_case_access` removes their power to decide it.
    # `targeted_to=user` AND NOT `submitted_by=user`: a self-report is not
    # recused (see `KaiReport.recusal_reason_for_pk`) — there is no identity to
    # withhold from the person who wrote it.
    return list(
        KaiReport.objects
        .filter(targeted_to=user)
        .exclude(submitted_by=user)
        .values_list('pk', flat=True)
    )


def _redact_activity_log(entries, report, kai_access):
    """
    Attach `display_actor` / `display_details` to each activity entry.

    ⚠️ v3.18.1 — THE ACTIVITY FEED WAS THE TENTH SURFACE, AND NOBODY HAD EVER
    COUNTED IT. `templates/kai/view_reports.html` enumerates the surfaces that
    render `KaiReport.description`; `docs/CONFIDENTIALITY_MATRIX.md` enumerates
    the surfaces that render either identity. **Neither list contained the
    activity log**, and it emits both identities two different ways:

      1. **Via the row's author.** `submit_kai_report` writes the `created`
         entry with `user=request.user` — the submitter. Both templates print
         `{{ entry.user.name }}`, so the case detail page opened with
         "Report Created · <the reporter>" for every reviewer who could reach
         it, whatever `can_view_submitter_identity` said.
      2. **Via `details`.** Three call sites interpolated the accused's name
         into the string: 'Accused person set to: X', 'Accused person removed
         (was: X)', 'Accused (X) notified of the case'. Those sites no longer
         write names — but rows written before v3.18.1 still contain them, so
         the substitution below is not belt-and-braces, it is the fix for
         every row already in the database.

    Measured 08-01-26: a reviewer holding `can_view_report_details` and neither
    identity flag saw both names exactly twice on the detail page — once in the
    Activity card, once in the Case Timeline v3.18.0 added. The page header was
    redacting correctly the whole time; the feed underneath undid it.

    **`can_view_report_details` is NOT a superset of the two identity flags.**
    `KaiMemberPermission` models them as four independent booleans and the
    grant UI offers them independently, so a details-only reviewer is a real
    configuration and this is a real disclosure to them.

    Templates must render `display_actor` / `display_details` and never
    `entry.user.name` / `entry.details` directly. `test_kai_activity_redaction`
    fails if either template reverts.
    """
    submitter_id = report.submitted_by_id
    accused_id = report.targeted_to_id
    show_submitter = bool(kai_access.get('can_view_submitter_identity'))
    show_accused = bool(kai_access.get('can_view_accused_identity'))

    # Resolved once, not per row.
    submitter_name = report.submitted_by.name if report.submitted_by_id else ''
    accused_name = report.targeted_to.name if report.targeted_to_id else ''

    for entry in entries:
        # -- the actor ---------------------------------------------------
        if entry.user_id is None:
            entry.display_actor = 'System'
        elif entry.user_id == submitter_id and not show_submitter:
            entry.display_actor = 'Anonymous'
        elif entry.user_id == accused_id and not show_accused:
            entry.display_actor = 'Redacted'
        else:
            entry.display_actor = entry.user.name if entry.user else 'System'

        # -- the details string ------------------------------------------
        # A plain substring swap, because the names in legacy rows are free
        # text with no structure to parse. Longest-first is not needed here —
        # the two names are swapped independently and a member cannot hold
        # both roles unless it is a self-report, in which case both flags
        # resolve to the same person and the swap is a no-op either way.
        details = entry.details or ''
        if details:
            if submitter_name and not show_submitter:
                details = details.replace(submitter_name, 'Anonymous')
            if accused_name and not show_accused:
                details = details.replace(accused_name, 'Redacted')
        entry.display_details = details

    return entries


def _kai_search_q(search_query, kai_access, redacted_case_ids=()):
    """
    Build the Kai report search predicate for a user with `kai_access`.

    v3.16.3 — SECURITY. A filter predicate is a join key. Both the report list
    and the CSV export used to filter unconditionally on submitted_by__name,
    targeted_to__name and description, while redacting exactly those columns
    in the output for users lacking the matching permission. That made the
    redaction cosmetic: a list-only reviewer could type a member's name and
    read off which cases that member submitted or was accused in, or
    binary-search the allegation body a word at a time.

    Each searchable field is now gated by the same flag that governs *reading*
    it. Title and tags are visible to anyone who can see the list at all, so
    they are always searchable.

    TAGS ARE ONLY SAFE HERE BECAUSE THEY ARE A CLOSED VOCABULARY. Tags were
    free text until 07-28-26, which meant a chair could type a member's name
    into one and hand it to every list-level reviewer — searchable, rendered on
    the list card, and exported in the CSV — straight through the identity
    redaction the rest of this function exists to enforce. `KaiReport.tags` is
    now validated against `KaiReport.ALLOWED_TAGS` at every write site. If you
    ever loosen that back to free text, this line has to become gated too.

    Kept as one shared helper on purpose: the list view and the export had
    duplicated copies of this filter, which is how both ended up wrong. If a
    new searchable field is added, it gets gated here once.

    ⚠️ v3.18.1 — `redacted_case_ids`: PERMISSION IS NOT THE ONLY THING THAT
    REDACTS. The gating above answers "may this user read this field *at all*",
    which was the whole question until v3.18.0. It is no longer. The reviewer
    list now shows a case the viewer is the accused on **as a redacted row**
    rather than excluding it, so for those particular rows the allegation body
    and the submitter's identity are withheld even though the viewer's
    committee-level flags say otherwise — and the flags are what this function
    reads.

    Without this argument the search box was a clean oracle over exactly the
    three fields the card refuses to print. Typing a word that appears only in
    the hidden description returned the row (identifiable by its case number
    and its "Recused" badge); typing the reporter's surname returned it too.
    Reproduced both directions 08-01-26, and `src/test_kai_search_oracle.py`
    fails against the pre-fix helper.

    So: the caller passes the pks whose content is redacted *for this viewer*,
    and those rows are matchable on title and tags only — the two fields the
    card does render. Everything else keeps the permission-gated predicate.

    **The general rule, and it is the one this codebase keeps re-learning:
    when a surface stops EXCLUDING a row and starts REDACTING it, every
    predicate that touches that row becomes a disclosure.** Exclusion protects
    the filters for free; redaction does not.
    """
    # Always searchable: the two fields the redacted card still renders.
    public_q = Q(title__icontains=search_query) | Q(tags__icontains=search_query)

    gated_q = Q()
    if kai_access['can_view_report_details']:
        gated_q |= Q(description__icontains=search_query)
    if kai_access['can_view_submitter_identity']:
        gated_q |= Q(submitted_by__name__icontains=search_query)
    if kai_access['can_view_accused_identity']:
        gated_q |= Q(targeted_to__name__icontains=search_query)

    if not gated_q:
        # An empty Q() is falsy. Nothing gated is searchable for this user, so
        # there is nothing for `redacted_case_ids` to protect.
        return public_q

    redacted = list(redacted_case_ids or ())
    if redacted:
        # `pk` is on the base table, so this negation is a plain NOT IN — no
        # subquery, no join semantics to get wrong.
        gated_q &= ~Q(pk__in=redacted)

    return public_q | gated_q


def _kai_search_placeholder(kai_access):
    """
    Describe, for the search box, exactly the fields `_kai_search_q` will search.

    v3.16.3: the template hardcoded the full field list, so after the predicate
    was gated a list-only reviewer could search a member's name, get nothing,
    and conclude that member has no cases. Keep this in step with
    `_kai_search_q` — they are two views of one decision.
    """
    fields = ['title']
    if kai_access['can_view_report_details']:
        fields.append('description')
    if kai_access['can_view_submitter_identity']:
        fields.append('submitter')
    if kai_access['can_view_accused_identity']:
        fields.append('targeted person')
    fields.append('tags')
    if len(fields) == 2:
        joined = ' or '.join(fields)
    else:
        joined = ', '.join(fields[:-1]) + ', or ' + fields[-1]
    return f'Search by {joined}...'


@login_required
@require_feature_flag('kai_reports')
def view_kai_reports(request):
    """View for Kai chairs to see all submitted reports"""
    # Check if user is a Kai chair
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    # v3.18.0: a stand-in appointed under bylaws §§ vi-ix may hold no
    # committee-level grant of their own. They must still be able to reach the
    # case they were appointed to — otherwise the appointment is ceremonial.
    # They see ONLY those cases; the list is restricted below.
    standin_case_ids = list(
        KaiRecusal.objects.filter(replacement=request.user)
        .values_list('report_id', flat=True)
    )
    if not kai_access['can_view_report_list'] and not standin_case_ids:
        messages.error(request, 'You do not have permission to view Kai reports.')
        return redirect('home')

    # Check if KaiReport table exists
    try:
        # Get filter from query params
        status_filter = request.GET.get('status', 'all')
        category_filter = request.GET.get('category', 'all')
        search_query = request.GET.get('search', '').strip()
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')

        # Start with all reports.
        #
        # v3.18.0 — RECUSAL, and the design here went through three versions.
        # It first excluded every case the viewer was a party to — submitter or
        # accused — which hid three self-filed test reports and rendered an
        # empty queue. Narrowing it to the accused still hid two.
        #
        # The exclusion was the wrong tool. **Hiding the row protects nothing**:
        # the accused already knows the case exists — they are notified, and it
        # is on their own dashboard under "Reports Where I'm Named". What they
        # must not have is the allegation body, the submitter's identity, and
        # any power to act on it. `manage_kai_report` refuses them, the exports
        # exclude, and the card below redacts.
        #
        # So the list shows EVERY case and marks the viewer's own — see
        # `viewer_recusal` below and the redacted card in view_reports.html.
        # The counts then agree with the rows, which is the property that broke.
        #
        # ⚠️ v3.18.1 — THE HALF THAT DID NOT MOVE WITH IT. Switching from
        # exclusion to redaction left the SEARCH PREDICATE reading the viewer's
        # committee-level flags, so it still matched on `description` and
        # `submitted_by__name` for the very rows the card refuses to print
        # them for. A viewer who was the accused could recover the allegation
        # body a guess at a time and identify their own reporter by surname —
        # reproduced 08-01-26 in both directions. `redacted_case_ids` below is
        # the fix; see `_kai_search_q`.
        redacted_case_ids = _recused_case_ids(request.user)
        reports = KaiReport.objects.all()
        # A pure stand-in (no committee grant) sees only their appointments.
        if not kai_access['can_view_report_list']:
            reports = reports.filter(pk__in=standin_case_ids)
        assigned_filter = request.GET.get('assigned', 'all')

        # Apply status filter
        if status_filter == 'pending':
            reports = reports.filter(status='pending')
        elif status_filter == 'reviewed':
            reports = reports.filter(status='reviewed')
        elif status_filter == 'archived':
            reports = reports.filter(status='archived')

        # Apply category filter
        if category_filter != 'all':
            reports = reports.filter(category=category_filter)

        # Apply search filter — v3.16.3: permission-gated, see _kai_search_q.
        # v3.18.1: `redacted_case_ids` narrows it to title/tags for the rows
        # this viewer sees redacted. Without it the box is an oracle.
        if search_query:
            reports = reports.filter(
                _kai_search_q(search_query, kai_access, redacted_case_ids)
            )

        # Apply date range filter
        #
        # v3.18.1: these used to carry function-local `from datetime import …`
        # lines. A local import binds the name for the WHOLE function scope, so
        # `timedelta` used earlier in the same function raised
        # UnboundLocalError. Both are module-level imports now — do not put
        # them back inside the branches.
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                reports = reports.filter(submitted_at__gte=date_from_obj)
            except ValueError:
                pass

        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                # Include the entire day
                date_to_obj = date_to_obj + timedelta(days=1)
                reports = reports.filter(submitted_at__lt=date_to_obj)
            except ValueError:
                pass

        # v3.18.0: "My cases" filter — `assigned_to` is set independently of
        # status, so this is a genuine work queue rather than a status view.
        if assigned_filter == 'me':
            reports = reports.filter(assigned_to=request.user)
        elif assigned_filter == 'unassigned':
            reports = reports.filter(assigned_to__isnull=True)

        # select_related directly — the test-DB schema-probe fallback is gone
        # (migrations consolidated + tracked since 07-05-26). (v3.15.6)
        #
        # v3.18.1 — CAPPED. This materialised the whole table. Kai volume is
        # small today, which is exactly what the three views capped in v3.17.5
        # and v3.17.7 were before they weren't. The cap comes with true totals
        # from a separate GROUP BY below, because **a capped page must not
        # count its capped list** — `counts` and `category_counts` are already
        # aggregates over `_visible`, so they stay honest for free. The two
        # that were computed off `reports` (`stale_count`, `assigned_counts`)
        # are moved to aggregates for the same reason.
        reports = list(
            reports
            .select_related('submitted_by', 'reviewed_by', 'targeted_to', 'assigned_to')
            .defer(*member_defer('submitted_by', 'reviewed_by', 'targeted_to', 'assigned_to'))
            .order_by('-submitted_at')[:KAI_LIST_LIMIT]
        )

        # Per-row party status. The list template gates content on ONE
        # committee-level `kai_access` for every row, so a case the viewer is
        # named in needs its own flag — set here, read in view_reports.html.
        for _report in reports:
            _report.viewer_recusal = _report.recusal_reason(request.user)

        # Status counts — v3.16.3: one aggregate instead of four separate
        # .count() round trips, matching the category pattern directly below.
        #
        # v3.18.1 — the v3.18.0 comment here claimed "recused cases are
        # excluded here too", which was never true of the shipped code and had
        # stopped being the right thing to do: the list SHOWS those rows
        # redacted, so a count that excluded them would disagree with the rows
        # on screen — the exact defect the exclusion was meant to prevent, in
        # reverse. `_visible` is every case the viewer can see, and the counts
        # match the list. Removed rather than corrected in place, because a
        # comment describing enforcement that does not exist reads as
        # enforcement to the next person.
        _visible = KaiReport.objects.all()
        if not kai_access['can_view_report_list']:
            _visible = _visible.filter(pk__in=standin_case_ids)
        status_map = {
            row['status']: row['total']
            for row in _visible.values('status').annotate(total=Count('id'))
        }
        counts = {
            'all': sum(status_map.values()),
            'pending': status_map.get('pending', 0),
            'reviewed': status_map.get('reviewed', 0),
            'archived': status_map.get('archived', 0),
        }

        # Get counts for category filters — one aggregated query instead of one per category
        cat_qs = _visible.values('category').annotate(total=Count('id'))
        cat_map = {row['category']: row['total'] for row in cat_qs}
        category_counts = {cat_value: cat_map.get(cat_value, 0) for cat_value, _ in KaiReport.CATEGORY_CHOICES}

        # v3.18.0 — case aging. Nothing surfaced how long a case had been
        # sitting, so a `pending` case could age indefinitely with no signal.
        # One extra query for the oldest unreviewed case; `days_open` and
        # `is_stale` are properties, computed per row with no further queries.
        #
        # v3.18.1: skip cases this viewer is recused from. The banner's "Open
        # oldest" button links straight to `manage_kai_report`, which refuses a
        # party — so pointing it at their own case produced a button that could
        # only bounce, above a case number the card below is careful about.
        _oldest_qs = _visible.filter(status='pending')
        if redacted_case_ids:
            _oldest_qs = _oldest_qs.exclude(pk__in=redacted_case_ids)
        oldest_pending = (
            _oldest_qs
            .select_related('assigned_to')
            .defer(*member_defer('assigned_to'))
            .order_by('submitted_at')
            .first()
        )

        # v3.18.1 — these were `sum(... for r in reports)` over the capped
        # list, so past the cap they would have frozen at whatever the newest
        # KAI_LIST_LIMIT rows happened to contain. Aggregates instead.
        #
        # v3.18.2 — ONE aggregate, not three. Moving them off the capped list
        # was right; doing it with three separate `.count()` round trips beside
        # the two GROUP BYs directly above was not. CLAUDE.md's own checklist
        # names this: *repeated identical queries that could share a single
        # `aggregate()` call*. 5 queries → 3.
        _stale_before = timezone.now() - timedelta(days=KaiReport.STALE_AFTER_DAYS)
        _tallies = _visible.aggregate(
            stale=Count('id', filter=Q(status='pending', submitted_at__lte=_stale_before)),
            mine=Count('id', filter=Q(assigned_to=request.user)),
            unassigned=Count('id', filter=Q(assigned_to__isnull=True)),
        )
        stale_count = _tallies['stale']
        assigned_counts = {
            'mine': _tallies['mine'],
            'unassigned': _tallies['unassigned'],
        }

        # ⚠️ v3.18.2 — THE TRUNCATION NOTICE USED TO SWITCH ITSELF OFF UNDER
        # EXACTLY THE CONDITION IT EXISTS FOR.
        #
        # It was `total_reports > len(reports) and not (any filter active)`.
        # The guard was there for a real reason — `counts['all']` is a total
        # across every status, so with a status filter applied the inequality
        # is true for an innocent reason and would fire a false notice. But it
        # suppressed the notice *entirely* under any filter while `[:LIMIT]`
        # still applied: search a common word, match 700 cases, see 500 rows
        # and no indication that 200 are missing. The template's own advice —
        # "Filter or search to reach older cases" — walked the user into the
        # one state where the warning could not appear.
        #
        # Truncation is knowable without a second COUNT: a full page IS the
        # cap. This over-reports in the exact-multiple case (a result set of
        # precisely 500 says it was truncated), which is the right way round —
        # over-warning costs a sentence, under-warning hides cases.
        total_reports = counts['all']
        reports_truncated = len(reports) >= KAI_LIST_LIMIT
        filters_active = bool(
            status_filter != 'all' or category_filter != 'all'
            or search_query or date_from or date_to or assigned_filter != 'all'
        )
    except DatabaseError:
        # v3.18.1: was a bare `except Exception`, which flattened ANY error in
        # the eighty lines above — a typo, a FieldError, a template-context
        # mistake — into an empty list plus "table not yet created". The
        # v3.15.6 schema probes were removed from this module for that reason
        # and this handler outlived them. Narrowed to the error it names.
        reports = []
        status_filter = request.GET.get('status', 'all')
        category_filter = request.GET.get('category', 'all')
        search_query = request.GET.get('search', '').strip()
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        counts = {
            'all': 0,
            'pending': 0,
            'reviewed': 0,
            'archived': 0,
        }
        category_counts = {}
        cat_map = {}
        assigned_filter = 'all'
        oldest_pending = None
        stale_count = 0
        assigned_counts = {'mine': 0, 'unassigned': 0}
        total_reports = 0
        reports_truncated = False
        filters_active = False
        messages.info(request, 'Kai Reports database table not yet created. This is a preview of the interface.')

    # Dashboard stats (compute after main try/except so counts are available)
    try:
        # v3.18.1: `timedelta` is a module-level import now — a local one here
        # bound the name for the whole function and broke its earlier use.
        import json

        category_data = {
            cat_label: cat_map.get(cat_value, 0)
            for cat_value, cat_label in KaiReport.CATEGORY_CHOICES
            if cat_map.get(cat_value, 0)
        }

        # Deliberation outcomes — v3.16.3: one aggregate, was three .count() calls.
        outcome_map = {
            row['deliberation_outcome']: row['total']
            for row in KaiReport.objects.values('deliberation_outcome').annotate(total=Count('id'))
        }
        outcome_pending = outcome_map.get('pending', 0)
        outcome_heard = outcome_map.get('heard', 0)
        outcome_thrown_out = outcome_map.get('thrown_out', 0)

        # Six-month trend.
        #
        # v3.16.3 — this was two bugs. It walked months with
        # `current_date - timedelta(days=30 * i)`, which is not one step per
        # calendar month: on 32 days of 2026 two of the six steps land in the
        # same month, so one dict key overwrote another and the chart silently
        # rendered five bars instead of six. On 2026-03-01 the keys came out
        # ['Oct','Nov','Dec','Dec','Jan','Mar'] — February missing entirely and
        # December double-counted. It also fired one COUNT per month.
        #
        # Now: step by calendar month, and bucket every report in the window
        # with a single grouped query.
        now = timezone.localtime()
        month_starts = []
        cursor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        for _ in range(6):
            month_starts.append(cursor)
            cursor = (cursor - timedelta(days=1)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
        month_starts.reverse()

        window_start = month_starts[0]
        month_counts = {}
        for row in (
            KaiReport.objects
            .filter(submitted_at__gte=window_start)
            .annotate(bucket=TruncMonth('submitted_at'))
            .values('bucket')
            .annotate(total=Count('id'))
        ):
            bucket = row['bucket']
            if bucket is None:
                continue
            if timezone.is_aware(bucket):
                bucket = timezone.localtime(bucket)
            month_counts[(bucket.year, bucket.month)] = row['total']

        # Built in order, one entry per calendar month — no key can collide.
        monthly_data = {
            ms.strftime('%b %Y'): month_counts.get((ms.year, ms.month), 0)
            for ms in month_starts
        }

        # ⚠️ v3.18.1 — THE FOURTH COPY OF THE ACTIVITY FEED, and in some ways
        # the worst: it spans EVERY case rather than one, and it sits on the
        # list page, so its audience is everyone with `can_view_report_list`
        # rather than everyone with `can_view_report_details`.
        #
        # It rendered `{{ activity.user.name }}`, and the author of a `created`
        # entry is the case's submitter — so the panel read "Report Created —
        # <case title> · <the person who reported it>" for every case in the
        # chapter, to reviewers with no identity grant at all.
        #
        # `_redact_activity_log` works per report, so it is applied per entry
        # here against that entry's own case. Cross-case recusal matters too: a
        # viewer who is the accused on one of these cases must not read its
        # activity out of this panel either.
        #
        # v3.18.2 — THE RECUSAL LOOKUPS ARE BATCHED. The loop below called
        # `_case_access` per entry, and `_case_access` falls through to two
        # queries (`KaiRecusal` manual row, then `standin_grant`) for every
        # case the viewer is not a party to — i.e. almost all of them. Eight
        # entries × two = sixteen queries on every load of this page, for every
        # reviewer. `_recusal_rows_for` fetches all of them at once and
        # `_case_access` reads from that map instead.
        _entries = list(
            KaiReportActivity.objects
            .select_related('report', 'report__submitted_by', 'report__targeted_to', 'user')
            .defer(*member_defer('user'))
            .order_by('-timestamp')[:8]
        )
        _recusal_rows = _recusal_rows_for(
            request.user, {e.report_id for e in _entries},
        )
        recent_activities = []
        for _entry in _entries:
            _entry_access = _case_access(
                request.user, _entry.report, kai_access, recusal_rows=_recusal_rows,
            )
            if _entry_access.get('is_recused'):
                continue
            _redact_activity_log([_entry], _entry.report, _entry_access)
            recent_activities.append(_entry)
    except DatabaseError:
        category_data = {}
        outcome_pending = outcome_heard = outcome_thrown_out = 0
        monthly_data = {}
        recent_activities = []

    import json as _json
    context = {
        'reports': reports,
        'status_filter': status_filter,
        'category_filter': category_filter,
        'search_query': search_query,
        # v3.16.3: describes exactly what _kai_search_q will search for THIS user.
        'search_placeholder': _kai_search_placeholder(kai_access),
        'date_from': date_from,
        'date_to': date_to,
        'counts': counts,
        'category_counts': category_counts,
        'kai_committee': kai_committee,
        'category_choices': KaiReport.CATEGORY_CHOICES,
        # v3.18.2: `'total_reports': counts['all']` used to sit here as well as
        # below. Same dict literal, so the later one silently won — which was
        # the correct one, but a duplicate key in a 40-line context dict is a
        # trap for whoever edits the wrong copy. Removed; the survivor is in
        # the v3.18.1 cap block below.
        'pending_count': counts['pending'],
        'reviewed_count': counts['reviewed'],
        'archived_count': counts['archived'],
        'category_data': _json.dumps(category_data),
        'monthly_data': _json.dumps(monthly_data),
        'outcome_pending': outcome_pending,
        'outcome_heard': outcome_heard,
        'outcome_thrown_out': outcome_thrown_out,
        'recent_activities': recent_activities,
        'kai_access': kai_access,

        # v3.18.0 — aging + assignment
        'oldest_pending': oldest_pending,
        'stale_count': stale_count,
        'stale_after_days': KaiReport.STALE_AFTER_DAYS,
        'assigned_filter': assigned_filter,
        'assigned_counts': assigned_counts,
        # v3.18.1 — cap. `total_reports` is the true total from the GROUP BY,
        # not `len(reports)`; `reports_truncated` drives the notice.
        'total_reports': total_reports,
        'reports_truncated': reports_truncated,
        'report_fetch_limit': KAI_LIST_LIMIT,
        # v3.18.2 — the notice wording changes depending on whether a filter is
        # narrowing the list, because "of {{ total_reports }} cases" is only
        # true when nothing is filtered. Previously the notice simply did not
        # render under a filter, which is how truncation went silent.
        'filters_active': filters_active,
    }

    return render(request, 'kai/view_reports.html', context)


#: Column headers for every Kai report CSV export.
#:
#: v3.17.7 — WHY THE HEADERS AND THE ROW BUILDER ARE SHARED
#: --------------------------------------------------------
#: There were two exports writing these thirteen columns: this module's
#: `export_kai_reports_csv` and the `action == 'export_csv'` branch of
#: `bulk_actions_kai_reports`, about 1,100 lines apart. v3.16.2 added
#: per-permission redaction to the first one and nobody noticed the second,
#: so the bulk export wrote **Submitted By, Targeted To and Description
#: unredacted** to anyone holding `can_view_report_list` — which is exactly the
#: population the redaction exists to protect against, and it was a visible
#: option in the bulk-action dropdown rather than a hidden endpoint.
#:
#: The general lesson, and this codebase has now paid for it four times
#: (v3.16.2 admin/CSV, v3.16.3 list-filter/export, v3.17.5's four sites of the
#: vote-COUNT pattern, and this): **when a control is applied to one view, grep
#: for the other views that write the same columns.** Two copies of a redaction
#: rule is one copy too many — so there is now one row builder and one header
#: list, and `KaiCsvExportsAreRedactedTests` fails if a third `csv.writer` is
#: added to this module without going through them.
KAI_CSV_HEADERS = [
    'ID',
    'Title',
    'Category',
    'Submitted By',
    'Targeted To',
    'Submitted At',
    'Status',
    'Deliberation Outcome',
    'Minutes Closed',
    'Reviewed By',
    'Reviewed At',
    'Tags',
    'Description',
]


def _kai_csv_row(report, kai_access):
    """
    One CSV row for `report`, redacted against `kai_access`.

    Three fields are governed by permissions strictly narrower than the
    `can_view_report_list` that gates the exports themselves, and each must
    match what the in-app detail view would show this user:

      * ``Submitted By``  → ``can_view_submitter_identity``
      * ``Targeted To``   → ``can_view_accused_identity``
      * ``Description``   → ``can_view_report_details`` (the allegation body)

    `report` is expected to come from a queryset with `submitted_by`,
    `reviewed_by` and `targeted_to` selected.
    """
    return [
        report.id,
        report.title,
        report.get_category_display(),
        report.submitted_by.name if kai_access['can_view_submitter_identity'] else '[Redacted]',
        (report.targeted_to.name if report.targeted_to else '') if kai_access['can_view_accused_identity'] else '[Redacted]',
        localtime(report.submitted_at).strftime('%Y-%m-%d %H:%M:%S'),
        report.get_status_display(),
        report.get_deliberation_outcome_display(),
        'Yes' if report.closed_by_accused_request else 'No',
        report.reviewed_by.name if report.reviewed_by else '',
        localtime(report.reviewed_at).strftime('%Y-%m-%d %H:%M:%S') if report.reviewed_at else '',
        ', '.join(report.get_tag_labels()),
        report.description if kai_access['can_view_report_details'] else '[Redacted]',
    ]


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def export_kai_reports_csv(request):
    """Export filtered Kai reports to CSV"""
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_list']:
        messages.error(request, 'You do not have permission to export Kai reports.')
        return redirect('home')

    # Get same filters as view
    status_filter = request.GET.get('status', 'all')
    category_filter = request.GET.get('category', 'all')
    search_query = request.GET.get('search', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    try:
        # Start with all reports
        # v3.18.0 — RECUSAL: excluded before filtering, so a recused case
        # cannot be reached by any filter combination either.
        redacted_case_ids = _recused_case_ids(request.user)
        reports = KaiReport.objects.exclude(pk__in=redacted_case_ids)

        # Apply filters (same logic as view)
        if status_filter == 'pending':
            reports = reports.filter(status='pending')
        elif status_filter == 'reviewed':
            reports = reports.filter(status='reviewed')
        elif status_filter == 'archived':
            reports = reports.filter(status='archived')

        if category_filter != 'all':
            reports = reports.filter(category=category_filter)

        # v3.16.3: same permission-gated predicate the list view uses. The
        # export redacts Submitted By / Targeted To / Description below; before
        # this change it still *filtered* on them, which handed the redacted
        # values straight back to the caller.
        #
        # v3.18.1: `redacted_case_ids` is passed even though those rows were
        # already excluded two lines up. Belt and braces on purpose — the
        # helper's guarantee should not depend on each caller remembering to
        # exclude first, which is precisely how the list view's version of this
        # went wrong.
        if search_query:
            reports = reports.filter(
                _kai_search_q(search_query, kai_access, redacted_case_ids)
            )

        if date_from:
            from datetime import datetime
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                reports = reports.filter(submitted_at__gte=date_from_obj)
            except ValueError:
                pass

        if date_to:
            from datetime import datetime, timedelta
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
                date_to_obj = date_to_obj + timedelta(days=1)
                reports = reports.filter(submitted_at__lt=date_to_obj)
            except ValueError:
                pass

        # select_related directly — test-DB fallback removed (v3.15.6)
        reports = list(reports.select_related('submitted_by', 'reviewed_by', 'targeted_to').defer(*member_defer('submitted_by', 'reviewed_by', 'targeted_to')).order_by('-submitted_at'))

        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="kai_reports_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        # v3.17.7: headers and per-row redaction moved to KAI_CSV_HEADERS /
        # _kai_csv_row so this export and the bulk one cannot drift. The
        # redaction rule itself is unchanged from v3.16.2.
        writer.writerow(KAI_CSV_HEADERS)

        for report in reports:
            writer.writerow(_kai_csv_row(report, kai_access))

        ActivityLog.log_activity(
            action_type='kai_action',
            user=request.user,
            description=f'{request.user.name} exported Kai reports CSV ({len(reports)} records)',
            request=request,
            object_type='KaiReport',
            metadata={'action': 'export_csv', 'record_count': len(reports)},
        )

        return response

    except Exception:
        # v3.16.3: this used to interpolate str(e) into the flash message,
        # surfacing DB/driver internals (table and column names, query text) to
        # any list-level reviewer. Log it for the operator; show the user
        # nothing they'd have to be trusted with.
        logger.exception('Kai CSV export failed for user %s', request.user.pk)
        messages.error(
            request,
            'Failed to export reports. The error has been logged — contact an administrator '
            'if it keeps happening.'
        )
        return redirect('view_kai_reports')


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def manage_kai_report(request, report_id):
    """Manage a specific Kai report (mark as reviewed, add notes, etc.)"""
    # Check if KaiReport table exists
    try:
        report = get_object_or_404(KaiReport, id=report_id)
    except Exception:
        messages.warning(request, 'Kai Reports feature is not yet set up. Please run database migrations.')
        return redirect('home')

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    # v3.18.0 — RECUSAL. Narrow to this case before any permission is read.
    # A member who is the accused or the submitter has every permission
    # withdrawn here, so the checks below refuse them exactly as they refuse a
    # member with no grant at all. See `_case_access`.
    kai_access = _case_access(request.user, report, kai_access)
    if kai_access.get('is_recused'):
        messages.error(
            request,
            'You are recused from this case because you are the '
            f"{kai_access['recusal_reason']} named in it. Chapter bylaws § vi.",
        )
        return redirect('view_kai_reports')
    if not kai_access['can_view_report_details']:
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('home')

    if request.method == 'POST':
        action = request.POST.get('action')

        # Action-level permission checks
        _edit_actions = {'mark_reviewed', 'mark_pending', 'update_tags', 'update_deliberation',
                         'link_report', 'unlink_report', 'update_accused', 'notify_accused',
                         'notify_submitter',
                         # v3.18.0 — assigning a case is an edit, not a close.
                         'assign_case'}
        _activity_actions = {'update_notes', 'add_activity'}
        _close_actions = {'archive', 'approve_closure', 'deny_closure'}

        if action in _edit_actions and not kai_access['can_edit_open_cases']:
            messages.error(request, 'You do not have permission to edit cases.')
            return redirect('manage_kai_report', report_id=report.id)
        if action in _activity_actions and not kai_access['can_add_activity']:
            messages.error(request, 'You do not have permission to add activity to cases.')
            return redirect('manage_kai_report', report_id=report.id)
        if action in _close_actions and not kai_access['can_close_cases']:
            messages.error(request, 'You do not have permission to close cases.')
            return redirect('manage_kai_report', report_id=report.id)

        # v3.18.0 — case assignment. `reviewed_by` is only set at review
        # time, so before that a case had no owner at all.
        if action == 'assign_case':
            raw = request.POST.get('assigned_to') or ''
            if not raw:
                report.assigned_to = None
                detail = 'Case unassigned.'
            else:
                # Must be an active Kai member and not a party — the same rule
                # the <select> is built from, re-checked because a POST is not
                # a form.
                candidate = kai_committee.members.filter(
                    pk=raw, member_status='Active',
                ).exclude(
                    pk__in=[pk for pk in (report.submitted_by_id, report.targeted_to_id) if pk]
                ).first()
                if candidate is None:
                    messages.error(request, 'That member cannot be assigned to this case.')
                    return redirect('manage_kai_report', report_id=report.id)
                report.assigned_to = candidate
                detail = f'Case assigned to {candidate.name}.'
            report.save(update_fields=['assigned_to'])
            KaiReportActivity.objects.create(
                report=report, user=request.user,
                action='status_changed', details=detail,
            )
            messages.success(request, detail)
            return redirect('manage_kai_report', report_id=report.id)

        if action == 'mark_reviewed':
            report.mark_as_reviewed(request.user)
            messages.success(request, f'Report "{report.title}" marked as reviewed.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='status_changed',
                details=f'Status changed from pending to reviewed'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} marked Kai case #{report.id} as reviewed',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'mark_reviewed'},
            )

            # Send email notification to submitter
            try:
                if report.submitted_by.email:
                    subject = f'Kai Report Update: {report.title}'
                    message = f"""
Your Kai report has been reviewed.

Report Title: {report.title}
Status: Reviewed
Reviewed by: {request.user.name}
Reviewed at: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}

You can view the full report details at the Kai Committee page.
                    """
                    send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, [report.submitted_by.email])
            except Exception as e:
                import logging
                logger = logging.getLogger('function_calls')
                logger.error(f"Failed to queue status update email: {e}")

        elif action == 'mark_pending':
            report.status = 'pending'
            report.reviewed_by = None
            report.reviewed_at = None
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
            messages.success(request, f'Report "{report.title}" marked as pending.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='status_changed',
                details='Status changed back to pending'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} set Kai case #{report.id} back to pending',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'mark_pending'},
            )

        elif action == 'archive':
            report.status = 'archived'
            report.save(update_fields=['status'])
            messages.success(request, f'Report "{report.title}" archived.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='archived',
                details='Report manually archived'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} archived Kai case #{report.id}',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'archived'},
            )

        elif action == 'update_notes':
            report.chair_notes = request.POST.get('chair_notes', '')
            report.save(update_fields=['chair_notes'])
            messages.success(request, 'Notes updated successfully.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='notes_updated',
                details='Chair notes updated'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} updated chair notes on Kai case #{report.id}',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'update_notes'},
            )

        elif action == 'update_tags':
            # v3.16.3 — SECURITY: tags are a closed vocabulary, not free text.
            # This is the only write site for KaiReport.tags. A free-text tag
            # naming a member would be searchable, rendered on the list card
            # and exported in the CSV for every list-level reviewer, bypassing
            # the identity redaction entirely. See KaiReport.TAG_CHOICES.
            #
            # The form now posts checkboxes, so `rejected` should only ever be
            # non-empty for a hand-crafted POST or a stale form — report it
            # rather than silently dropping the value.
            submitted = request.POST.getlist('tags') or request.POST.get('tags', '')
            accepted, rejected = KaiReport.normalize_tags(submitted)
            if rejected:
                messages.error(
                    request,
                    'These tags are not in the allowed list and were not saved: '
                    + ', '.join(rejected)
                    + '. Tags are visible to every reviewer who can see the report list, '
                      'so they are restricted to a fixed vocabulary that carries no '
                      'personal information.'
                )
                return redirect('manage_kai_report', report_id=report.id)

            report.tags = accepted
            report.save(update_fields=['tags'])
            messages.success(request, 'Tags updated successfully.')

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=request.user,
                action='tags_updated',
                details=f'Tags updated to: {", ".join(report.get_tag_labels()) if report.tags else "none"}'
            )
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} updated tags on Kai case #{report.id}',
                request=request,
                object_type='KaiReport',
                object_id=report.id,
                object_repr=f'Case #{report.id}',
                metadata={'action': 'update_tags'},
            )

        elif action == 'update_deliberation':
            deliberation_outcome = request.POST.get('deliberation_outcome')
            committee_notes = request.POST.get('committee_notes', '')
            closed_by_accused = request.POST.get('closed_by_accused_request') == 'on'

            if deliberation_outcome:
                old_outcome = report.deliberation_outcome
                report.deliberation_outcome = deliberation_outcome
                report.committee_notes = committee_notes
                report.closed_by_accused_request = closed_by_accused

                # If minutes closed at accused's request, archive the report
                if closed_by_accused and deliberation_outcome == 'heard':
                    report.status = 'archived'
                    # Append closure note to committee notes if not already there
                    closure_note = "Minutes closed at the request of the accused."
                    if closure_note not in report.committee_notes:
                        if report.committee_notes:
                            report.committee_notes += f"\n\n{closure_note}"
                        else:
                            report.committee_notes = closure_note
                    messages.success(request, 'Deliberation outcome updated. Minutes closed and report archived.')
                else:
                    outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)
                    messages.success(request, f'Deliberation outcome updated to: {outcome_display}')

                report.save(update_fields=['deliberation_outcome', 'committee_notes', 'closed_by_accused_request', 'status'])

                # Log activity
                if old_outcome != deliberation_outcome:
                    outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='deliberation_updated',
                        details=f'Deliberation outcome changed to: {outcome_display}'
                    )

                if committee_notes:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='committee_notes_updated',
                        details='Committee notes added/updated'
                    )

                if closed_by_accused:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='minutes_closed',
                        details='Minutes closed at the request of the accused'
                    )

                ActivityLog.log_activity(
                    action_type='kai_action',
                    user=request.user,
                    description=f'{request.user.name} updated deliberation on Kai case #{report.id}',
                    request=request,
                    object_type='KaiReport',
                    object_id=report.id,
                    object_repr=f'Case #{report.id}',
                    metadata={'action': 'update_deliberation'},
                )

                # Send email notifications about outcome (ONLY to targeted person, NOT submitter)
                if old_outcome != deliberation_outcome and report.targeted_to and report.targeted_to.email:
                    try:
                        outcome_display = dict(report.DELIBERATION_CHOICES).get(deliberation_outcome)
                        message = None  # Initialize before conditional branches

                        # Notify targeted person about deliberation outcome
                        if deliberation_outcome == 'heard':
                            subject = 'Kai Committee Notification - Case Heard'
                            message = f"""
This is to inform you that a report has been submitted to the Kai Committee that involves you.

The Kai Committee has decided to hear this case and may reach out to you for further information.

If you have any questions, please contact the Kai Committee chair(s).

Updated at: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}
                            """
                        elif deliberation_outcome == 'thrown_out':
                            subject = 'Kai Committee Notification - Case Resolved'
                            message = f"""
This is to inform you that a report submitted to the Kai Committee that involved you has been resolved.

The case has been thrown out and no further action is required from you.

If you have any questions, please contact the Kai Committee chair(s).

Updated at: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}
                            """
                        elif deliberation_outcome == 'pending':
                            # Don't notify for pending status
                            message = None

                        if message:
                            send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, [report.targeted_to.email])
                    except Exception as e:
                        import logging
                        logger = logging.getLogger('function_calls')
                        logger.error(f"Failed to queue deliberation update email: {e}")
            else:
                messages.error(request, 'Please select a deliberation outcome.')

        elif action == 'notify_submitter':
            # Only allow if minutes are not closed
            if report.closed_by_accused_request:
                messages.error(request, 'Cannot notify submitter when minutes are closed.')
            else:
                # Send notification to submitter with deliberation outcome and notes
                try:
                    if report.submitted_by.email:
                        from django.core.mail import EmailMultiAlternatives
                        from django.urls import reverse
                        from django.utils.html import escape

                        outcome_display = dict(report.DELIBERATION_CHOICES).get(report.deliberation_outcome, 'Pending')
                        notify_time = localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')

                        subject = f'Kai Report Update: {report.title}'

                        # Plain text version
                        text_message = f"""
This is a notification regarding your Kai report submission.

Case Number: #{report.id}
Deliberation Outcome: {outcome_display}

Committee Notes:
{report.committee_notes if report.committee_notes else 'No additional notes provided.'}

If you have any questions, please contact the Kai Committee chair(s).

Notified at: {notify_time}
                        """

                        # Build tracking pixel URL
                        tracking_url = request.build_absolute_uri(
                            reverse('track_kai_submitter_email', kwargs={'report_id': report.id})
                        )

                        escaped_notes = escape(report.committee_notes or 'No additional notes provided.').replace('\n', '<br>')

                        # HTML version with tracking pixel
                        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2c5282 100%); padding: 30px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">Kai Committee Notification</h1>
        <p style="color: #a0c4e8; margin: 10px 0 0 0; font-size: 14px;">Case Update — Case #{report.id}</p>
    </div>

    <div style="background: #ffffff; padding: 30px; border: 1px solid #e2e8f0; border-top: none;">
        <p style="margin-top: 0;">This is a notification regarding your Kai report submission.</p>

        <div style="background: #f7fafc; border-left: 4px solid #4299e1; padding: 15px 20px; margin: 20px 0; border-radius: 0 4px 4px 0;">
            <p style="margin: 0;"><strong>Deliberation Outcome:</strong> {escape(outcome_display)}</p>
        </div>

        <h3 style="font-size: 16px; color: #2d3748;">Committee Notes</h3>
        <p style="margin: 0; white-space: pre-wrap;">{escaped_notes}</p>

        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 25px 0;">

        <p style="color: #718096; font-size: 12px; margin-bottom: 0;">
            If you have any questions, please contact the Kai Committee chair(s).<br>
            Notified at: {notify_time}<br>
            Kai Committee &bull; Beta Theta Pi - Samford Chapter
        </p>
    </div>

    <!-- Tracking pixel -->
    <img src="{tracking_url}" width="1" height="1" alt="" style="display:none;">
</body>
</html>
                        """

                        email = EmailMultiAlternatives(
                            subject=subject,
                            body=text_message,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=[report.submitted_by.email],
                        )
                        email.attach_alternative(html_message, "text/html")
                        email.send(fail_silently=False)

                        # Update report tracking fields — reset viewed on new send
                        report.submitter_notified_at = timezone.now()
                        report.submitter_email_viewed_at = None
                        report.save(update_fields=['submitter_notified_at', 'submitter_email_viewed_at'])

                        # Log activity
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='status_changed',
                            details=f'Submitter notified of deliberation outcome'
                        )
                        ActivityLog.log_activity(
                            action_type='kai_action',
                            user=request.user,
                            description=f'{request.user.name} notified submitter of Kai case #{report.id}',
                            request=request,
                            object_type='KaiReport',
                            object_id=report.id,
                            object_repr=f'Case #{report.id}',
                            metadata={'action': 'notify_submitter'},
                        )

                        messages.success(request, f'Submitter has been notified via email.')
                    else:
                        messages.warning(request, f'Submitter does not have an email address on file.')
                except Exception as e:
                    import logging
                    logger = logging.getLogger('function_calls')
                    logger.error(f"Failed to send submitter notification: {e}")
                    messages.error(request, f'Failed to send notification: {str(e)}')

        elif action == 'link_report':
            # Link a related report
            related_id = request.POST.get('related_report_id')
            if related_id:
                try:
                    related_report = KaiReport.objects.get(id=related_id)
                    report.related_reports.add(related_report)

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details=f'Linked to related report: {related_report.title} (#{related_report.id})'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} linked Kai case #{report.id} to case #{related_report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'link_report', 'linked_case_id': related_report.id},
                    )

                    messages.success(request, f'Linked to report: {related_report.title}')
                except KaiReport.DoesNotExist:
                    messages.error(request, 'Related report not found.')
            else:
                messages.error(request, 'No report selected.')

        elif action == 'unlink_report':
            # Unlink a related report
            related_id = request.POST.get('related_report_id')
            if related_id:
                try:
                    related_report = KaiReport.objects.get(id=related_id)
                    report.related_reports.remove(related_report)

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details=f'Unlinked from related report: {related_report.title} (#{related_report.id})'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} unlinked Kai case #{report.id} from case #{related_report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'unlink_report', 'unlinked_case_id': related_report.id},
                    )

                    messages.success(request, f'Unlinked from report: {related_report.title}')
                except KaiReport.DoesNotExist:
                    messages.error(request, 'Related report not found.')
            else:
                messages.error(request, 'No report selected.')

        elif action == 'update_accused':
            # Update or set the accused person
            accused_id = request.POST.get('accused_id', '').strip()
            accused_email = request.POST.get('accused_email', '').strip()

            if accused_id:
                try:
                    accused_user = ParliamentUser.objects.get(user_id=accused_id)
                    old_targeted = report.targeted_to
                    report.targeted_to = accused_user

                    # Update email if provided and different
                    if accused_email and accused_email != accused_user.email:
                        accused_user.email = accused_email
                        accused_user.save(update_fields=['email'])

                    report.save(update_fields=['targeted_to'])

                    # Log activity
                    if old_targeted != accused_user:
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='status_changed',
                            # v3.18.1: no name. The accused's identity is
                            # governed by `can_view_accused_identity` and this
                            # string is rendered to anyone with
                            # `can_view_report_details`. The name is on the
                            # case record itself, gated properly, and the log
                            # only needs to say that it changed.
                            details='Accused person set.'
                        )
                        ActivityLog.log_activity(
                            action_type='kai_action',
                            user=request.user,
                            description=f'{request.user.name} updated accused person on Kai case #{report.id}',
                            request=request,
                            object_type='KaiReport',
                            object_id=report.id,
                            object_repr=f'Case #{report.id}',
                            metadata={'action': 'update_accused'},
                        )

                    messages.success(request, f'Accused person updated to {accused_user.name}.')
                except ParliamentUser.DoesNotExist:
                    messages.error(request, 'Selected member not found.')
            else:
                # Clear the accused person
                if report.targeted_to:
                    old_name = report.targeted_to.name
                    report.targeted_to = None
                    report.save(update_fields=['targeted_to'])

                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        # v3.18.1: `old_name` removed — see the sibling above.
                        details='Accused person removed from the case.'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} removed accused person from Kai case #{report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'update_accused', 'cleared': True},
                    )
                    messages.success(request, 'Accused person removed from report.')

        elif action == 'notify_accused':
            # Notify the accused person of the case
            notification_message = request.POST.get('accused_notification_message', '').strip()

            if not report.targeted_to:
                messages.error(request, 'No accused person specified for this report.')
            elif not report.targeted_to.email:
                messages.error(request, f'{report.targeted_to.name} does not have an email address on file.')
            elif not notification_message:
                messages.error(request, 'Please enter a message explaining what the person is being reported for.')
            else:
                try:
                    from django.core.mail import EmailMultiAlternatives
                    from django.urls import reverse

                    subject = 'Kai Committee Notification - Case Filed'

                    # Plain text version
                    text_message = f"""
Dear {report.targeted_to.name},

This is an official notification from the Kai Committee of Beta Theta Pi.

A report has been filed with the Kai Committee that involves you. The details are as follows:

{notification_message}

The Kai Committee will review this matter and may contact you for further information or to schedule a hearing. You have the right to:
- Present your side of the story
- Bring witnesses or evidence in your defense
- Request that the minutes be closed (kept confidential)

If you have any questions or concerns, please contact the Kai Committee chair(s).

This notification was sent on {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}.

Kai Committee
Beta Theta Pi - Samford Chapter
                    """

                    # Build tracking pixel URL
                    tracking_url = request.build_absolute_uri(
                        reverse('track_kai_accused_email', kwargs={'report_id': report.id})
                    )

                    # Escape notification message for HTML
                    from django.utils.html import escape
                    escaped_message = escape(notification_message).replace('\n', '<br>')

                    # HTML version with tracking pixel
                    html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: linear-gradient(135deg, #1e3a5f 0%, #2c5282 100%); padding: 30px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0; font-size: 24px;">Kai Committee Notification</h1>
        <p style="color: #a0c4e8; margin: 10px 0 0 0; font-size: 14px;">Official Notice - Case Filed</p>
    </div>

    <div style="background: #ffffff; padding: 30px; border: 1px solid #e2e8f0; border-top: none;">
        <p style="margin-top: 0;">Dear <strong>{report.targeted_to.name}</strong>,</p>

        <p>This is an official notification from the Kai Committee of Beta Theta Pi.</p>

        <p>A report has been filed with the Kai Committee that involves you. The details are as follows:</p>

        <div style="background: #f7fafc; border-left: 4px solid #4299e1; padding: 15px 20px; margin: 20px 0; border-radius: 0 4px 4px 0;">
            <p style="margin: 0; white-space: pre-wrap;">{escaped_message}</p>
        </div>

        <p>The Kai Committee will review this matter and may contact you for further information or to schedule a hearing.</p>

        <div style="background: #ebf8ff; border: 1px solid #90cdf4; border-radius: 8px; padding: 20px; margin: 20px 0;">
            <h3 style="margin: 0 0 10px 0; color: #2b6cb0; font-size: 16px;">Your Rights</h3>
            <ul style="margin: 0; padding-left: 20px; color: #2c5282;">
                <li>Present your side of the story</li>
                <li>Bring witnesses or evidence in your defense</li>
                <li>Request that the minutes be closed (kept confidential)</li>
            </ul>
        </div>

        <p>If you have any questions or concerns, please contact the Kai Committee chair(s).</p>

        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 25px 0;">

        <p style="color: #718096; font-size: 12px; margin-bottom: 0;">
            This notification was sent on {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}.<br>
            Kai Committee &bull; Beta Theta Pi - Samford Chapter
        </p>
    </div>

    <!-- Tracking pixel -->
    <img src="{tracking_url}" width="1" height="1" alt="" style="display:none;">
</body>
</html>
                    """

                    # Send email with both plain text and HTML
                    email = EmailMultiAlternatives(
                        subject=subject,
                        body=text_message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[report.targeted_to.email]
                    )
                    email.attach_alternative(html_message, "text/html")
                    email.send(fail_silently=False)

                    # Update report - reset viewed status since new email sent
                    report.accused_notified = True
                    report.accused_notified_at = timezone.now()
                    report.accused_notification_message = notification_message
                    report.accused_email_viewed_at = None  # Reset on new notification
                    report.save(update_fields=['accused_notified', 'accused_notified_at', 'accused_notification_message', 'accused_email_viewed_at'])

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        # v3.18.1: name removed — see the two siblings above.
                        details='Accused notified of the case.'
                    )
                    ActivityLog.log_activity(
                        action_type='kai_action',
                        user=request.user,
                        description=f'{request.user.name} notified accused on Kai case #{report.id}',
                        request=request,
                        object_type='KaiReport',
                        object_id=report.id,
                        object_repr=f'Case #{report.id}',
                        metadata={'action': 'notify_accused'},
                    )

                    messages.success(request, f'{report.targeted_to.name} has been notified of the case via email.')

                except Exception as e:
                    import logging
                    logger = logging.getLogger('function_calls')
                    logger.error(f"Failed to send accused notification: {e}")
                    messages.error(request, f'Failed to send notification: {str(e)}')

        elif action == 'approve_closure':
            # Approve a closure request
            closure_request_id = request.POST.get('closure_request_id')
            review_notes = request.POST.get('review_notes', '').strip()

            if closure_request_id:
                try:
                    closure_request = KaiClosureRequest.objects.get(id=closure_request_id, report=report)
                    if closure_request.status == 'pending':
                        closure_request.status = 'approved'
                        closure_request.reviewed_by = request.user
                        closure_request.reviewed_at = timezone.now()
                        closure_request.review_notes = review_notes
                        closure_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])

                        # Archive the report
                        report.status = 'archived'
                        report.save(update_fields=['status'])

                        # Log activity
                        KaiReportActivity.objects.create(
                            report=report,
                            user=request.user,
                            action='closure_approved',
                            details=f'Closure request approved. Report archived.'
                        )
                        ActivityLog.log_activity(
                            action_type='kai_action',
                            user=request.user,
                            description=f'{request.user.name} approved closure request on Kai case #{report.id}',
                            request=request,
                            object_type='KaiReport',
                            object_id=report.id,
                            object_repr=f'Case #{report.id}',
                            metadata={'action': 'approve_closure'},
                        )

                        # Notify the requester
                        if closure_request.requested_by.email:
                            send_email.delay(
                                f'[Kai] Closure Request Approved: {report.title}',
                                f"""Your closure request has been approved.

Report: {report.title}
Decision: Approved
{f"Notes: {review_notes}" if review_notes else ""}

The case has been archived.
""",
                                settings.DEFAULT_FROM_EMAIL,
                                [closure_request.requested_by.email],
                            )

                        messages.success(request, 'Closure request approved. Report has been archived.')
                    else:
                        messages.warning(request, 'This closure request has already been processed.')
                except KaiClosureRequest.DoesNotExist:
                    messages.error(request, 'Closure request not found.')

        elif action == 'deny_closure':
            # Deny a closure request
            closure_request_id = request.POST.get('closure_request_id')
            review_notes = request.POST.get('review_notes', '').strip()

            if closure_request_id:
                try:
                    closure_request = KaiClosureRequest.objects.get(id=closure_request_id, report=report)
                    if closure_request.status == 'pending':
                        if not review_notes:
                            messages.error(request, 'Please provide a reason for denying the closure request.')
                        else:
                            closure_request.status = 'denied'
                            closure_request.reviewed_by = request.user
                            closure_request.reviewed_at = timezone.now()
                            closure_request.review_notes = review_notes
                            closure_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])

                            # Log activity
                            KaiReportActivity.objects.create(
                                report=report,
                                user=request.user,
                                action='closure_denied',
                                details=f'Closure request denied. Reason: {review_notes[:100]}...' if len(review_notes) > 100 else f'Closure request denied. Reason: {review_notes}'
                            )
                            ActivityLog.log_activity(
                                action_type='kai_action',
                                user=request.user,
                                description=f'{request.user.name} denied closure request on Kai case #{report.id}',
                                request=request,
                                object_type='KaiReport',
                                object_id=report.id,
                                object_repr=f'Case #{report.id}',
                                metadata={'action': 'deny_closure'},
                            )

                            # Notify the requester
                            if closure_request.requested_by.email:
                                send_email.delay(
                                    f'[Kai] Closure Request Denied: {report.title}',
                                    f"""Your closure request has been denied.

Report: {report.title}
Decision: Denied
Reason: {review_notes}

You may submit another closure request in the future if circumstances change.
""",
                                    settings.DEFAULT_FROM_EMAIL,
                                    [closure_request.requested_by.email],
                                )

                            messages.success(request, 'Closure request denied.')
                    else:
                        messages.warning(request, 'This closure request has already been processed.')
                except KaiClosureRequest.DoesNotExist:
                    messages.error(request, 'Closure request not found.')

        return redirect('manage_kai_report', report_id=report.id)

    # Get activity log
    #
    # v3.18.1: redacted before it reaches either template. Both the Activity
    # card and the Case Timeline printed the entry author's name and the raw
    # details string, and the author of the `created` entry is the submitter.
    # See `_redact_activity_log`.
    try:
        activity_log = _redact_activity_log(
            list(
                report.activity_log.all()
                .select_related('user')
                .defer(*member_defer('user'))[:20]  # Last 20 activities
            ),
            report,
            kai_access,
        )
    except DatabaseError:
        activity_log = []

    # Get related reports
    try:
        related_reports = list(report.related_reports.all().select_related('submitted_by', 'targeted_to').defer(*member_defer('submitted_by', 'targeted_to')))
    except Exception:
        related_reports = []

    # Get available reports to link (excluding current report and already linked ones)
    try:
        available_reports = KaiReport.objects.exclude(id=report.id).exclude(id__in=[r.id for r in related_reports]).select_related('submitted_by', 'targeted_to').defer(*member_defer('submitted_by', 'targeted_to')).order_by('-submitted_at')[:20]
    except Exception:
        available_reports = []

    # Get all members for accused person selection (active first, then inactive/alumni)
    try:
        all_members = ParliamentUser.objects.exclude(member_status='Removed').order_by('member_status', 'name')
    except Exception:
        all_members = ParliamentUser.objects.exclude(member_status='Removed').order_by('name')

    # Get pending closure requests for this report
    try:
        closure_requests = list(report.closure_requests.all().select_related('requested_by', 'reviewed_by').defer(*member_defer('requested_by', 'reviewed_by')).order_by('-requested_at'))
    except Exception:
        closure_requests = []

    # Get custom field responses
    try:
        custom_responses = list(report.custom_responses.all().select_related('field'))
    except Exception:
        custom_responses = []

    # v3.18.0 — recusal seats. Idempotent; records the vacancy so it can be
    # filled and shown in the minutes. Enforcement is independent of these rows.
    _sync_recusals(report, kai_committee)
    recusals = list(
        report.recusals.select_related('user', 'replacement')
        .defer(*member_defer('user', 'replacement'))
    )
    can_appoint = _can_appoint_standins(request.user, kai_committee)
    eligible_standins = (
        list(KaiRecusal.eligible_standins(report)) if can_appoint else []
    )
    recusable_members = (
        list(
            ParliamentUser.objects
            .filter(pk__in=list(kai_committee.members.values_list('pk', flat=True))
                            + list(kai_committee.chairs.values_list('pk', flat=True)))
            .exclude(pk__in=[r.user_id for r in recusals])
            .exclude(pk__in=[pk for pk in (report.submitted_by_id, report.targeted_to_id) if pk])
            .order_by('name')
        )
        if can_appoint else []
    )
    assignable_members = (
        list(
            kai_committee.members.filter(member_status='Active')
            .exclude(pk__in=[pk for pk in (report.submitted_by_id, report.targeted_to_id) if pk])
            .order_by('name')
        )
        if kai_access['can_edit_open_cases'] else []
    )

    context = {
        'report': report,
        'kai_committee': kai_committee,
        'activity_log': activity_log,
        'related_reports': related_reports,
        'available_reports': available_reports,
        'all_members': all_members,
        'closure_requests': closure_requests,
        'custom_responses': custom_responses,
        'kai_access': kai_access,
        # v3.16.3: the tag editor is a checkbox list over the closed vocabulary
        # rather than a free-text box. `selected_tags` includes any legacy
        # out-of-vocabulary value still on the record so the checkbox state is
        # honest; `legacy_tags` drives the warning banner.
        'tag_choices': KaiReport.TAG_CHOICES,
        'selected_tags': report.get_tags_list(),
        'legacy_tags': [t for t in report.get_tags_list() if t not in KaiReport.ALLOWED_TAGS],

        # v3.18.0 — recusal seats (bylaws §§ vi-ix), aging, assignment, appeals.
        # `_sync_recusals` materialises a row for any committee member who is a
        # party, so the chair has something to appoint against. Enforcement does
        # not depend on these rows — see `_case_access`.
        'recusals': recusals,
        'can_appoint_standins': can_appoint,
        'eligible_standins': eligible_standins,
        'recusable_members': recusable_members,
        'assignable_members': assignable_members,
        'appeals': report.appeals.select_related('filed_by').defer(*member_defer('filed_by')),
        'appeal_window_days': KaiAppeal.APPEAL_WINDOW_DAYS,
        'appeal_days_remaining': KaiAppeal.days_remaining(report),
    }

    return render(request, 'kai/manage_report.html', context)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def print_kai_report(request, report_id):
    """Print-friendly view for a Kai report (can be printed to PDF)"""
    # Check if KaiReport table exists
    try:
        report = get_object_or_404(KaiReport, id=report_id)
    except Exception:
        messages.warning(request, 'Kai Reports feature is not yet set up. Please run database migrations.')
        return redirect('home')

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    # v3.18.0 — RECUSAL. The print view is the fifth surface; a recused member
    # must not be able to route around the detail page by printing it.
    kai_access = _case_access(request.user, report, kai_access)
    if kai_access.get('is_recused'):
        messages.error(request, 'You are recused from this case. Chapter bylaws § vi.')
        return redirect('view_kai_reports')
    if not kai_access['can_view_report_details']:
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('home')

    # Get activity log
    #
    # v3.18.1 — redacted, same as the detail page. This view is the third copy
    # of the activity feed and the one that matters most: it renders the whole
    # log rather than the last 20, and its output is a document that leaves the
    # app. See `_redact_activity_log`.
    try:
        activity_log = _redact_activity_log(
            list(
                report.activity_log.all()
                .select_related('user')
                .defer(*member_defer('user'))
            ),
            report,
            kai_access,
        )
    except DatabaseError:
        activity_log = []

    ActivityLog.log_activity(
        action_type='kai_action',
        user=request.user,
        description=f'{request.user.name} printed/exported Kai case #{report.id}',
        request=request,
        object_type='KaiReport',
        object_id=report.id,
        object_repr=f'Case #{report.id}',
        metadata={'action': 'print_report'},
    )

    context = {
        'report': report,
        'kai_committee': kai_committee,
        'activity_log': activity_log,
        'print_date': timezone.now(),
        # v3.18.1 — this template never received `kai_access`, which is why it
        # printed both parties' names ungated: there was nothing to gate on.
        # Every other Kai surface has had it since v3.16.2.
        'kai_access': kai_access,
    }

    return render(request, 'kai/print_report.html', context)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def kai_dashboard(request):
    """Redirects to the consolidated Kai reports page (dashboard merged in)."""
    return redirect('view_kai_reports')


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def bulk_actions_kai_reports(request):
    """Handle bulk actions on multiple Kai reports"""
    if request.method != 'POST':
        return redirect('view_kai_reports')

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    kai_access = _get_kai_access(request.user, kai_committee)
    if not kai_access['can_view_report_list']:
        messages.error(request, 'You do not have permission to perform bulk actions.')
        return redirect('home')

    # Get selected report IDs and action
    report_ids = request.POST.getlist('report_ids')
    action = request.POST.get('bulk_action')

    # Action-level permission check
    if action in ('mark_reviewed', 'mark_pending') and not kai_access['can_edit_open_cases']:
        messages.error(request, 'You do not have permission to edit cases.')
        return redirect('view_kai_reports')
    if action == 'archive' and not kai_access['can_close_cases']:
        messages.error(request, 'You do not have permission to close cases.')
        return redirect('view_kai_reports')
    # v3.17.7 — WHY `export_csv` HAS NO EXTRA ACTION-LEVEL GATE.
    # The two checks above guard *write* actions with write permissions.
    # Exporting is a read, and after this release its read scope is exactly the
    # list view's scope with the three detail fields redacted per-permission by
    # `_kai_csv_row` — identical to `export_kai_reports_csv`, which has gated on
    # `can_view_report_list` alone since it was written. Gating the two exports
    # differently is how they drifted apart in the first place, so they are
    # deliberately kept at parity. If exporting should ever require more than
    # viewing, change BOTH and say so here.

    if not report_ids:
        messages.warning(request, 'No reports selected.')
        return redirect('view_kai_reports')

    if not action:
        messages.warning(request, 'No action selected.')
        return redirect('view_kai_reports')

    try:
        # Get the reports
        # v3.18.0 — RECUSAL: a recused member must not act on their own case
        # via a bulk action either. Excluded from the queryset, so the case is
        # simply not among those the action applies to.
        reports = (
            KaiReport.objects
            .filter(id__in=report_ids)
            .exclude(pk__in=_recused_case_ids(request.user))
        )

        # ⚠️ …and the same is true of a case the caller FILED, for the write
        # actions. `manage_kai_report` narrows per-case through `_case_access`;
        # this endpoint gates on the committee-level `kai_access` above, so
        # without this the submitter was refused on the detail page and allowed
        # here. Found by `test_the_submitter_cannot_archive_the_case_he_filed`
        # the moment the submitter rule was corrected — the second-copy pattern
        # again, in the same file it was found in on 07-31-26.
        if action in ('mark_reviewed', 'mark_pending', 'archive'):
            reports = reports.exclude(submitted_by=request.user)
        count = reports.count()

        if action == 'mark_reviewed':
            # Mark all as reviewed
            for report in reports:
                if report.status != 'reviewed':
                    report.mark_as_reviewed(request.user)

                    # Log activity
                    KaiReportActivity.objects.create(
                        report=report,
                        user=request.user,
                        action='status_changed',
                        details='Bulk action: Status changed to reviewed'
                    )

            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} bulk marked {count} Kai case(s) as reviewed',
                request=request,
                object_type='KaiReport',
                metadata={'action': 'bulk_mark_reviewed', 'count': count},
            )
            messages.success(request, f'{count} report(s) marked as reviewed.')

        elif action == 'archive':
            # Archive all
            updated = reports.update(status='archived')

            # Log activity for each
            for report in reports:
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='archived',
                    details='Bulk action: Report archived'
                )

            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} bulk archived {updated} Kai case(s)',
                request=request,
                object_type='KaiReport',
                metadata={'action': 'bulk_archive', 'count': updated},
            )
            messages.success(request, f'{updated} report(s) archived.')

        elif action == 'mark_pending':
            # Mark all as pending
            updated = reports.update(status='pending', reviewed_by=None, reviewed_at=None)

            # Log activity for each
            for report in reports:
                KaiReportActivity.objects.create(
                    report=report,
                    user=request.user,
                    action='status_changed',
                    details='Bulk action: Status changed to pending'
                )

            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'{request.user.name} bulk marked {updated} Kai case(s) as pending',
                request=request,
                object_type='KaiReport',
                metadata={'action': 'bulk_mark_pending', 'count': updated},
            )
            messages.success(request, f'{updated} report(s) marked as pending.')

        elif action == 'export_csv':
            # Export selected reports to CSV.
            #
            # v3.17.7: this branch used to write its own header list and its own
            # row, with NO redaction of Submitted By / Targeted To / Description
            # — a verbatim copy of what `export_kai_reports_csv` looked like
            # before v3.16.2, sitting ~1,100 lines below the comment explaining
            # why that was wrong. It now shares that view's row builder, so the
            # redaction rule has exactly one definition. See KAI_CSV_HEADERS.
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="selected_kai_reports_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'

            writer = csv.writer(response)
            writer.writerow(KAI_CSV_HEADERS)

            for report in reports.select_related('submitted_by', 'reviewed_by', 'targeted_to').defer(*member_defer('submitted_by', 'reviewed_by', 'targeted_to')):
                writer.writerow(_kai_csv_row(report, kai_access))

            return response

        else:
            messages.error(request, 'Invalid action selected.')

    except Exception as e:
        # v3.17.7: was `f'Error performing bulk action: {str(e)}'`. Raw
        # exception text in a user-facing message leaks paths, column names and
        # query fragments — same shape as the 07-28 CSV export leak and the two
        # `str(e)` removals v3.17.5 made in changelog.py. Log it, show a
        # generic message.
        logger.exception('Bulk Kai action %r failed for user %s', action, request.user.user_id)
        messages.error(request, 'Something went wrong performing that bulk action.')

    return redirect('view_kai_reports')


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def manage_kai_templates(request):
    """Manage Kai report templates (for chairs only)"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can manage templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    templates = KaiReportTemplate.objects.all()

    context = {
        'templates': templates,
        'kai_committee': kai_committee,
    }

    return render(request, 'kai/manage_templates.html', context)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def create_kai_template(request):
    """Create a new Kai report template"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can create templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description')
        category = request.POST.get('category')
        title_template = request.POST.get('title_template')
        description_template = request.POST.get('description_template')
        # v3.16.3: same closed vocabulary as KaiReport.tags — suggested_tags
        # feeds that field, so free text here would be a side door.
        suggested_tags, rejected_tags = KaiReport.normalize_tags(
            request.POST.getlist('suggested_tags') or request.POST.get('suggested_tags', '')
        )
        is_active = request.POST.get('is_active') == 'on'

        if rejected_tags:
            messages.error(
                request,
                'These suggested tags are not in the allowed list: ' + ', '.join(rejected_tags)
                + '. Template not created.'
            )
        elif name and description and category and title_template and description_template:
            template = KaiReportTemplate.objects.create(
                name=name,
                description=description,
                category=category,
                title_template=title_template,
                description_template=description_template,
                suggested_tags=suggested_tags,
                is_active=is_active,
                created_by=request.user
            )
            messages.success(request, f'Template "{template.name}" created successfully.')
            return redirect('manage_kai_templates')
        else:
            messages.error(request, 'Please fill in all required fields.')

    context = {
        'kai_committee': kai_committee,
        'category_choices': KaiReport.CATEGORY_CHOICES,
        'tag_choices': KaiReport.TAG_CHOICES,
    }

    return render(request, 'kai/create_template.html', context)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def edit_kai_template(request, template_id):
    """Edit an existing Kai report template"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can edit templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    template = get_object_or_404(KaiReportTemplate, id=template_id)

    if request.method == 'POST':
        template.name = request.POST.get('name')
        template.description = request.POST.get('description')
        template.category = request.POST.get('category')
        template.title_template = request.POST.get('title_template')
        template.description_template = request.POST.get('description_template')
        # v3.16.3: closed vocabulary — see KaiReport.TAG_CHOICES.
        accepted_tags, rejected_tags = KaiReport.normalize_tags(
            request.POST.getlist('suggested_tags') or request.POST.get('suggested_tags', '')
        )
        if rejected_tags:
            messages.error(
                request,
                'These suggested tags are not in the allowed list: ' + ', '.join(rejected_tags)
                + '. No changes were saved.'
            )
            return redirect('edit_kai_template', template_id=template.id)

        template.suggested_tags = accepted_tags
        template.is_active = request.POST.get('is_active') == 'on'
        template.save(update_fields=['name', 'description', 'category', 'title_template', 'description_template', 'suggested_tags', 'is_active'])

        messages.success(request, f'Template "{template.name}" updated successfully.')
        return redirect('manage_kai_templates')

    context = {
        'template': template,
        'kai_committee': kai_committee,
        'category_choices': KaiReport.CATEGORY_CHOICES,
        'tag_choices': KaiReport.TAG_CHOICES,
    }

    return render(request, 'kai/edit_template.html', context)


@login_required
@require_feature_flag('kai_reports')
def delete_kai_template(request, template_id):
    """Delete a Kai report template"""
    # Check if user is a Kai chair or admin
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        if not (kai_committee.is_chair(request.user) or request.user.is_admin):
            messages.error(request, 'Only Kai chairs can delete templates.')
            return redirect('home')
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    template = get_object_or_404(KaiReportTemplate, id=template_id)
    template_name = template.name
    template.delete()

    messages.success(request, f'Template "{template_name}" deleted successfully.')
    return redirect('manage_kai_templates')


def track_kai_accused_email_view(request, report_id):
    """
    Track when an accused person views their notification email.
    Returns a 1x1 transparent pixel.
    This view does not require login since it's loaded as an image in emails.
    """
    import base64
    import logging

    logger = logging.getLogger('function_calls')

    # 1x1 transparent GIF
    PIXEL_GIF = base64.b64decode(
        'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
    )

    try:
        report = KaiReport.objects.get(id=report_id)
        logger.info(f"Kai email tracking pixel accessed for report {report_id}")

        # Only update if notified and not already viewed
        if report.accused_notified:
            # Check if already viewed
            current_viewed = getattr(report, 'accused_email_viewed_at', None)
            if not current_viewed:
                report.accused_email_viewed_at = timezone.now()
                report.save(update_fields=['accused_email_viewed_at'])
                logger.info(f"Marked Kai report {report_id} accused email as viewed")

                # Log the view in activity log
                try:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=None,  # System action
                        action='status_changed',
                        details='Accused person viewed notification email'
                    )
                except Exception as e:
                    logger.error(f"Failed to log activity for report {report_id}: {e}")
    except KaiReport.DoesNotExist:
        logger.warning(f"Kai email tracking: Report {report_id} not found")
    except Exception as e:
        logger.error(f"Kai email tracking error for report {report_id}: {e}")

    return HttpResponse(PIXEL_GIF, content_type='image/gif')


def track_kai_submitter_email_view(request, report_id):
    """
    Track when a submitter views their outcome notification email.
    Returns a 1x1 transparent pixel.
    This view does not require login since it's loaded as an image in emails.
    """
    import base64
    import logging

    logger = logging.getLogger('function_calls')

    # 1x1 transparent GIF
    PIXEL_GIF = base64.b64decode(
        'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
    )

    try:
        report = KaiReport.objects.get(id=report_id)
        logger.info(f"Kai submitter email tracking pixel accessed for report {report_id}")

        # Only update if notified and not already viewed
        if report.submitter_notified_at:
            if not report.submitter_email_viewed_at:
                report.submitter_email_viewed_at = timezone.now()
                report.save(update_fields=['submitter_email_viewed_at'])
                logger.info(f"Marked Kai report {report_id} submitter email as viewed")

                try:
                    KaiReportActivity.objects.create(
                        report=report,
                        user=None,  # System action
                        action='status_changed',
                        details='Submitter viewed outcome notification email'
                    )
                except Exception as e:
                    logger.error(f"Failed to log activity for report {report_id}: {e}")
    except KaiReport.DoesNotExist:
        logger.warning(f"Kai submitter email tracking: Report {report_id} not found")
    except Exception as e:
        logger.error(f"Kai submitter email tracking error for report {report_id}: {e}")

    return HttpResponse(PIXEL_GIF, content_type='image/gif')


# ═══════════════════════════════════════════════════════════════════════════
#  Recusal stand-ins — bylaws §§ vi–ix (v3.18.0)
# ═══════════════════════════════════════════════════════════════════════════
#
#     "vi. Should members of the Kai Committee be recused from their duties,
#      the head of Kai shall appoint suitable replacement(s) for the position."
#     "vii. Should the VP of Risk Management be unable to fill the vacancy, a
#      suitable replacement member will be appointed by the head of Kai."
#
# Recusal itself is automatic and computed from the case (`_case_access`).
# These views are the other half: recording that a seat was vacated and letting
# the head of Kai fill it, so the minutes can show the committee was properly
# constituted when it decided.


def _is_recused_from(report, user):
    """
    True if `user` is recused from `report` for ANY reason — party or manual.

    Stops a recused member acting on the case at all, including appointing
    stand-ins and recusing other people. Mason 07-31-26: someone who has stood
    themselves back "will not be able to do any other actions or recuse others."
    """
    if report is None or not getattr(user, 'pk', None):
        return False
    if report.recusal_reason(user) == 'accused':
        return True
    return KaiRecusal.objects.filter(
        report=report, user=user, reason__in=KaiRecusal.MANUAL_REASONS,
    ).exists()


def _can_appoint_standins(user, committee):
    """
    Only the head of Kai — or a site admin — may appoint a stand-in.

    Deliberately NOT a `KaiMemberPermission` flag. Appointment hands another
    member access to a specific case, so it is a delegation of authority rather
    than an ordinary case action; § vi assigns it to the head of Kai by name.

    v3.18.1: `_is_kai_chair`, not `committee.is_chair` — appointing a stand-in
    is the most privileged action in the module (it grants another member a
    snapshot of a seat's permissions on a live case), so it is the last place
    the `is_exec_board` shortcut belongs. See `_is_kai_chair`.
    """
    return bool(user.is_admin or _is_kai_chair(user, committee))


def _sync_recusals(report, committee):
    """
    Ensure a `KaiRecusal` row exists for every committee member who is a party.

    Recusal is *enforced* by `_case_access` whether or not a row exists — this
    only materialises the record so the seat, and its replacement, are visible
    and auditable. Idempotent; safe to call on every case load.

    Only committee members are recorded. An ordinary member who is the accused
    holds no seat, so there is nothing to vacate and nothing to fill.
    """
    # ⚠️ CORRECTED 07-31-26: this used to record BOTH parties. Only the accused
    # vacates a seat. A submitter keeps their seat and merely loses the power to
    # decide the case they filed (see `_case_access`), so recording a recusal
    # for them would show a "vacant seat" needing a stand-in that is not vacant.
    accused_id = report.targeted_to_id
    # A self-report vacates no seat — the member is not adjudicating anything
    # they could not already see, and there is no stand-in to appoint.
    if not accused_id or accused_id == report.submitted_by_id:
        return
    seated = (
        committee.members.filter(pk=accused_id).exists()
        or committee.chairs.filter(pk=accused_id).exists()
    )
    if seated:
        KaiRecusal.objects.get_or_create(
            report=report, user_id=accused_id, defaults={'reason': 'accused'},
        )


def _apply_standin(recusal, replacement, committee, actor):
    """
    Put `replacement` in `recusal`'s seat, snapshotting that seat's permissions.

    Shared by the single appointment view and the combined recuse-and-replace
    form, so the snapshot rule has one definition — the same reason
    `_kai_csv_row` exists. See `KaiRecusal.granted_permissions` for why it is a
    snapshot and not a live lookup.
    """
    seat = KaiMemberPermission.objects.filter(
        committee=committee, user=recusal.user).first()
    if seat is not None:
        grant = {field: getattr(seat, field) for field in _KAI_PERMISSION_FIELDS}
    else:
        # The recused member held a chair/admin seat with no explicit row, so
        # the seat carried full access. Grant that — capped, structurally, by
        # the fact that only a chair or admin can reach either caller.
        grant = {field: True for field in _KAI_PERMISSION_FIELDS}

    recusal.replacement = replacement
    recusal.granted_permissions = grant
    recusal.recorded_by = actor
    recusal.appointed_at = timezone.now()
    recusal.save()
    return recusal


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def appoint_kai_standin(request, report_id):
    """Appoint a member to fill a recused seat on one case."""
    if request.method != 'POST':
        return redirect('manage_kai_report', report_id=report_id)

    report = get_object_or_404(KaiReport, id=report_id)
    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    # A recused member must not appoint their own replacement — and since
    # 07-31-26 that includes a member who recused themselves by hand.
    if _is_recused_from(report, request.user):
        messages.error(request, 'You are recused from this case and cannot appoint a stand-in.')
        return redirect('view_kai_reports')

    if not _can_appoint_standins(request.user, committee):
        messages.error(request, 'Only the head of Kai may appoint a stand-in.')
        return redirect('manage_kai_report', report_id=report.id)

    recusal = get_object_or_404(KaiRecusal, id=request.POST.get('recusal_id'), report=report)
    replacement = get_object_or_404(ParliamentUser, pk=request.POST.get('replacement'))

    # Re-check eligibility server-side. The <select> is built from the same
    # queryset, but a POST is not a form — never trust the option list.
    if not recusal.is_eligible_replacement(replacement):
        messages.error(
            request,
            f'{replacement.name} cannot stand in on this case. Stand-ins must be '
            f'active members or advisors who are not already involved in it.',
        )
        return redirect('manage_kai_report', report_id=report.id)

    _apply_standin(recusal, replacement, committee, request.user)

    KaiReportActivity.objects.create(
        report=report,
        user=request.user,
        action='standin_appointed',
        details=f'{replacement.name} appointed to stand in for {recusal.user.name} '
                f'({recusal.get_reason_display().lower()}).',
    )
    ActivityLog.log_activity(
        action_type='kai_action',
        user=request.user,
        # v3.18.2 — no names. A stand-in is appointed to fill a seat vacated by
        # a recusal, and the commonest recusal reason is `accused`, so naming
        # who was replaced names the accused. Naming the replacement is
        # harmless on its own but pointless once the other half is gone; the
        # pks are in `metadata` for the audit trail either way.
        description=f'A Kai stand-in was appointed on case {report.display_number}',
        request=request,
        object_type='KaiRecusal',
        metadata={
            'report_id': report.id,
            'replacement': str(replacement.pk),
            'actor': str(request.user.pk),
        },
    )
    messages.success(request, f'{replacement.name} is now standing in on this case.')
    return redirect('manage_kai_report', report_id=report.id)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def remove_kai_standin(request, report_id):
    """Withdraw a stand-in appointment, leaving the seat vacant again."""
    if request.method != 'POST':
        return redirect('manage_kai_report', report_id=report_id)

    report = get_object_or_404(KaiReport, id=report_id)
    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None or _is_recused_from(report, request.user) \
            or not _can_appoint_standins(request.user, committee):
        messages.error(request, 'Only the head of Kai may withdraw a stand-in.')
        return redirect('view_kai_reports')

    recusal = get_object_or_404(KaiRecusal, id=request.POST.get('recusal_id'), report=report)
    former = recusal.replacement
    if former is None:
        return redirect('manage_kai_report', report_id=report.id)

    recusal.replacement = None
    recusal.granted_permissions = {}
    recusal.appointed_at = None
    recusal.save()

    KaiReportActivity.objects.create(
        report=report,
        user=request.user,
        action='standin_removed',
        details=f'{former.name} withdrawn as stand-in for {recusal.user.name}.',
    )
    messages.success(request, f'{former.name} is no longer standing in on this case.')
    return redirect('manage_kai_report', report_id=report.id)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def recuse_kai_member(request, report_id):
    """
    Recuse a committee member from one case by hand — bylaws §§ vi–ix.

    v3.18.0. `_sync_recusals` records the seats the *case* vacates: the accused.
    It cannot see the other reason a seat needs filling — the holder is simply
    **not available**. Travelling, ill, or standing back from one case for a
    reason the data does not know about. § vi covers both ("should members of
    the Kai Committee be recused from their duties…"); only one of them is
    computable.

    `accused` and `submitter` are NOT offered here. Those are derived from the
    case itself, so letting someone assert them by hand would record a
    relationship the data contradicts — and, worse, `_case_access` would ignore
    it, so the record and the enforcement would disagree.
    """
    if request.method != 'POST':
        return redirect('manage_kai_report', report_id=report_id)

    report = get_object_or_404(KaiReport, id=report_id)
    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    if _is_recused_from(report, request.user) \
            or not _can_appoint_standins(request.user, committee):
        messages.error(request, 'Only the head of Kai may recuse a member.')
        return redirect('view_kai_reports')

    # v3.18.0: several at once. A committee reshuffling for one case usually
    # moves more than one seat, and doing it one at a time meant re-opening the
    # form per person — with the added trap that recusing YOURSELF first would
    # lock you out before you could recuse anyone else.
    # v3.18.0 — one row per seat: who steps back, why, and who fills it.
    #
    # The form posts three parallel lists, one entry per row. A `<select>`
    # always submits a value (the placeholder posts ''), so the lists stay
    # aligned; a row with a blank member is an untouched "Add another" row.
    member_pks = request.POST.getlist('member')
    reasons = request.POST.getlist('reason')
    replacements = request.POST.getlist('replacement')
    notes = (request.POST.get('notes') or '').strip()

    rows = []
    for i, member_pk in enumerate(member_pks):
        if not member_pk:
            continue
        reason = reasons[i] if i < len(reasons) else 'unavailable'
        if reason not in KaiRecusal.MANUAL_REASONS:
            reason = 'unavailable'
        rows.append((member_pk, reason,
                     replacements[i] if i < len(replacements) else ''))

    if not rows:
        messages.error(request, 'Select at least one member to recuse.')
        return redirect('manage_kai_report', report_id=report.id)

    by_pk = {
        str(u.pk): u for u in ParliamentUser.objects.filter(
            pk__in=[r[0] for r in rows] + [r[2] for r in rows if r[2]])
    }
    recused, skipped, filled = [], [], []

    # ⚠️ SELF LAST. Recusing yourself withdraws every permission on this case,
    # including the one being exercised right now — so if `request.user` is in
    # the batch and is processed first, the remaining names are silently
    # dropped. Sorting them to the end means the whole batch lands, and the
    # lockout takes effect on the next request.
    rows.sort(key=lambda r: r[0] == str(request.user.pk))

    for member_pk, reason, replacement_pk in rows:
        member = by_pk.get(member_pk)
        if member is None:
            continue
        if report.is_party(member):
            skipped.append(f'{member.name} (already recused as a named party)')
            continue
        recusal, created = KaiRecusal.objects.get_or_create(
            report=report, user=member,
            defaults={'reason': reason, 'notes': notes, 'recorded_by': request.user},
        )
        if not created:
            skipped.append(f'{member.name} (already recused)')
            continue
        recused.append(member)
        KaiReportActivity.objects.create(
            report=report, user=request.user, action='standin_appointed',
            details=f'{member.name} recused '
                    f'({dict(KaiRecusal.REASON_CHOICES)[reason].lower()}).',
        )

        # …and fill the seat in the same step, if a replacement was chosen.
        replacement = by_pk.get(replacement_pk) if replacement_pk else None
        if replacement is None:
            continue
        if not recusal.is_eligible_replacement(replacement):
            skipped.append(f'{replacement.name} cannot stand in for {member.name}')
            continue
        _apply_standin(recusal, replacement, committee, request.user)
        filled.append(f'{replacement.name} for {member.name}')
        KaiReportActivity.objects.create(
            report=report, user=request.user, action='standin_appointed',
            details=f'{replacement.name} appointed to stand in for {member.name}.',
        )

    if recused:
        ActivityLog.log_activity(
            action_type='kai_action', user=request.user,
            # v3.18.2 — no names. `_sync_recusals` auto-recuses the accused
            # whenever they hold a seat, so this list routinely contained them,
            # next to the case number, on a page every officer can read. The
            # count says everything the audit trail needs; the pks are in
            # `metadata`, which no surface renders.
            description=(
                f'{len(recused)} Kai committee member(s) recused from case '
                f'{report.display_number}'
            ),
            request=request, object_type='KaiRecusal',
            metadata={'report_id': report.id,
                      'members': [str(m.pk) for m in recused], 'reason': reason,
                      'actor': str(request.user.pk)},
        )
        names = ', '.join(m.name for m in recused)
        if any(m.pk == request.user.pk for m in recused):
            messages.warning(
                request,
                f'{names} recused. You recused yourself from this case, so you can '
                f'no longer view or act on it — including appointing stand-ins. '
                f'Another chair or an admin must fill the remaining seats.',
            )
        else:
            messages.success(
                request,
                f'{names} recused from this case. Appoint stand-ins to fill the seats.',
            )
    if filled:
        messages.success(request, 'Standing in: ' + '; '.join(filled) + '.')
    for note in skipped:
        messages.info(request, f'Skipped {note}.')
    return redirect('manage_kai_report', report_id=report.id)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def end_kai_recusal(request, report_id):
    """
    Undo a MANUAL recusal — the member is available again.

    Only manual reasons can be ended. `accused` and `submitter` are computed
    from the case by `_case_access`, so deleting such a row would change the
    record without changing the access, leaving the two disagreeing. The button
    is not offered for those, and this refuses them if one is posted anyway.
    """
    if request.method != 'POST':
        return redirect('manage_kai_report', report_id=report_id)

    report = get_object_or_404(KaiReport, id=report_id)
    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None or _is_recused_from(report, request.user) \
            or not _can_appoint_standins(request.user, committee):
        messages.error(request, 'Only the head of Kai may end a recusal.')
        return redirect('view_kai_reports')

    recusal = get_object_or_404(KaiRecusal, id=request.POST.get('recusal_id'), report=report)
    if recusal.reason not in KaiRecusal.MANUAL_REASONS:
        messages.error(
            request,
            'This recusal comes from the case itself and cannot be lifted by hand.',
        )
        return redirect('manage_kai_report', report_id=report.id)

    member_name = recusal.user.name
    recusal.delete()

    KaiReportActivity.objects.create(
        report=report, user=request.user, action='standin_removed',
        details=f'{member_name} is no longer recused from this case.',
    )
    messages.success(request, f'{member_name} has been restored to this case.')
    return redirect('manage_kai_report', report_id=report.id)
