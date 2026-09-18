"""
Role knowledge base — a handoff wiki tied to each officer Role, so an
outgoing officer can leave procedures/notes/links for whoever holds the
role next (09-16-26, requested by Mason).

Scope, deliberately: the 10 formal `Role` positions only (President, VPs,
etc. — anything with RoleHistory/transfer_role support already). Committee
chairs (Kai, Recruitment, Education, ...) have no term-tracking model to
hang this off of today and are out of scope for this pass.

Access, per Mason's call: the page is readable by ANY logged-in member —
treated as an open wiki, not an officer-only page, so members can learn how
a role works even if they don't hold it. Editing is `officer_required`,
matching every other page that touches `Role` (manage_roles.py,
transitions.py) — there's no separate "only this role's own holder" gate,
so any officer can help keep any role's page current.

⚠️ READ PATH MUST NOT WRITE. Viewing a role with no knowledge base yet must
not create a `RoleKnowledgeBase` row — see src/models/singleton.py's whole
reason for existing, and src/tests/guards/test_singleton_rows.py, which
would have caught a `get_or_create` on the read path if this used the
`get_instance()` name/shape it walks. This model deliberately does NOT use
that name (it's per-Role, not a true one-row-total singleton, so it isn't
part of that population) — but the same failure mode applies just as much
to a per-key row, so the row is only ever created on the POST (edit) path,
where a write is happening anyway.
"""
import logging
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.views.decorators.http import require_POST

from src.models import Role, RoleKnowledgeBase, RoleKnowledgeBaseRevision, ActivityLog, ParliamentUser
from src.models.users import member_defer
from src.decorators import officer_required
from src.permissions import user_is_officer_or_chair

logger = logging.getLogger(__name__)


@login_required
def role_knowledge_base_index(request):
    """
    List every Role with a link to its knowledge base page — the general
    entry point for the "everyone can read" access Mason chose. Without
    this, the feature was only reachable by an officer following a link
    from an officer-only page; a plain member had no way to discover it.

    Deliberately open to any logged-in member, same as the pages it links to.

    Supports `?q=` (09-17-26, added once the feature was expected to grow
    past a handful of short pages) — a case-insensitive substring match
    against the role's name, its current holders' names, and the CURRENT
    revision's content only (not every past revision — matching against
    history the page doesn't display would surface a role for a word a
    visitor can't actually find once they click through). Filtered in
    Python over the same small, already-fetched candidate set the rest of
    this view uses (10 formal roles today — see the module docstring for
    why this feature is scoped to just those), same tradeoff
    `_linkable_events()` (chapter_minutes.py) makes for an identically
    small candidate set.
    """
    query = (request.GET.get('q') or '').strip()

    roles = list(Role.objects.all().order_by('name'))

    # One query for all holders instead of one per role — same N+1 shape
    # role_transitions() already avoids for the same reason.
    holders_by_role = defaultdict(list)
    for row in (
        ParliamentUser.objects.filter(roles__in=roles, member_status='Active')
        .order_by('name').values('name', 'roles')
    ):
        holders_by_role[row['roles']].append(row['name'])

    # Current revision content per role, in one query regardless of how many
    # roles or revisions exist — same shape as the holders query above.
    # RoleKnowledgeBaseRevision.Meta.ordering is (-created_at, -pk), and
    # ordering primarily by knowledge_base_id groups each KB's revisions
    # together in iteration order with the newest one first in each group,
    # so keeping only the first row seen per knowledge_base_id gives the
    # current revision without a second query or a Postgres-only
    # `distinct(...)`. `role_by_kb_id` translates that back to a role id.
    role_by_kb_id = dict(RoleKnowledgeBase.objects.values_list('pk', 'role_id'))
    current_content_by_role = {}
    if role_by_kb_id:
        seen_kb_ids = set()
        for row in (
            RoleKnowledgeBaseRevision.objects
            .filter(knowledge_base_id__in=role_by_kb_id.keys())
            .order_by('knowledge_base_id', '-created_at', '-pk')
            .values('knowledge_base_id', 'content')
        ):
            kb_id = row['knowledge_base_id']
            if kb_id in seen_kb_ids:
                continue
            seen_kb_ids.add(kb_id)
            current_content_by_role[role_by_kb_id[kb_id]] = row['content']

    has_content_ids = set(current_content_by_role.keys())

    query_lower = query.lower()
    roles_data = []
    for role in roles:
        if query_lower:
            haystack = ' '.join([
                role.name,
                ' '.join(holders_by_role.get(role.id, [])),
                current_content_by_role.get(role.id, ''),
            ]).lower()
            if query_lower not in haystack:
                continue
        roles_data.append({
            'role': role,
            'holders': holders_by_role.get(role.id, []),
            'has_content': role.id in has_content_ids,
        })

    return render(request, 'role_knowledge_base_index.html', {
        'roles_data': roles_data,
        'query': query,
        'total_roles': len(roles),
    })


@login_required
def role_knowledge_base(request, role_id):
    """
    View a role's knowledge base page. Open to any logged-in member — see
    module docstring. Does not create a `RoleKnowledgeBase` row if one
    doesn't exist yet (empty state instead); see module docstring.
    """
    role = get_object_or_404(Role, id=role_id)
    kb = RoleKnowledgeBase.objects.filter(role=role).first()

    current_revision = None
    if kb is not None:
        current_revision = (
            kb.revisions.select_related('edited_by').defer(*member_defer('edited_by')).first()
        )

    context = {
        'role': role,
        'knowledge_base': kb,
        'current_revision': current_revision,
        'can_edit': user_is_officer_or_chair(request.user),
        'revision_count': kb.revisions.count() if kb is not None else 0,
    }
    return render(request, 'officer/role_knowledge_base.html', context)


@login_required
@officer_required
@require_POST
def edit_role_knowledge_base(request, role_id):
    """
    Save a new revision of a role's knowledge base page.

    Always creates a NEW `RoleKnowledgeBaseRevision` rather than updating one
    in place — see the model docstring. `RoleKnowledgeBase` itself is
    get-or-create'd here, on the write path, which is the one place that's
    safe (an edit is a write regardless; see module docstring for why the
    read path above does not do the same thing).
    """
    role = get_object_or_404(Role, id=role_id)
    content = request.POST.get('content', '')

    with transaction.atomic():
        kb, _ = RoleKnowledgeBase.objects.get_or_create(role=role)
        RoleKnowledgeBaseRevision.objects.create(
            knowledge_base=kb, content=content, edited_by=request.user,
        )

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} updated the knowledge base for {role.name}',
        request=request,
        metadata={'action': 'edit_role_knowledge_base', 'role_id': role.id, 'role_name': role.name},
    )
    logger.info('User %s updated the knowledge base for role %s', request.user.user_id, role.id)

    messages.success(request, f'"{role.name}" knowledge base updated.')
    return redirect('role_knowledge_base', role_id=role.id)


@login_required
def role_knowledge_base_history(request, role_id):
    """
    List every past revision of a role's knowledge base page, newest first.
    Same open-to-any-member read access as the page itself.
    """
    role = get_object_or_404(Role, id=role_id)
    kb = RoleKnowledgeBase.objects.filter(role=role).first()

    revisions = []
    if kb is not None:
        revisions = list(
            kb.revisions.select_related('edited_by').defer(*member_defer('edited_by'))
        )

    context = {
        'role': role,
        'revisions': revisions,
    }
    return render(request, 'officer/role_knowledge_base_history.html', context)
