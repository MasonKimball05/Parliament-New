"""
The Quote Book — a lighthearted feature, requested by Mason, modeled on a
Discord "quote book" channel: whenever someone says something outrageous or
funny, anyone can add it to the book. On Parliament, the book has one
"chapter" per member — every quote attributed to a member lives on their
chapter, and the reader flips through the whole thing like an actual book
(see `src/view/quote_book.py` and the vendored `page-flip` library for the
UI side).

Design decisions, confirmed with Mason directly (09-14-26):
- Any member can submit a quote for any member's chapter.
- The member a quote is attributed to, the member who submitted it, or an
  officer, may flag it — flagging immediately soft-hides it, no approval
  step, no delay. (The submitter's flag permission was added later the
  same day, at Mason's request: "the person who submitted the quote can
  also remove it/flag it.")
- Flagging is normally a SOFT hide (`flagged_at` set), not a delete.
  Nothing in this app hard-deletes user-submitted content by default (see
  the Known Design Decisions in CLAUDE.md on Kai retention), and a soft
  hide leaves a path to restore a flag made by mistake without losing the
  quote. **One deliberate exception:** when the SAME person is both the
  submitter and the quoted member — they wrote a quote about themselves —
  flagging it deletes it outright instead. Mason's framing: "if the
  person who flag[ged] it is the author and it's for themself they can
  just delete it, no flag needed." There is no third party's record to
  preserve in that case (nobody else contributed anything: not the
  subject, not another submitter, not a flag placed by someone with a
  different reason), so there is nothing a restore would ever need to
  bring back.
- Any flag (other than the self-quote/self-delete case above) can be
  restored — by whoever flagged it, by the quoted member, or by an
  officer. See `Quote.can_be_restored_by`.
- Each quote shows who submitted it (a byline), matching how the original
  Discord channel worked.
"""
from django.conf import settings
from django.db import models


class QuoteQuerySet(models.QuerySet):
    def visible(self):
        """Not flagged. This is the set every reader-facing view should use —
        deliberately NOT the default manager, so the admin and any future
        moderation view can still see (and restore) a flagged quote without
        a `.all_objects()`-style workaround. See the note on default
        managers in `CompletedItems.md` re: why this codebase avoids a
        filtered default manager on models with reverse relations."""
        return self.filter(flagged_at__isnull=True)


class Quote(models.Model):
    """One quote, attributed to one member's chapter."""

    #: Whose chapter this quote lives on — the person who said it.
    quoted_member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quotes_about_them',
        help_text='The member this quote is attributed to — whose chapter it appears on.',
    )
    text = models.TextField(help_text='The quote itself.')
    context = models.CharField(
        max_length=300, blank=True,
        help_text='Optional — where/when/why this was said, if it helps the joke land.',
    )

    #: Who added the quote. SET_NULL, not CASCADE — the quote is about the
    #: quoted_member and should survive the submitter's account being
    #: removed later; only the byline is lost, matching FeedbackRequest's
    #: submitted_by pattern in src/models/security.py.
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quotes_submitted',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    #: Soft-hide. Set the moment the quoted member (or an officer) flags it
    #: — no review queue, no delay, per Mason's answer. NULL = visible.
    flagged_at = models.DateTimeField(null=True, blank=True)
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quotes_flagged',
        help_text='Who flagged this quote — the quoted member themselves, or an officer.',
    )

    objects = QuoteQuerySet.as_manager()

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['quoted_member', 'flagged_at']),
        ]

    def __str__(self):
        preview = self.text[:40] + ('…' if len(self.text) > 40 else '')
        return f'"{preview}" — {self.quoted_member}'

    def can_be_flagged_by(self, user):
        """
        The quoted member, the submitter, or an officer, may flag this
        quote. `is_officer` (see `ParliamentUser`) already covers
        `is_admin`, so checking it alone covers both. `submitted_by`
        added 09-14-26 at Mason's request — the person who wrote a quote
        down can also take it back, not just the person it's about.
        """
        pk = getattr(user, 'pk', None)
        if pk is not None and pk in (self.quoted_member_id, self.submitted_by_id):
            return True
        return bool(getattr(user, 'is_officer', False))

    def can_be_deleted_outright_by(self, user):
        """
        True only when `user` is BOTH the submitter and the quoted member
        of this exact quote — they wrote it about themselves. In that one
        case, `flag_quote` deletes the row outright instead of soft-hiding
        it (see the view). Nobody else qualifies: not an officer, not the
        quoted member alone, not the submitter alone — each of those
        cases still has a party whose record a restore might need to
        bring back, so they stay on the soft-hide/restore path.
        """
        pk = getattr(user, 'pk', None)
        return (
            pk is not None
            and pk == self.submitted_by_id
            and pk == self.quoted_member_id
        )

    def can_be_restored_by(self, user):
        """
        Who may un-flag (restore) an already-flagged quote — added
        09-14-26, after Mason asked "can the person who submitted the flag
        [...] remove it if they change their mind" and confirmed yes.

        Three standings, each independently sufficient:
        - Whoever flagged it (`flagged_by`) — reversing your own call is
          not the same thing as a stranger overriding someone else's;
          restoring a flag you placed yourself has no conflict-of-interest
          question the way "approving" someone else's removal would.
        - The quoted member — per the original design, they have final
          say over their own chapter regardless of who placed the flag
          (an officer flagging on their behalf, for instance).
        - Any officer — the general moderation/audit backstop, same as
          `can_be_flagged_by`.

        Not automatically the submitter UNLESS they are also who flagged
        it or the quoted member — writing a quote down doesn't by itself
        give you a say over whether it stays hidden (matches
        `can_be_flagged_by`'s original scope before `submitted_by` was
        added there); it's the act of flagging or being the subject that
        grants standing, not authorship alone.
        """
        pk = getattr(user, 'pk', None)
        if pk is not None and pk in (self.quoted_member_id, self.flagged_by_id):
            return True
        return bool(getattr(user, 'is_officer', False))
