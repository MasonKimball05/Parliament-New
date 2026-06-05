from django.db import models
from django.conf import settings


class PageVisit(models.Model):
    """
    Tracks per-user, per-path visit counts for analytics.
    No timestamps — just cumulative counts to keep storage minimal.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='page_visits',
    )
    path = models.CharField(max_length=255, db_index=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'path')
        verbose_name = 'Page Visit'
        verbose_name_plural = 'Page Visits'

    def __str__(self):
        return f"{self.user} — {self.path} ({self.count})"
