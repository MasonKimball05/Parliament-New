"""
v3.19.6 — ownership-aware serving for the uploads `/media/` must not serve.

⚠️ READ THIS BEFORE ADDING A `FileField` ANYWHERE IN THIS CODEBASE.

`/media/` (`src/view/serve_media.py`) makes exactly one promise: *any logged-in
member may read this*. That promise is correct for uploaded legislation, chapter
minutes, songbook audio, profile pictures and landing photos. It is wrong for
anything stored under a narrower one — and until this release, eight directories
were stored under a narrower one and served under the wider one anyway.

HOW THIS HAPPENED, because the shape recurs and is worth naming
---------------------------------------------------------------
v3.19.3 found that legislation drafts were in exactly this position, and fixed
it: a new author-scoped view, uuid storage names, both templates repointed.
v3.19.5 then found that the fix had left the old `/media/` route open, and
closed it with `PRIVATE_MEDIA_PREFIXES` — deliberately a *set* rather than an
`if`, with a test asserting that every entry has a replacement route.

Both were right. Neither enumerated. `PRIVATE_MEDIA_PREFIXES` shipped with one
entry and a comment calling `legislation_drafts/` *"the first such thing in this
codebase"*; the 08-10 review walked `apps.get_models()` and found it was the
ninth. Four of the other eight hold the most confidential material the
application handles — Kai allegation attachments, slating GPA screenshots and
application files, and excuse documents, which are doctors' notes.

**The general form: building the general mechanism is not the same as applying
it to the general case.** A set is only the general form if something enumerates
the population it is drawn from. That enumeration is now
`test_every_upload_directory_is_classified` in `src/test_media_classification.py`,
and it fails the build when a model gains an `upload_to` nobody classified.

THE RULE FOR EACH VIEW BELOW
----------------------------
**The file's access rule is the same object as the page's access rule.** Not a
copy of it, not something equivalent to it — the same helper, called the same
way. Two rules that have to agree will eventually not agree, and the one nobody
looks at is the file.

So each view here re-uses the predicate its host page already applies, and where
a page has two populations (a party to a case and a reviewer of it), the view
checks both in the same order the pages do. The pattern to copy is
`serve_legislation_draft_document`, which does this via `_get_own_draft`.

WHAT THE UUID NAMES ARE AND ARE NOT
-----------------------------------
The four confidential directories gained uuid `upload_to` callables in this
release (see `src/models/kai.py` and `src/models/slating.py`). They sit
UNDERNEATH these views and are not a substitute for them — the same sentence
v3.19.3 wrote about drafts and then had to prove it meant. `upload_to` is
save-time only, so files already on disk keep their `slugify()` names: this
stops the guessable population growing, it does not retire it. **The route being
shut is what protects the existing files.**
"""
import logging
import mimetypes
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import SuspiciousFileOperation
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from src.feature_flag_decorators import require_feature_flag

from ..decorators import log_function_call
from ..permissions import user_is_officer_or_chair, user_is_vpp

logger = logging.getLogger(__name__)


#: ⚠️ v3.19.8 — MOVED TO `src/utils/content_disposition.py` AND RE-EXPORTED HERE.
#:
#: v3.19.7 defined this set in this module, which was the right decision applied
#: to the wrong half of the problem: this module serves six private directories
#: to a Kai reviewer or a slating committee member, and `serve_media` serves ten
#: PUBLIC ones to every logged-in member — with `as_attachment=False` and a
#: filename-guessed content type, unchanged since v3.14.1. The fix went where the
#: attention was rather than where the surface was.
#:
#: Read `src/utils/content_disposition.py` for the rule, the `image/svg+xml`
#: exclusion, and why audio being absent does not break the songbook. The name
#: stays importable from here because `src/test_private_upload_rendering.py`
#: imports it from this module and that test is about this module's behaviour.
from ..utils.content_disposition import (  # noqa: E402  (placed with the docs it replaces)
    INLINE_SAFE_CONTENT_TYPES,
    apply_disposition,
)


def _stream_private_file(fieldfile, download_name=None):
    """
    Stream one already-authorised upload, or 404.

    **This function performs no authorisation and must never be called before
    it.** Every caller below decides access first and hands the `FieldFile` in.
    It is deliberately not able to look anything up, so it cannot be mistaken
    for a gate.

    The traversal guard is not reachable from a request — the path comes from a
    `FileField`, not from user input — and is here for the same reason
    `serve_legislation_draft_document` has one: it costs two lines and it stops
    this from being the exception if the storage layer changes underneath it.

    ⚠️ `DualLocationStorage.path()` falls back to `BASE_DIR/exportable_media/`
    when a name is absent from `MEDIA_ROOT`, and that directory is committed to
    a PUBLIC repo by design (CLAUDE.md's standing disposition). Five of the
    eight models here use that storage. Requiring the resolved path to sit under
    `MEDIA_ROOT` means a confidential upload whose file has gone missing 404s
    rather than silently serving whatever shares its name in the public
    directory. That is not hypothetical bookkeeping — it is the same fallback
    `delete_draft_document_file` was written to guard against, one verb over.
    """
    if not fieldfile:
        raise Http404('No file attached.')

    media_root = os.path.realpath(settings.MEDIA_ROOT)
    try:
        resolved = os.path.realpath(fieldfile.path)
    except (ValueError, SuspiciousFileOperation):
        # `SuspiciousFileOperation` is what `FileSystemStorage.path()` raises for
        # a stored name that escapes its location. A row can only reach that
        # state through a direct DB write, so this is a 404 and not a 500.
        raise Http404('File not found')

    if not resolved.startswith(media_root + os.sep) or not os.path.isfile(resolved):
        raise Http404('File not found')

    content_type, _ = mimetypes.guess_type(resolved)
    content_type = content_type or 'application/octet-stream'
    response = FileResponse(open(resolved, 'rb'), content_type=content_type)

    # v3.19.7 — render only what is safe to render; download everything else.
    # v3.19.8 — the decision and the `nosniff` header that backs it now live in
    # `src/utils/content_disposition.py` and are shared with `serve_media`, so
    # the two upload-serving views cannot drift apart.
    apply_disposition(
        response, content_type,
        download_name or os.path.basename(resolved),
    )
    # `no-store`, matching `serve_legislation_draft_document` and NOT
    # `serve_media`'s `private, max-age=3600`. A shared cache was never the
    # risk; a confidential file sitting in a browser cache on a shared machine
    # after the member logs out is.
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    return response


# ---------------------------------------------------------------------------
# Kai — judicial/disciplinary
# ---------------------------------------------------------------------------
#
# ⚠️ THIS IS THE STANDING CONFIDENTIALITY BOUNDARY, NOT AN ORDINARY PERMISSION.
# CLAUDE.md (07-25-26) unregistered all seven Kai models from `/admin/` so that
# being a Django superuser could not route around `KaiMemberPermission`, and the
# same disposition fixed global search because *"a permission-less Kai member
# could full-text search allegation bodies."* An attachment readable at
# `/media/kai_reports/<slug>.pdf` by any member — including a pledge, including
# the accused — was that same bypass one resource over, needing no committee
# membership at all.
#
# Two populations may read an attachment, and they are checked in this order
# because `_case_access` WITHDRAWS every permission from a party to the case
# (recusal, v3.18.0). Checking the reviewer branch first would refuse the
# submitter their own evidence.


def _user_may_read_kai_report(user, report):
    """
    True if `user` may read `report`'s attachments.

    Mirrors the two host views exactly:
      * `kai_user_dashboard.user_view_report` — submitter or accused
        (`Q(submitted_by=user) | Q(targeted_to=user)`).
      * `kai_reports.manage_kai_report` — `_get_kai_access` narrowed by
        `_case_access`, requiring `can_view_report_details` and not recused.

    Note this deliberately grants the ACCUSED access to the attachment. That is
    what `user_view_report` already does and it is the right answer: CLAUDE.md
    records that the confidentiality promise is *the accused never learns who
    reported them*, enforced by `can_view_submitter_identity`, not that the
    accused is kept from the case against him. An attachment is evidence, and
    a member cannot answer an allegation he cannot see.
    """
    if report.submitted_by_id == user.pk or report.targeted_to_id == user.pk:
        return True

    # Local imports: `src.view.kai_reports` pulls in a large slice of the app,
    # and this module is imported by `urls.py` at startup. Same reasoning as
    # `kai_audit.py`'s import of the same two helpers.
    from src.models import Committee
    from src.view.kai_reports import _case_access, _get_kai_access

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        return False

    access = _case_access(user, report, _get_kai_access(user, kai_committee))
    if access.get('is_recused'):
        return False
    return bool(access.get('can_view_report_details'))


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def serve_kai_report_attachment(request, report_id):
    """Stream a Kai report's attachment to a party or a permitted reviewer."""
    from src.models import KaiReport

    report = get_object_or_404(KaiReport, id=report_id)
    if not _user_may_read_kai_report(request.user, report):
        # 404 and not 403, matching `_get_own_draft`: whether a given Kai case
        # exists is itself confidential, and a 403 answers that question.
        raise Http404('File not found')
    return _stream_private_file(report.attachment)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def serve_kai_response_file(request, response_id):
    """Stream a Kai custom-field file response. Same rule as the parent report."""
    from src.models import KaiReportFieldResponse

    response = get_object_or_404(
        KaiReportFieldResponse.objects.select_related('report'), id=response_id)
    if not _user_may_read_kai_report(request.user, response.report):
        raise Http404('File not found')
    return _stream_private_file(response.file_value)


# ---------------------------------------------------------------------------
# Kai — commendations (v3.28.9)
# ---------------------------------------------------------------------------
#
# See src/models/kai_commendations.py's module docstring for why this is a
# separate model from KaiReport. The access rule is deliberately SIMPLER than
# `_user_may_read_kai_report` above: there is no accused, so there is no
# `_case_access`/recusal narrowing to reapply — just "the submitter, or a
# committee member the chair granted can_view_report_details to", which is
# exactly `manage_kai_commendations`/`_detail`'s own gate. Note the commended
# member does NOT get access here — see the model docstring's visibility
# note: commendations are Kai-committee-only, not honoree-facing (yet).


def _user_may_read_kai_commendation(user, commendation):
    """
    True if `user` may read `commendation`'s attachments.

    Mirrors `kai_commendations.manage_kai_commendation_detail` exactly:
    the submitter, or a committee member with `can_view_report_details`
    under the shared KaiMemberPermission grants (see that module's
    docstring for why commendations reuse the Kai committee's permission
    system rather than inventing a second one).
    """
    if commendation.submitted_by_id == user.pk:
        return True

    from src.models import Committee
    from src.view.kai_reports import _get_kai_access

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        return False

    access = _get_kai_access(user, kai_committee)
    return bool(access.get('can_view_report_details'))


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def serve_kai_commendation_attachment(request, commendation_id):
    """Stream a commendation's attachment to its submitter or a permitted reviewer."""
    from src.models import KaiCommendation

    commendation = get_object_or_404(KaiCommendation, id=commendation_id)
    if not _user_may_read_kai_commendation(request.user, commendation):
        raise Http404('File not found')
    return _stream_private_file(commendation.attachment)


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def serve_kai_commendation_response_file(request, response_id):
    """Stream a commendation's custom-field file response. Same rule as the parent commendation."""
    from src.models import KaiCommendationFieldResponse

    response = get_object_or_404(
        KaiCommendationFieldResponse.objects.select_related('commendation'), id=response_id)
    if not _user_may_read_kai_commendation(request.user, response.commendation):
        raise Http404('File not found')
    return _stream_private_file(response.file_value)


# ---------------------------------------------------------------------------
# Slating — applications, GPA screenshots
# ---------------------------------------------------------------------------
#
# CLAUDE.md's admin boundary already excludes all four `SlatingApplicationResponse`
# value fields from `/admin/` on the grounds that `is_confidential` was
# app-layer-only. `file_value` is the fifth channel for the same data and was
# open to every member; `gpa_screenshot` is an academic record.
#
# `_user_can_view` is the committee-read predicate `slating_committee_required`
# uses, including its `_period_is_locked` rule that a site admin alone is not
# enough once a period locks down. Re-used rather than re-derived.


def _user_may_read_slating_application(user, application):
    """
    True if `user` may read `application`'s uploads.

    Mirrors both host views:
      * `slating.apply.view_application` — the applicant reading his own.
      * `slating.applications_review.application_detail` —
        `@slating_committee_required`, i.e. `_user_can_view(user, period)` plus
        the admin fallback *only while the period is unlocked*.
    """
    if application.applicant_id == user.pk:
        return True

    from src.view.slating.permissions import _period_is_locked, _user_can_view

    period = application.period
    if _user_can_view(user, period):
        return True
    return bool(user.is_admin and not _period_is_locked(period))


@login_required
@log_function_call
def serve_slating_gpa_screenshot(request, app_id):
    """Stream an applicant's GPA screenshot to the applicant or the committee."""
    from src.models import SlatingApplication

    application = get_object_or_404(
        SlatingApplication.objects.select_related('period'), id=app_id)
    if not _user_may_read_slating_application(request.user, application):
        raise Http404('File not found')
    return _stream_private_file(application.gpa_screenshot)


@login_required
@log_function_call
def serve_slating_response_file(request, response_id):
    """Stream a slating application file response. Same rule as the application."""
    from src.models import SlatingApplicationResponse

    response = get_object_or_404(
        SlatingApplicationResponse.objects.select_related(
            'application', 'application__period'),
        id=response_id,
    )
    if not _user_may_read_slating_application(request.user, response.application):
        raise Http404('File not found')
    return _stream_private_file(response.file_value)


# ---------------------------------------------------------------------------
# Excuses — supporting documents
# ---------------------------------------------------------------------------
#
# ⚠️ THE HELP TEXT ON THIS FIELD SAYS "doctor note, etc." That is health
# information about a named member, and it was readable by every member of the
# chapter at `/media/excuse_documents/<slug>.pdf`. Of the eight directories in
# this release this is the one whose exposure needed no argument at all.


@login_required
@require_feature_flag('attendance_tracking', 'excuse_system')
@log_function_call
def serve_excuse_document(request, excuse_id):
    """
    Stream an excuse's supporting document to its author or an officer.

    Mirrors `submit_excuse.my_excuses` (the member's own) and
    `officer.event_attendance.review_excuses` (`@officer_required`: officers,
    chairs and admins, excluding advisors and pledges).
    """
    from src.models import AttendanceExcuse

    excuse = get_object_or_404(AttendanceExcuse, id=excuse_id)

    is_owner = excuse.user_id == request.user.pk
    if not (is_owner or user_is_officer_or_chair(request.user)):
        raise Http404('File not found')
    return _stream_private_file(excuse.supporting_document)


# ---------------------------------------------------------------------------
# Service hours — proof of service
# ---------------------------------------------------------------------------
#
# Lower stakes than the five above and still not "any member may read this":
# these are receipts, sign-in sheets and photographs a member submits to prove
# hours, and the app shows them to the member and to the VPP.


def _user_may_read_service_submission(user, submission):
    """
    Mirrors `service_user_dashboard.user_view_submission` (the member's own) and
    `service_hours.manage_service_submission` (`@vpp_required`).
    """
    if submission.submitted_by_id == user.pk:
        return True
    return user_is_vpp(user)


@login_required
@log_function_call
def serve_service_hours_attachment(request, submission_id):
    """Stream a service-hours attachment to its submitter or the VPP."""
    from src.models import ServiceHoursSubmission

    submission = get_object_or_404(ServiceHoursSubmission, id=submission_id)
    if not _user_may_read_service_submission(request.user, submission):
        raise Http404('File not found')
    return _stream_private_file(submission.attachment)


@login_required
@log_function_call
def serve_service_hours_response_file(request, response_id):
    """Stream a service-hours custom-field file. Same rule as the submission."""
    from src.models import ServiceFieldResponse

    response = get_object_or_404(
        ServiceFieldResponse.objects.select_related('submission'), id=response_id)
    if not _user_may_read_service_submission(request.user, response.submission):
        raise Http404('File not found')
    return _stream_private_file(response.file_value)


# ---------------------------------------------------------------------------
# Bug reports — screenshots
# ---------------------------------------------------------------------------
#
# The mildest of the eight, and included because a classification must be
# complete to be enforceable. A bug screenshot is whatever happened to be on
# the reporter's screen, which is frequently another page of this application
# with someone else's data on it.
#
# `bug_admin_required` hardcodes `user_id == '73'` and CLAUDE.md records that as
# INTENTIONAL — this view re-uses that rule and does not question it.


@login_required
@log_function_call
def serve_bug_report_screenshot(request, report_id):
    """Stream a bug report's screenshot to its reporter or the bug admin."""
    from src.models import BugReport

    report = get_object_or_404(BugReport, id=report_id)
    user = request.user

    is_reporter = report.submitted_by_id == user.pk
    is_bug_admin = str(user.user_id) == '73'  # see `bug_admin_required`
    if not (is_reporter or is_bug_admin):
        raise Http404('File not found')
    return _stream_private_file(report.screenshot)
