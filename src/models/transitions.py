"""
Officer transition checklist models (v3.13.0), plus the role knowledge base
(v3.32.x — see RoleKnowledgeBase below).

Checklists are attached to RoleHistory rows — one member's term in one role —
so they work for transfer-created and manually-entered histories alike.
Items are admin-editable data, not code, so future chapters can maintain
them without a developer (graduation-handoff modularity goal).
"""
from django.db import models

from src.models.users import ParliamentUser, Role, RoleHistory


class TransitionChecklistItem(models.Model):
    """One task on the handoff checklist, optionally scoped to a single role."""
    role = models.ForeignKey(
        Role, null=True, blank=True, on_delete=models.CASCADE,
        related_name='checklist_items',
        help_text='Leave blank to apply this item to every role.',
    )
    text = models.CharField(max_length=300)
    order = models.PositiveIntegerField(default=0, help_text='Lower numbers appear first.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        scope = self.role.name if self.role else 'All roles'
        return f'[{scope}] {self.text}'


class TransitionChecklistStatus(models.Model):
    """Completion state of one checklist item for one RoleHistory (term)."""
    item = models.ForeignKey(TransitionChecklistItem, on_delete=models.CASCADE)
    role_history = models.ForeignKey(
        RoleHistory, on_delete=models.CASCADE, related_name='checklist_statuses',
    )
    completed_by = models.ForeignKey(
        ParliamentUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+',
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['item', 'role_history'], name='uniq_item_per_rolehistory',
            ),
        ]

    def __str__(self):
        state = 'done' if self.completed_at else 'open'
        return f'{self.item_id} for history {self.role_history_id} ({state})'


class RoleKnowledgeBase(models.Model):
    """
    One knowledge-base "page" per Role — where an outgoing officer leaves
    procedures/notes/links for whoever holds the role next.

    Scoped to `Role`, not `RoleHistory`: a checklist item belongs to one
    member's one term (v3.13.0's design, above), but institutional knowledge
    is supposed to accumulate ACROSS terms — an outgoing President's notes
    should still be there for the President two years from now, not just the
    one who takes over next semester. Tying this to RoleHistory instead would
    mean starting from a blank page every transfer.

    Deliberately holds no content field of its own. The current page content
    is `current_revision.content` — see RoleKnowledgeBaseRevision below.
    A denormalized "current content" field here, kept in sync with the
    revision table by convention, is exactly the two-sources-of-truth shape
    this codebase has been bitten by more than once (stale ledger lines,
    the pledge_phase field, the old dual IP-logging paths) — one table is
    the only way to guarantee "current" and "history" can't drift apart.
    """
    role = models.OneToOneField(Role, on_delete=models.CASCADE, related_name='knowledge_base')
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def current_revision(self):
        """Most recent revision, or None if the page has never been edited."""
        return self.revisions.first()  # Meta.ordering on the revision model is -created_at, -pk

    def __str__(self):
        return f'Knowledge base for {self.role.name}'


class RoleKnowledgeBaseRevision(models.Model):
    """
    One saved version of a role's knowledge-base page. Append-only — editing
    the page creates a new row rather than overwriting the last one, so a
    past officer's notes are never silently lost to the next edit. Same
    reasoning Mason applied to Kai record retention: this is institutional
    memory, and losing it has been a real problem for this chapter before.
    """
    knowledge_base = models.ForeignKey(
        RoleKnowledgeBase, on_delete=models.CASCADE, related_name='revisions',
    )
    content = models.TextField(blank=True)
    edited_by = models.ForeignKey(
        ParliamentUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        who = self.edited_by.name if self.edited_by else 'unknown'
        return f'{self.knowledge_base.role.name} revision by {who} at {self.created_at}'
