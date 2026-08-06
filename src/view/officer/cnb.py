"""
Constitution & Bylaws Builder views.

Access control:
  - Public document viewer (/constitution-bylaws/)  — any authenticated member
  - CNB management views (/officers/cnb/*)          — admins and CNB role holders only
"""

import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.decorators import cnb_required
from src.models.users import member_defer
from src.models import (
    GoverningDocument, Article, Section, Resolution, ResolutionAmendment,
    ResolutionCollaborator, ParliamentUser,
)


# ── Public document viewer ────────────────────────────────────────────────────

@login_required
def cnb_viewer(request):
    """
    Unified Constitution & Bylaws hub.
    Tab is selected via ?tab= query param: 'document' (default), 'resolutions', 'manage'.
    The 'manage' tab is only shown to CNB permission holders.
    """
    # v3.19.1: `.enabled()` rather than `.objects.all()` — one feature flag per
    # document (`cnb_foreword` / `cnb_constitution` / `cnb_bylaws` /
    # `cnb_appendix`), so a document can be pulled or staged without a deploy.
    # This is the MEMBER-facing surface; officer management below deliberately
    # keeps querying every row, because a document has to be editable before it
    # can be turned on. Ordering now comes from Meta (v3.19.1) instead of from
    # whatever the database happened to return.
    documents = GoverningDocument.enabled().prefetch_related('articles__sections')
    # v3.17.3: `created_by` was joined and never read by cnb/viewer.html.
    #
    # v3.17.5: `prefetch_related('amendments')` REMOVED and replaced with a
    # count annotation. The template never iterates the amendments here — it
    # only prints `{{ resolution.amendments.count }}` (twice, at :272 and :386)
    # — so the prefetch was pulling every amendment ROW in order to render a
    # number. (Measured: on a prefetched relation Django's `.count()` does read
    # the prefetch cache and costs 0 queries, so this was not an N+1 — it was a
    # wasted fetch of every column of every amendment.) One annotation returns
    # the number without the rows.
    resolutions = (
        Resolution.objects
        .annotate(amendment_total=Count('amendments'))
        .order_by('-created_at')
    )
    is_cnb = request.user.has_cnb_permission

    protected_sections = []
    if is_cnb:
        protected_sections = list(
            Section.objects.filter(amendment_protected=True).select_related('article__document')
        )

    active_tab = request.GET.get('tab', 'document')
    if active_tab not in ('document', 'resolutions', 'manage'):
        active_tab = 'document'
    if active_tab == 'manage' and not is_cnb:
        active_tab = 'document'

    context = {
        'documents': documents,
        'resolutions': resolutions,
        'user_can_manage': is_cnb,
        'is_cnb': is_cnb,
        'active_tab': active_tab,
        # v3.17.5: was two COUNT round trips over the same table. One pass.
        **Resolution.objects.aggregate(
            draft_count=Count('pk', filter=Q(status='draft')),
            pending_count=Count('pk', filter=Q(status='pending')),
        ),
        'protected_sections': protected_sections,
    }
    return render(request, 'cnb/viewer.html', context)


# ── CNB management hub ────────────────────────────────────────────────────────

@login_required
@cnb_required
def cnb_dashboard(request):
    """CNB Chair management dashboard — overview of documents and active resolutions."""
    # v3.17.5: `prefetch_related('articles')` REMOVED — cnb/dashboard.html only
    # prints `{{ doc.articles.count }}` (:72) and never iterates them, so the
    # prefetch fetched every article row to render a number that `.count` then
    # went back to the database for anyway.
    documents = GoverningDocument.objects.annotate(article_total=Count('articles'))
    # v3.17.3: `created_by` was joined and never read by cnb/dashboard.html.
    # v3.17.5: amendment count annotated — the template printed
    # `{{ resolution.amendments.count }}` twice on one line (:135, once for the
    # number and once for `|pluralize`), i.e. two COUNTs per resolution row.
    resolutions = Resolution.objects.annotate(amendment_total=Count('amendments'))
    protected_sections = Section.objects.filter(amendment_protected=True).select_related(
        'article__document'
    )

    context = {
        'documents': documents,
        'resolutions': resolutions,
        'protected_sections': protected_sections,
        # v3.17.5: was two COUNT round trips over the same table. One pass.
        **Resolution.objects.aggregate(
            draft_count=Count('pk', filter=Q(status='draft')),
            pending_count=Count('pk', filter=Q(status='pending')),
        ),
    }
    return render(request, 'cnb/dashboard.html', context)


# ── Document / Article / Section management ───────────────────────────────────

@login_required
@cnb_required
def manage_document(request, doc_type):
    """Edit articles and sections within a governing document."""
    document = get_object_or_404(GoverningDocument, doc_type=doc_type)
    articles = document.articles.prefetch_related('sections').all()

    context = {
        'document': document,
        'articles': articles,
    }
    return render(request, 'cnb/manage_document.html', context)


@login_required
@cnb_required
def edit_section(request, section_id):
    """Edit the text of a single section."""
    section = get_object_or_404(Section, pk=section_id)

    if request.method == 'POST':
        new_content = request.POST.get('content', '').strip()
        if not new_content:
            messages.error(request, 'Section content cannot be empty.')
        else:
            section.content = new_content
            section.title = request.POST.get('title', section.title).strip()
            section.save(update_fields=['content', 'title'])
            messages.success(request, f'{section.full_identifier} updated.')
            return redirect('cnb_manage_document', doc_type=section.article.document.doc_type)

    context = {'section': section}
    return render(request, 'cnb/edit_section.html', context)


@login_required
@cnb_required
@require_POST
def toggle_section_active(request, section_id):
    """Activate or deactivate a section (IFC/governing body ruling)."""
    section = get_object_or_404(Section, pk=section_id)

    if section.is_active:
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'A reason is required to deactivate a section.')
            return redirect('cnb_manage_document', doc_type=section.article.document.doc_type)
        section.is_active = False
        section.deactivation_reason = reason
        section.deactivated_by = request.user
        section.deactivated_at = timezone.now()
        section.save(update_fields=['is_active', 'deactivation_reason', 'deactivated_by', 'deactivated_at'])
        messages.warning(request, f'{section.full_identifier} deactivated.')
    else:
        section.is_active = True
        section.deactivation_reason = ''
        section.deactivated_by = None
        section.deactivated_at = None
        section.save(update_fields=['is_active', 'deactivation_reason', 'deactivated_by', 'deactivated_at'])
        messages.success(request, f'{section.full_identifier} reactivated.')

    return redirect('cnb_manage_document', doc_type=section.article.document.doc_type)


@login_required
@cnb_required
@require_POST
def add_section(request, article_id):
    """Add a new section to an article."""
    article = get_object_or_404(Article, pk=article_id)
    number = request.POST.get('number', '').strip()
    title = request.POST.get('title', '').strip()
    content = request.POST.get('content', '').strip()

    if not number or not content:
        messages.error(request, 'Section number and content are required.')
    elif article.sections.filter(number=number).exists():
        messages.error(request, f'Section {number} already exists in Article {article.number}.')
    else:
        last = article.sections.order_by('-display_order').first()
        display_order = (last.display_order + 1) if last else 1
        Section.objects.create(
            article=article, number=number, title=title,
            content=content, display_order=display_order,
        )
        messages.success(request, f'Section {number} added to Article {article.number}.')

    return redirect('cnb_manage_document', doc_type=article.document.doc_type)


@login_required
@cnb_required
@require_POST
def add_article(request, doc_type):
    """Add a new article to a governing document."""
    document = get_object_or_404(GoverningDocument, doc_type=doc_type)
    number = request.POST.get('number', '').strip()
    title = request.POST.get('title', '').strip()

    if not number or not title:
        messages.error(request, 'Article number and title are required.')
    elif document.articles.filter(number=number).exists():
        messages.error(request, f'Article {number} already exists.')
    else:
        last = document.articles.order_by('-display_order').first()
        display_order = (last.display_order + 1) if last else 1
        Article.objects.create(
            document=document, number=number, title=title,
            display_order=display_order,
        )
        messages.success(request, f'Article {number} — {title} added.')

    return redirect('cnb_manage_document', doc_type=doc_type)


@login_required
@cnb_required
@require_POST
def add_partial_suspension(request, section_id):
    """Suspend a specific sub-item within a section without suspending the whole section."""
    section = get_object_or_404(Section, pk=section_id)
    ref = request.POST.get('ref', '').strip()
    reason = request.POST.get('reason', '').strip()

    if not ref or not reason:
        messages.error(request, 'A sub-item reference and reason are both required.')
    else:
        suspensions = list(section.partial_suspensions or [])
        suspensions.append({
            'ref': ref,
            'reason': reason,
            'suspended_at': timezone.localdate().isoformat(),   # v3.17.4: local calendar date
            'suspended_by_name': request.user.name,
        })
        section.partial_suspensions = suspensions
        section.save(update_fields=['partial_suspensions'])
        messages.success(request, f'§ {section.number}({ref}) partially suspended.')

    return redirect('cnb_manage_document', doc_type=section.article.document.doc_type)


@login_required
@cnb_required
@require_POST
def remove_partial_suspension(request, section_id, idx):
    """Remove one partial suspension entry from a section."""
    section = get_object_or_404(Section, pk=section_id)
    suspensions = list(section.partial_suspensions or [])

    if 0 <= idx < len(suspensions):
        removed = suspensions.pop(idx)
        section.partial_suspensions = suspensions
        section.save(update_fields=['partial_suspensions'])
        messages.success(request, f'Partial suspension on "{removed.get("ref")}" lifted.')

    return redirect('cnb_manage_document', doc_type=section.article.document.doc_type)


@login_required
@cnb_required
@require_POST
def toggle_article_active(request, article_id):
    """Activate or deactivate an entire article."""
    article = get_object_or_404(Article, pk=article_id)

    if article.is_active:
        reason = request.POST.get('reason', '').strip()
        if not reason:
            messages.error(request, 'A reason is required to deactivate an article.')
            return redirect('cnb_manage_document', doc_type=article.document.doc_type)
        article.is_active = False
        article.deactivation_reason = reason
        article.deactivated_by = request.user
        article.deactivated_at = timezone.now()
        article.save(update_fields=['is_active', 'deactivation_reason', 'deactivated_by', 'deactivated_at'])
        messages.warning(request, f'Article {article.number} deactivated.')
    else:
        article.is_active = True
        article.deactivation_reason = ''
        article.deactivated_by = None
        article.deactivated_at = None
        article.save(update_fields=['is_active', 'deactivation_reason', 'deactivated_by', 'deactivated_at'])
        messages.success(request, f'Article {article.number} reactivated.')

    return redirect('cnb_manage_document', doc_type=article.document.doc_type)


# ── Resolution builder ────────────────────────────────────────────────────────

@login_required
def resolution_list(request):
    """List all resolutions — readable by any authenticated member."""
    # v3.17.5: `prefetch_related('amendments')` REMOVED, count annotated.
    # cnb/resolution_list.html only prints `{{ resolution.amendments.count }}`
    # (:55, twice on the line — once for the number, once for `|pluralize`) and
    # never iterates them, so the prefetch fetched every amendment row to render
    # a number. An annotation returns the number without the rows.
    resolutions = (
        Resolution.objects
        .select_related('created_by').defer(*member_defer('created_by'))
        .annotate(amendment_total=Count('amendments'))
    )
    context = {'resolutions': resolutions}
    return render(request, 'cnb/resolution_list.html', context)


@login_required
def resolution_detail(request, resolution_id):
    """View a resolution — readable by any authenticated member.
    Edit controls are shown to CNB permission holders and editor collaborators."""
    resolution = get_object_or_404(
        # v3.17.3: `created_by` was joined and never read by resolution_print.html.
        Resolution.objects.prefetch_related(
            'amendments__section__article__document',
            'collaborators__user',
        ),
        pk=resolution_id
    )

    is_editable_status = resolution.status in ('draft', 'pending')
    is_cnb = request.user.has_cnb_permission
    is_editor_collab = resolution.collaborators.filter(
        user=request.user, role='editor'
    ).exists()
    can_edit = is_editable_status and (is_cnb or is_editor_collab)

    # Section selector for the amendment modal — only needed by editors.
    # v3.19.1: gated. A document that is switched off should not be amendable,
    # and a resolution written against one would be invisible to the chapter it
    # governs.
    documents = (
        GoverningDocument.enabled().prefetch_related('articles__sections')
        if can_edit else []
    )

    # Members list for the add-collaborator form — CNB only
    members = (
        ParliamentUser.objects.filter(is_active=True).order_by('name')
        if is_cnb else []
    )

    context = {
        'resolution': resolution,
        'documents': documents,
        'can_edit': can_edit,
        'is_cnb': is_cnb,
        'members': members,
    }
    return render(request, 'cnb/resolution_detail.html', context)


@login_required
@cnb_required
def create_resolution(request):
    """Create a new resolution."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        resolution_type = request.POST.get('resolution_type', 'amendment')
        authors = request.POST.get('authors', '').strip()
        sponsors = request.POST.get('sponsors', '').strip()
        whereas = request.POST.get('whereas_clauses', '').strip()
        resolved = request.POST.get('resolved_text', '').strip()
        body = request.POST.get('resolution_body', '').strip()
        notes = request.POST.get('additional_notes', '').strip()
        vote_date_raw = request.POST.get('vote_date', '').strip()

        ref_docs = GoverningDocument.enabled().prefetch_related('articles__sections')  # v3.19.1: per-document flags

        if not title:
            messages.error(request, 'A title is required.')
            return render(request, 'cnb/resolution_form.html', {'action': 'Create', 'ref_docs': ref_docs})

        vote_date = None
        if vote_date_raw:
            try:
                vote_date = datetime.date.fromisoformat(vote_date_raw)
            except ValueError:
                messages.error(request, 'Invalid vote date format.')
                return render(request, 'cnb/resolution_form.html', {'action': 'Create', 'ref_docs': ref_docs})

        resolution = Resolution.objects.create(
            title=title,
            resolution_type=resolution_type,
            authors=authors,
            sponsors=sponsors,
            whereas_clauses=whereas,
            resolved_text=resolved,
            resolution_body=body,
            additional_notes=notes,
            vote_date=vote_date,
            created_by=request.user,
        )
        messages.success(request, f'Resolution "{title}" created.')
        return redirect('cnb_resolution_detail', resolution_id=resolution.pk)

    ref_docs = GoverningDocument.enabled().prefetch_related('articles__sections')  # v3.19.1: per-document flags
    return render(request, 'cnb/resolution_form.html', {'action': 'Create', 'ref_docs': ref_docs})


@login_required
@cnb_required
def edit_resolution(request, resolution_id):
    """Edit resolution metadata (title, whereas, resolved text, etc.)."""
    # v3.17.5: `prefetch_related('amendments')` — resolution_form.html tests the
    # amendments for presence, prints the count and then loops them (:199, :212,
    # :214). Without a prefetch that was three separate queries; with it, all
    # three read one cached result set.
    resolution = get_object_or_404(
        Resolution.objects.prefetch_related('amendments__section'),
        pk=resolution_id,
    )

    if resolution.status not in ('draft', 'pending'):
        messages.error(request, 'Only draft or pending resolutions can be edited.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    if request.method == 'POST':
        resolution.title = request.POST.get('title', resolution.title).strip()
        resolution.resolution_type = request.POST.get('resolution_type', resolution.resolution_type)
        resolution.authors = request.POST.get('authors', '').strip()
        resolution.sponsors = request.POST.get('sponsors', '').strip()
        resolution.whereas_clauses = request.POST.get('whereas_clauses', '').strip()
        resolution.resolved_text = request.POST.get('resolved_text', '').strip()
        resolution.resolution_body = request.POST.get('resolution_body', '').strip()
        resolution.additional_notes = request.POST.get('additional_notes', '').strip()
        vote_date_raw = request.POST.get('vote_date', '').strip()
        if vote_date_raw:
            try:
                resolution.vote_date = datetime.date.fromisoformat(vote_date_raw)
            except ValueError:
                messages.error(request, 'Invalid vote date format.')
        else:
            resolution.vote_date = None
        resolution.save(update_fields=['title', 'resolution_type', 'authors', 'sponsors', 'whereas_clauses', 'resolved_text', 'resolution_body', 'additional_notes', 'vote_date'])
        messages.success(request, 'Resolution updated.')
        if request.POST.get('save_and_preview'):
            from django.urls import reverse
            return redirect(reverse('cnb_resolution_print', kwargs={'resolution_id': resolution.pk}) + '?from_save=1')
        return redirect('cnb_resolution_detail', resolution_id=resolution.pk)

    ref_docs = GoverningDocument.enabled().prefetch_related('articles__sections')  # v3.19.1: per-document flags
    context = {'resolution': resolution, 'action': 'Edit', 'ref_docs': ref_docs}
    return render(request, 'cnb/resolution_form.html', context)


@login_required
@cnb_required
@require_POST
def add_amendment(request, resolution_id):
    """
    Add a section amendment to a resolution.
    The proposed text is provided by the user; the original text snapshot
    is captured automatically from the current section content.
    """
    resolution = get_object_or_404(Resolution, pk=resolution_id)

    if resolution.status not in ('draft', 'pending'):
        messages.error(request, 'Cannot modify a closed resolution.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    section_id = request.POST.get('section_id')
    proposed_text = request.POST.get('proposed_text', '').strip()
    scope_note = request.POST.get('scope_note', '').strip()

    if not section_id:
        messages.error(request, 'A section must be selected.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    section = get_object_or_404(Section, pk=section_id)

    # Auto-detect amendment type from the diff
    if not proposed_text:
        # Empty proposed text = deletion
        amendment_type = 'deletion'
        if scope_note:
            # Partial deletion must include the revised section text (with clause removed)
            messages.error(request, 'For a partial deletion, provide the full section text with the removed clause omitted.')
            return redirect('cnb_resolution_detail', resolution_id=resolution_id)
    elif section.content.strip() and section.content.strip() in proposed_text:
        amendment_type = 'addition'
    else:
        amendment_type = 'change'

    whole_section_delete = amendment_type == 'deletion' and not scope_note

    if section.amendment_protected:
        expires = section.protected_until
        messages.error(
            request,
            f'{section.full_identifier} is protected from new amendments until {expires}. '
            f'Reason: {section.protection_note}'
        )
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    # Update existing or create new
    amendment, created = ResolutionAmendment.objects.get_or_create(
        resolution=resolution,
        section=section,
        defaults={
            'proposed_text': proposed_text,
            'original_text_snapshot': section.content,
            'amendment_type': amendment_type,
            'scope_note': scope_note,
        }
    )
    if not created:
        amendment.proposed_text = proposed_text
        amendment.amendment_type = amendment_type
        amendment.scope_note = scope_note
        amendment.save(update_fields=['proposed_text', 'amendment_type', 'scope_note'])
        messages.success(request, f'Amendment for {section.full_identifier} updated.')
    else:
        messages.success(request, f'Amendment for {section.full_identifier} added.')

    if request.POST.get('next') == 'edit':
        return redirect('cnb_edit_resolution', resolution_id=resolution_id)
    return redirect('cnb_resolution_detail', resolution_id=resolution_id)


@login_required
@cnb_required
@require_POST
def remove_amendment(request, resolution_id, amendment_id):
    """Remove a section amendment from a resolution."""
    resolution = get_object_or_404(Resolution, pk=resolution_id)
    amendment = get_object_or_404(ResolutionAmendment, pk=amendment_id, resolution=resolution)

    if resolution.status not in ('draft', 'pending'):
        messages.error(request, 'Cannot modify a closed resolution.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    identifier = str(amendment.section)
    amendment.delete()
    messages.success(request, f'Amendment for {identifier} removed.')
    if request.POST.get('next') == 'edit':
        return redirect('cnb_edit_resolution', resolution_id=resolution_id)
    return redirect('cnb_resolution_detail', resolution_id=resolution_id)


@login_required
@cnb_required
@require_POST
def set_resolution_status(request, resolution_id):
    """
    Advance or change a resolution's status.
    Valid transitions:
      draft → pending
      pending → passed | failed | withdrawn
      draft → withdrawn
    """
    resolution = get_object_or_404(Resolution, pk=resolution_id)
    new_status = request.POST.get('status', '').strip()
    valid = {'draft', 'pending', 'passed', 'failed', 'withdrawn'}

    if new_status not in valid:
        messages.error(request, 'Invalid status.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    with transaction.atomic():
        resolution.status = new_status
        if new_status == 'passed':
            resolution.passed_at = timezone.now()
            resolution.apply_amendments(applied_by=request.user)
            messages.success(
                request,
                f'Resolution passed — {resolution.amendments.count()} section(s) updated.'
            )
        elif new_status == 'failed':
            resolution.failed_at = timezone.now()
            resolution.apply_failure_protection()
            protected_count = resolution.amendments.count()
            messages.warning(
                request,
                f'Resolution failed — {protected_count} section(s) are now amendment-protected '
                f'per their document\'s protection period.'
            )
        elif new_status == 'pending':
            messages.info(request, 'Resolution moved to pending vote.')
        elif new_status == 'withdrawn':
            messages.info(request, 'Resolution withdrawn.')
        resolution.save(update_fields=['status', 'passed_at', 'failed_at'])

    return redirect('cnb_resolution_detail', resolution_id=resolution_id)


# ── Collaborator management ───────────────────────────────────────────────────

@login_required
@cnb_required
@require_POST
def add_collaborator(request, resolution_id):
    """Add or update a collaborator on a resolution."""
    resolution = get_object_or_404(Resolution, pk=resolution_id)
    user_id = request.POST.get('user_id', '').strip()
    role = request.POST.get('role', 'viewer')

    if role not in ('viewer', 'editor'):
        messages.error(request, 'Invalid role.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    try:
        user = ParliamentUser.objects.get(pk=user_id, is_active=True)
    except (ParliamentUser.DoesNotExist, ValueError):
        messages.error(request, 'User not found.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    if user == request.user:
        messages.error(request, 'You cannot add yourself as a collaborator.')
        return redirect('cnb_resolution_detail', resolution_id=resolution_id)

    collab, created = ResolutionCollaborator.objects.get_or_create(
        resolution=resolution,
        user=user,
        defaults={'role': role, 'added_by': request.user},
    )
    if not created:
        collab.role = role
        collab.save(update_fields=['role'])
        messages.success(request, f'{user.name} updated to {collab.get_role_display()}.')
    else:
        messages.success(request, f'{user.name} added as {collab.get_role_display()}.')

    return redirect('cnb_resolution_detail', resolution_id=resolution_id)


@login_required
@cnb_required
@require_POST
def remove_collaborator(request, resolution_id, collaborator_id):
    """Remove a collaborator from a resolution."""
    resolution = get_object_or_404(Resolution, pk=resolution_id)
    collab = get_object_or_404(ResolutionCollaborator, pk=collaborator_id, resolution=resolution)
    name = collab.user.name
    collab.delete()
    messages.success(request, f'{name} removed from collaborators.')
    return redirect('cnb_resolution_detail', resolution_id=resolution_id)


# ── Print / PDF view ──────────────────────────────────────────────────────────

@login_required
def resolution_print(request, resolution_id):
    """Standalone printable resolution — no nav, formatted for Cmd+P → Save as PDF."""
    resolution = get_object_or_404(
        Resolution.objects.select_related('created_by').defer(*member_defer('created_by')).prefetch_related(
            'amendments__section__article__document',
        ),
        pk=resolution_id
    )
    return render(request, 'cnb/resolution_print.html', {'resolution': resolution})


# ── Section context API (AJAX) ────────────────────────────────────────────────

@login_required
@cnb_required
def section_context_api(request, section_id):
    """Return section data as JSON for the amendment modal, including neighboring sections."""
    section = get_object_or_404(
        Section.objects.select_related('article__document'), pk=section_id
    )
    article = section.article
    siblings = list(article.sections.order_by('display_order'))
    idx = next((i for i, s in enumerate(siblings) if s.pk == section.pk), None)

    def sibling_data(s):
        return {'number': s.number, 'title': s.title, 'content': s.content} if s else None

    prev_section = siblings[idx - 1] if idx and idx > 0 else None
    next_section = siblings[idx + 1] if idx is not None and idx < len(siblings) - 1 else None

    return JsonResponse({
        'id': section.pk,
        'identifier': section.full_identifier,
        'title': section.title,
        'content': section.content,
        'is_active': section.is_active,
        'amendment_protected': section.amendment_protected,
        'protected_until': section.protected_until.isoformat() if section.protected_until else None,
        'article_number': article.number,
        'article_title': article.title,
        'prev_section': sibling_data(prev_section),
        'next_section': sibling_data(next_section),
    })
