"""
Officer transition checklist models (v3.13.0).

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
