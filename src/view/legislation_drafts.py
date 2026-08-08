"""
Private legislation drafts — the My Work "Drafts" tab (v3.19.0).

WHAT THIS IS
------------
Before this, My Work was read-only: it showed what you had already submitted.
There was no way to start a bill you were not ready to present, and the only
upload path (`/legislation/upload/`) is officer-gated and puts the bill in front
of the chapter immediately.

A `LegislationDraft` is private to its author until they publish it. See the
model docstring for why it is a separate table rather than `Legislation.is_draft`
— short version: `Legislation` is read from 35+ places and a boolean would have
to be excluded correctly in every one of them.

⚠️ THE AUTHZ SPLIT, AND IT IS DELIBERATE
----------------------------------------
* **Drafting is open to every member** (`@login_required`). That is what makes
  the feature useful to someone who is not an officer.
* **Publishing is officer-only** (`@officer_required`), because publishing puts
  a bill on the chapter ballot and that is an existing, settled authorization
  boundary — `/legislation/upload/` has always been `@officer_required`.

Widening *drafting* is a new surface with no chapter-visible effect. Widening
*publishing* would change who can put business before the chapter, which is a
governance decision and not one to make as a side effect of adding an upload
button. A non-officer's draft shows "Ready — ask an officer to present this."

**If Mason wants members to publish their own bills, this is a one-line change:**
swap `@officer_required` for `@login_required` on `publish_legislation_draft`.
The ownership check below is independent of it and stays either way.

EVERY VIEW HERE IS OWNERSHIP-SCOPED
-----------------------------------
`_get_own_draft()` is the single read path and it filters on `author=user`. A
draft is never fetched by pk alone. This is one helper rather than a repeated
`filter(author=...)` at four call sites, for the reason five consecutive
releases of this codebase have documented: the call site that forgets is the one
that leaks.
"""
import logging
import mimetypes
import os

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from django.contrib.auth.decorators import login_required

from ..decorators import officer_required, log_function_call
from ..forms import LegislationDraftForm
from src.models import Legislation, LegislationDraft

logger = logging.getLogger(__name__)

#: Ceiling on the drafts panel. Scoped to one author so it is small by
#: construction; this is a guard against a runaway, not a page size. Same
#: reasoning as MY_POLLS_LIMIT in view_legislation_history.py.
MY_DRAFTS_LIMIT = 100


def _get_own_draft(request, draft_id):
    """
    The ONLY way this module loads a draft.

    Scoped to `author=request.user`, so another member's draft is a 404 rather
    than a 403 — a 403 confirms the row exists, and for a private document the
    existence is part of what is private. Same reasoning as the Kai surfaces.
    """
    return get_object_or_404(
        LegislationDraft, pk=draft_id, author=request.user,
    )


@login_required
@log_function_call
def create_legislation_draft(request):
    """Create a draft from the My Work page. Any member."""
    if request.method != 'POST':
        return redirect('view_legislation_history')

    form = LegislationDraftForm(request.POST, request.FILES)
    if form.is_valid():
        draft = form.save(commit=False)
        draft.author = request.user
        draft.save()
        messages.success(
            request,
            f'Draft "{draft.title}" saved. Only you can see it until it is published.',
        )
    else:
        # Surface the actual problem rather than "there was an error" — the
        # upload path's generic message is a known annoyance and there is no
        # reason to reproduce it.
        for field, errors in form.errors.items():
            label = form.fields[field].label if field in form.fields else field
            for error in errors:
                messages.error(request, f'{label}: {error}')

    return redirect(f"{_history_url()}?tab=drafts")


@login_required
@log_function_call
def edit_legislation_draft(request, draft_id):
    """Edit one of your own drafts."""
    draft = _get_own_draft(request, draft_id)

    if draft.is_published:
        messages.warning(
            request,
            'This draft has already been published. Edit the bill itself instead.',
        )
        return redirect(f"{_history_url()}?tab=drafts")

    if request.method == 'POST':
        form = LegislationDraftForm(request.POST, request.FILES, instance=draft)
        if form.is_valid():
            form.save()
            messages.success(request, 'Draft updated.')
            return redirect(f"{_history_url()}?tab=drafts")
        messages.error(request, 'There was a problem saving your changes.')
    else:
        form = LegislationDraftForm(instance=draft)

    ready, reason = draft.ready_to_publish()
    return render(request, 'legislation_draft_edit.html', {
        'form': form,
        'draft': draft,
        'ready_to_publish': ready,
        'not_ready_reason': reason,
        'can_publish': _can_publish(request.user),
    })


@login_required
@log_function_call
def serve_legislation_draft_document(request, draft_id):
    """
    Stream a draft's attachment to its author, and to nobody else.

    ⚠️ v3.19.3 — WHY THIS EXISTS. The draft ROW was always author-scoped
    (`_get_own_draft`). The FILE was not. `LegislationDraft.document` saves into
    `MEDIA_ROOT/legislation_drafts/`, and `/media/<path>` is served by
    `view/serve_media.py`, whose entire gate is `@login_required` — no owner
    check, and no way to do one, because it resolves a path on disk and knows
    nothing about which model owns it.

    That gate is correct for everything else under `/media/`: uploaded
    legislation, minutes and profile pictures are all meant to be read by
    members. Drafts are the first thing in this codebase stored there under a
    NARROWER promise than "members may read this" — the feature says "Only you
    can see it until it is published" in four places. The gate did not get
    weaker; the content behind it got more sensitive, and nobody re-derived
    whether the gate still matched.

    So: the file's protection is now the same object as the row's. Both go
    through `_get_own_draft()`, both 404 on someone else's draft, and there is
    one access rule rather than two that have to agree.

    The uuid filenames (`legislation_draft_upload_path`) sit UNDERNEATH this,
    not instead of it — they remove the guessability the v3.14.2 slugifier
    introduced, but a random path is not an access control and must never be
    treated as one.
    """
    draft = _get_own_draft(request, draft_id)

    if not draft.document:
        raise Http404('No document attached to this draft.')

    # The path comes from the FileField, not from the request, so traversal is
    # not reachable here. Guarded anyway, for the same reason `serve_media` is:
    # the check is two lines and it stops this from being the exception if the
    # storage layer ever changes underneath it.
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    resolved = os.path.realpath(draft.document.path)
    if not resolved.startswith(media_root + os.sep) or not os.path.isfile(resolved):
        raise Http404('File not found')

    content_type, _ = mimetypes.guess_type(resolved)
    response = FileResponse(
        open(resolved, 'rb'),
        content_type=content_type or 'application/octet-stream',
    )
    response['Content-Disposition'] = content_disposition_header(
        as_attachment=False,
        # The author's own filename, not the uuid on disk.
        filename=draft.document_display_name or os.path.basename(resolved),
    )
    # `no-store`, not `private, max-age=…` as `serve_media` uses. A shared cache
    # was never the risk here — a private draft simply has no business sitting
    # in a browser cache on a shared library machine after the member logs out.
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    return response


@login_required
@require_POST
@log_function_call
def delete_legislation_draft(request, draft_id):
    """Delete one of your own drafts."""
    draft = _get_own_draft(request, draft_id)
    title = draft.title
    draft.delete()
    messages.success(request, f'Draft "{title}" deleted.')
    return redirect(f"{_history_url()}?tab=drafts")


@officer_required
@require_POST
@log_function_call
def publish_legislation_draft(request, draft_id):
    """
    Turn a draft into real legislation.

    Officer-only — see the module docstring. The ownership check still applies:
    an officer publishes their OWN draft, not somebody else's, because the draft
    is private and publishing it is the author's decision to make.
    """
    draft = _get_own_draft(request, draft_id)

    ready, reason = draft.ready_to_publish()
    if not ready:
        messages.error(request, reason)
        return redirect(f"{_history_url()}?tab=drafts")

    available_at = draft.planned_available_at
    if timezone.is_naive(available_at):
        # v3.13.3's lesson: naive datetimes are interpreted as UTC by the DB
        # layer, which skewed appointment vote times by the UTC offset. Form
        # input arrives naive in the server's local zone.
        available_at = timezone.make_aware(available_at)

    voting_ends_at = draft.planned_voting_ends_at
    if voting_ends_at and timezone.is_naive(voting_ends_at):
        voting_ends_at = timezone.make_aware(voting_ends_at)

    legislation = Legislation(
        title=draft.title,
        description=draft.description,
        posted_by=request.user,
        available_at=available_at,
        voting_ends_at=voting_ends_at,
        # ⚠️ voting_manual_open=True, ALWAYS, and this is not a default worth
        # overriding from the draft. A bill arriving from a draft has a planned
        # availability date but no considered "voting opens now" moment — with
        # manual open the bill becomes readable on its date and stays unopened
        # until the author presents it and hits "Open Voting Now". Publishing
        # something that starts collecting ballots on a timer nobody watched is
        # the failure this whole feature exists to avoid.
        voting_manual_open=True,
        vote_mode=draft.vote_mode,
        required_percentage=draft.required_percentage,
        anonymous_vote=draft.anonymous_vote,
        allow_abstain=draft.allow_abstain,
    )

    # NOTE: `notes` is deliberately NOT copied. It is the author's private
    # scratch space and the model docstring says so; copying it here would make
    # a private field chapter-visible, which is the v3.16.2 boundary in
    # miniature.

    # ⚠️ v3.19.3 — ATOMIC, because publishing is no longer one save.
    #
    # It used to be: build the Legislation, point its `document` at the draft's
    # file, save once. The copy below makes it three writes (the bill, the
    # bill's document, the draft's back-link), and a failure between them used
    # to be impossible and now is not — a raised exception partway through would
    # otherwise leave a published bill with no document and no draft linking to
    # it, which is a bill on the chapter ballot that its author cannot find.
    #
    # The FILE write is not transactional and cannot be. A rollback after the
    # copy leaves an orphaned file in `legislation_docs/`, which costs disk and
    # nothing else — strictly the better failure of the two, and the reason the
    # copy sits inside rather than outside.
    with transaction.atomic():
        legislation.save()

        if draft.document:
            # ⚠️ v3.19.3 — COPY, do not re-point. This used to be
            # `legislation.document = draft.document`, which left both rows sharing
            # one path under `legislation_drafts/`. Two problems, and the first is
            # the one that matters now:
            #
            #  * `legislation_drafts/` is author-private by construction as of
            #    v3.19.3 — that is what `serve_legislation_draft_document` enforces
            #    and what the uuid names are for. A PUBLISHED bill has to be
            #    chapter-readable, so leaving its document in the private directory
            #    makes the directory mean two things and guarantees that any future
            #    rule of the form "deny legislation_drafts/" breaks published bills.
            #  * the shared path was also a latent footgun: the two rows are
            #    independently deletable, and any cleanup command that ever sweeps
            #    the drafts directory would silently take the published bill's
            #    document with it.
            #
            # The copy goes through `Legislation.document`'s own field, so it lands
            # in `legislation_docs/` with that field's storage and sanitiser, and
            # under the name the author actually uploaded rather than the uuid.
            # `DualLocationStorage` saves to MEDIA_ROOT only (confirmed 07-31-26),
            # so this does not write into the git-tracked exportable_media/ copy.
            from django.core.files import File

            published_name = draft.document_display_name or os.path.basename(draft.document.name)
            try:
                draft.document.open('rb')
                # `File(...)`, not `ContentFile(read())` — FieldFile.save streams via
                # chunks(), so a 20 MB attachment is not held in memory twice.
                legislation.document.save(published_name, File(draft.document.file), save=True)
            finally:
                draft.document.close()

        draft.published_legislation = legislation
        draft.published_at = timezone.now()
        draft.save(update_fields=['published_legislation', 'published_at', 'updated_at'])

    # The chapter is NOT notified here. `tasks.notify_available_legislation`
    # fires when `available_at` actually arrives — see that task and the
    # `availability_notified_at` field. A bill dated three weeks out should not
    # push a notification today.
    if legislation.is_available():
        # ...unless it is already available, in which case the task would fire
        # within the minute anyway and doing it inline gives the author
        # immediate feedback that it went out.
        from src.tasks.votes import announce_legislation_availability
        announce_legislation_availability(legislation)
        messages.success(
            request,
            f'"{legislation.title}" published and the chapter has been notified. '
            f'Voting stays closed until you open it.',
        )
    else:
        messages.success(
            request,
            f'"{legislation.title}" published. The chapter will be notified on '
            f'{timezone.localtime(available_at).strftime("%b %d at %I:%M %p")}. '
            f'Voting stays closed until you open it.',
        )

    return redirect(f"{_history_url()}?tab=drafts")


def _can_publish(user):
    """
    Mirror of `officer_required`'s test, for the template.

    Kept as one function so the button's enabled state and the endpoint's gate
    cannot drift — the thing that goes wrong otherwise is a button that is
    visible and an endpoint that refuses, or worse the reverse.
    """
    from src.constants import MemberType
    # Exactly `officer_required`'s predicate:
    #     request.user.is_officer or request.user.member_type == MemberType.CHAIR
    # `is_officer` already folds in `is_admin` (models/users.py:250), so this is
    # the whole test and not a subset of it.
    return bool(
        getattr(user, 'is_officer', False)
        or getattr(user, 'member_type', None) == MemberType.CHAIR
    )


def _history_url():
    from django.urls import reverse
    return reverse('view_legislation_history')
