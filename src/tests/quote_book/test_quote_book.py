"""
Tests for the Quote Book feature (added 09-14-26).

Design recap (full writeup in src/models/quote_book.py and
src/view/quote_book.py): any member can add a quote to another member's
"chapter"; the quoted member (or an officer) can flag a quote, which
immediately soft-hides it — no review step; submitter is shown on every
quote; the reader is a real animated page-flip UI
(templates/quote_book/book.html), fed by one JSON payload built in
`quote_book()`.

What this module covers:
  - Quote model: the `visible()` queryset excludes flagged rows,
    `can_be_flagged_by` permission logic (quoted member, officer, nobody
    else).
  - `quote_book` view: login + page-toggle gating, chapters grouped
    correctly, flagged quotes excluded from the JSON payload, and the
    payload is script-safe (the `_script_safe_json` XSS fix — same shape
    as the v3.13.1 roles_json finding, since quote text is free-text
    member input rendered straight into a <script> block).
  - `submit_quote` view: login + page-toggle gating, excludes Removed
    members from the picker, required-field validation, and — the
    discipline this codebase established in the v3.29.10 fix — the
    ActivityLog entry it writes is structural-only and never contains the
    quote text itself.
  - `flag_quote` view: POST-only, permission-gated (quoted member or
    officer, nobody else), idempotent (a second flag does not re-log or
    error), and its ActivityLog entry is likewise structural-only.
  - `review_flagged_quotes` / `restore_quote` (added 09-14-26, after Mason
    asked where officers could review what had been flagged): officer-only
    listing of flagged quotes, and a POST-only restore that clears the
    flag and is idempotent the same way flagging is.
  - A query-budget ceiling for `quote_book` (QueryBudgetMixin, per
    src/tests/guards/test_query_budgets.py's convention) — the view's
    docstring claims "one query, not one per chapter", so that claim gets
    a ratchet.
"""
import json

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.models import ActivityLog, ParliamentUser, Quote
from src.models_feature_flags import PageToggle
from src.tests.guards.test_query_budgets import (
    QueryBudgetMixin, STALENESS_SLACK, warm_singleton_rows,
)


def make_member(uid, name=None, member_type='Member', member_status='Active', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name or f'Member {uid}', username=uid,
        member_type=member_type, member_status=member_status, is_admin=is_admin,
    )
    user.set_password('quote-book-test-pass-12345!')
    user.save()
    return user


def enable_quote_book_toggle():
    """
    `@require_page_enabled('quote_book')` gates every view in this module.
    Production seeds the row via `seed_feature_flags`, but a fresh test DB
    has no PageToggle rows at all — and PageToggle fails CLOSED on a
    missing row (unlike FeatureFlag's Python-side fail-open asymmetry —
    see CLAUDE.md's "Known Design Decisions" for why that distinction
    matters and is not a bug to fix here). Every test that exercises a
    gated view has to seed this first or every response is a 403.
    """
    PageToggle.objects.get_or_create(
        url_name='quote_book',
        defaults={'display_name': 'Quote Book', 'is_enabled': True},
    )


class QuoteModelTests(TestCase):
    def setUp(self):
        self.quoted = make_member('qb-quoted', 'Quoted Quincy')
        self.submitter = make_member('qb-submitter', 'Sub Sam')
        self.officer = make_member('qb-officer', 'Officer Olive', member_type='Officer')
        self.bystander = make_member('qb-bystander', 'Bystander Bea')

    def test_visible_excludes_flagged_rows(self):
        visible_q = Quote.objects.create(
            quoted_member=self.quoted, text='A visible quote', submitted_by=self.submitter,
        )
        flagged_q = Quote.objects.create(
            quoted_member=self.quoted, text='A flagged quote', submitted_by=self.submitter,
        )
        flagged_q.flagged_by = self.quoted
        from django.utils import timezone
        flagged_q.flagged_at = timezone.now()
        flagged_q.save(update_fields=['flagged_at', 'flagged_by'])

        visible_ids = set(Quote.objects.visible().values_list('pk', flat=True))
        self.assertIn(visible_q.pk, visible_ids)
        self.assertNotIn(flagged_q.pk, visible_ids)

    def test_quoted_member_can_flag_their_own_quote(self):
        q = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.submitter)
        self.assertTrue(q.can_be_flagged_by(self.quoted))

    def test_officer_can_flag_any_quote(self):
        q = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.submitter)
        self.assertTrue(q.can_be_flagged_by(self.officer))

    def test_submitter_can_flag_their_own_submission(self):
        # Added later 09-14-26, at Mason's request: "the person who
        # submitted the quote can also remove it/flag it" — not just the
        # quoted member or an officer.
        q = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.submitter)
        self.assertTrue(q.can_be_flagged_by(self.submitter))

    def test_uninvolved_member_cannot_flag(self):
        q = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.submitter)
        self.assertFalse(q.can_be_flagged_by(self.bystander))

    def test_can_be_deleted_outright_only_when_submitter_and_quoted_member_are_the_same_person(self):
        # The narrow case: someone wrote a quote about THEMSELVES.
        self_quote = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.quoted)
        self.assertTrue(self_quote.can_be_deleted_outright_by(self.quoted))

        # Not when they're only the submitter (quote is about someone else)...
        other_quote = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.submitter)
        self.assertFalse(other_quote.can_be_deleted_outright_by(self.submitter))
        # ...not when they're only the quoted member (someone else wrote it)...
        self.assertFalse(other_quote.can_be_deleted_outright_by(self.quoted))
        # ...and not for an officer, even on a genuine self-quote — this
        # shortcut is for the author removing their own words about
        # themselves, not a general officer power.
        self.assertFalse(self_quote.can_be_deleted_outright_by(self.officer))

    def _flagged_by(self, flagger):
        from django.utils import timezone
        q = Quote.objects.create(quoted_member=self.quoted, text='x', submitted_by=self.submitter)
        q.flagged_at = timezone.now()
        q.flagged_by = flagger
        q.save(update_fields=['flagged_at', 'flagged_by'])
        return q

    def test_flagger_can_restore_their_own_flag(self):
        # Mason: "can the person who submitted the flag [...] remove it if
        # they change their mind" — yes, reversing your own call.
        q = self._flagged_by(self.officer)
        self.assertTrue(q.can_be_restored_by(self.officer))

    def test_quoted_member_can_restore_even_if_someone_else_flagged_it(self):
        q = self._flagged_by(self.officer)
        self.assertTrue(q.can_be_restored_by(self.quoted))

    def test_any_officer_can_restore(self):
        # A *different* officer than the one who flagged it — the general
        # moderation backstop, not tied to being the original flagger.
        another_officer = make_member('qb-officer2', 'Officer Two', member_type='Officer')
        q = self._flagged_by(self.quoted)
        self.assertTrue(q.can_be_restored_by(another_officer))

    def test_uninvolved_bystander_cannot_restore(self):
        q = self._flagged_by(self.quoted)
        self.assertFalse(q.can_be_restored_by(self.bystander))

    def test_submitter_cannot_restore_just_for_having_written_it(self):
        q = self._flagged_by(self.quoted)
        self.assertFalse(q.can_be_restored_by(self.submitter))


class QuoteBookViewTests(TestCase):
    def setUp(self):
        enable_quote_book_toggle()
        self.viewer = make_member('qb-viewer', 'Viewer Vic')
        self.quoted_a = make_member('qb-a', 'Alice Adams')
        self.quoted_b = make_member('qb-b', 'Bob Baker')
        self.submitter = make_member('qb-sub', 'Sub Sam')
        self.client = Client()

    def test_requires_login(self):
        resp = self.client.get(reverse('quote_book'))
        self.assertNotEqual(resp.status_code, 200)

    def test_403_when_page_toggle_disabled(self):
        PageToggle.objects.filter(url_name='quote_book').update(is_enabled=False)
        self.client.force_login(self.viewer)
        resp = self.client.get(reverse('quote_book'))
        self.assertEqual(resp.status_code, 403)

    def test_chapters_grouped_by_quoted_member(self):
        Quote.objects.create(quoted_member=self.quoted_a, text='Quote 1', submitted_by=self.submitter)
        Quote.objects.create(quoted_member=self.quoted_a, text='Quote 2', submitted_by=self.submitter)
        Quote.objects.create(quoted_member=self.quoted_b, text='Quote 3', submitted_by=self.submitter)

        self.client.force_login(self.viewer)
        resp = self.client.get(reverse('quote_book'))
        self.assertEqual(resp.status_code, 200)

        chapters = json.loads(resp.context['chapters_json'])
        by_member = {c['member_name']: c for c in chapters}
        self.assertEqual(len(by_member['Alice Adams']['quotes']), 2)
        self.assertEqual(len(by_member['Bob Baker']['quotes']), 1)

    def test_flagged_quote_excluded_from_payload(self):
        visible = Quote.objects.create(quoted_member=self.quoted_a, text='Keep me', submitted_by=self.submitter)
        flagged = Quote.objects.create(quoted_member=self.quoted_a, text='Remove me', submitted_by=self.submitter)
        from django.utils import timezone
        flagged.flagged_at = timezone.now()
        flagged.flagged_by = self.quoted_a
        flagged.save(update_fields=['flagged_at', 'flagged_by'])

        self.client.force_login(self.viewer)
        resp = self.client.get(reverse('quote_book'))
        body = resp.content.decode()
        self.assertIn('Keep me', body)
        self.assertNotIn('Remove me', body)

    def test_empty_book_has_quotes_false(self):
        self.client.force_login(self.viewer)
        resp = self.client.get(reverse('quote_book'))
        self.assertFalse(resp.context['has_quotes'])

    def test_quote_text_is_script_safe_against_tag_breakout(self):
        """
        v3.13.1's `_script_safe_json` fix, applied here for the same reason:
        quote text is free-text member input rendered straight into
        book.html's <script> block. A quote containing '</script>' must not
        be able to terminate the tag early.
        """
        Quote.objects.create(
            quoted_member=self.quoted_a,
            text='nice try </script><script>alert(1)</script>',
            submitted_by=self.submitter,
        )
        self.client.force_login(self.viewer)
        resp = self.client.get(reverse('quote_book'))
        body = resp.content.decode()
        self.assertNotIn('</script><script>alert(1)</script>', body)
        # The escaped form must still be present — this proves the content
        # made it into the payload rather than being silently dropped.
        # `_script_safe_json` only escapes '<' (not '>'), which is enough:
        # neither '</script>' nor '<!--' can form without a literal '<'.
        self.assertIn('\\u003c/script>', body)


class SubmitQuoteViewTests(TestCase):
    def setUp(self):
        enable_quote_book_toggle()
        self.author = make_member('qb-author', 'Author Amy')
        self.quoted = make_member('qb-target', 'Target Tara')
        self.removed = make_member('qb-gone', 'Gone Gary', member_status='Removed')
        self.client = Client()
        self.client.force_login(self.author)

    def test_get_excludes_removed_members(self):
        resp = self.client.get(reverse('submit_quote'))
        members = list(resp.context['members'])
        self.assertIn(self.quoted, members)
        self.assertNotIn(self.removed, members)

    def test_post_creates_quote_and_redirects(self):
        resp = self.client.post(reverse('submit_quote'), {
            'quoted_member': self.quoted.pk,
            'text': 'Something outrageous',
            'context': 'At chapter meeting',
        })
        self.assertRedirects(resp, reverse('quote_book'))
        quote = Quote.objects.get(text='Something outrageous')
        self.assertEqual(quote.quoted_member, self.quoted)
        self.assertEqual(quote.submitted_by, self.author)
        self.assertEqual(quote.context, 'At chapter meeting')
        self.assertIsNone(quote.flagged_at)

    def test_post_missing_text_does_not_create(self):
        resp = self.client.post(reverse('submit_quote'), {
            'quoted_member': self.quoted.pk,
            'text': '',
        })
        self.assertRedirects(resp, reverse('submit_quote'))
        self.assertEqual(Quote.objects.count(), 0)

    def test_post_missing_quoted_member_does_not_create(self):
        resp = self.client.post(reverse('submit_quote'), {
            'quoted_member': '',
            'text': 'Something',
        })
        self.assertRedirects(resp, reverse('submit_quote'))
        self.assertEqual(Quote.objects.count(), 0)

    def test_activity_log_never_contains_the_quote_text(self):
        """
        The discipline this codebase established fixing the v3.29.10
        FeedbackRequest leak: ActivityLog.description is readable by every
        officer and chair, so free-text a member wrote does not belong in
        it — structural facts only (who/who/id).
        """
        secret_text = 'a wildly specific and identifying thing someone said'
        self.client.post(reverse('submit_quote'), {
            'quoted_member': self.quoted.pk,
            'text': secret_text,
            'context': 'a distinctive context string',
        })
        entry = ActivityLog.objects.get(action_type='quote_submitted')
        self.assertNotIn(secret_text, entry.description)
        self.assertNotIn('a distinctive context string', entry.description)
        # It should still be traceable structurally.
        self.assertIn(str(Quote.objects.get().pk), entry.description)

    def test_requires_login(self):
        anon = Client()
        resp = anon.get(reverse('submit_quote'))
        self.assertNotEqual(resp.status_code, 200)

    def test_403_when_page_toggle_disabled(self):
        PageToggle.objects.filter(url_name='quote_book').update(is_enabled=False)
        resp = self.client.get(reverse('submit_quote'))
        self.assertEqual(resp.status_code, 403)


class FlagQuoteViewTests(TestCase):
    def setUp(self):
        enable_quote_book_toggle()
        self.quoted = make_member('qb-fq-target', 'Flag Target')
        self.officer = make_member('qb-fq-officer', 'Flag Officer', member_type='Officer')
        self.submitter = make_member('qb-fq-sub', 'Flag Submitter')
        self.bystander = make_member('qb-fq-bystander', 'Flag Bystander')

    def _new_quote(self):
        return Quote.objects.create(
            quoted_member=self.quoted, text='Flag me maybe', submitted_by=self.submitter,
        )

    def test_get_not_allowed(self):
        quote = self._new_quote()
        client = Client()
        client.force_login(self.quoted)
        resp = client.get(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_uninvolved_member_gets_403_and_quote_stays_visible(self):
        quote = self._new_quote()
        client = Client()
        client.force_login(self.bystander)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 403)
        quote.refresh_from_db()
        self.assertIsNone(quote.flagged_at)

    def test_quoted_member_can_flag(self):
        quote = self._new_quote()
        client = Client()
        client.force_login(self.quoted)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'success': True, 'deleted': False})
        quote.refresh_from_db()
        self.assertIsNotNone(quote.flagged_at)
        self.assertEqual(quote.flagged_by, self.quoted)

    def test_officer_can_flag(self):
        quote = self._new_quote()
        client = Client()
        client.force_login(self.officer)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        quote.refresh_from_db()
        self.assertIsNotNone(quote.flagged_at)

    def test_submitter_can_flag_their_own_submission(self):
        # `self.submitter` is neither the quoted member nor an officer —
        # only the person who wrote it down.
        quote = self._new_quote()
        client = Client()
        client.force_login(self.submitter)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'success': True, 'deleted': False})
        quote.refresh_from_db()
        self.assertIsNotNone(quote.flagged_at)
        self.assertEqual(quote.flagged_by, self.submitter)

    def test_flagging_is_idempotent(self):
        quote = self._new_quote()
        client = Client()
        client.force_login(self.quoted)
        client.post(reverse('flag_quote', args=[quote.pk]))
        first_flagged_at = Quote.objects.get(pk=quote.pk).flagged_at

        # A second flag call (e.g. a double-click, or the officer flagging
        # after the quoted member already did) must not error, must not
        # move the timestamp, and must not write a second ActivityLog row.
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'success': True, 'deleted': False})
        quote.refresh_from_db()
        self.assertEqual(quote.flagged_at, first_flagged_at)
        self.assertEqual(ActivityLog.objects.filter(action_type='quote_flagged').count(), 1)

    def test_activity_log_never_contains_the_quote_text(self):
        quote = self._new_quote()
        client = Client()
        client.force_login(self.quoted)
        client.post(reverse('flag_quote', args=[quote.pk]))
        entry = ActivityLog.objects.get(action_type='quote_flagged')
        self.assertNotIn('Flag me maybe', entry.description)
        self.assertIn(str(quote.pk), entry.description)

    def test_requires_login(self):
        quote = self._new_quote()
        anon = Client()
        resp = anon.post(reverse('flag_quote', args=[quote.pk]))
        self.assertNotEqual(resp.status_code, 200)


class DeleteOutrightTests(TestCase):
    """
    Added 09-14-26, second round of feedback: Mason submitted a quote
    about himself, flagged it, and asked why he couldn't remove his own
    (already-fixed by `can_be_restored_by`) — then asked for a bigger
    change: "if the person who flag[ged] it is the author and it's for
    themself they can just delete it, no flag needed." When submitted_by
    and quoted_member are the same person, `flag_quote` now deletes the
    row outright instead of soft-hiding it — there's no restore path for
    this case because there is nothing left to restore.
    """
    def setUp(self):
        enable_quote_book_toggle()
        self.author = make_member('qb-del-author', 'Self Author')
        self.officer = make_member('qb-del-officer', 'Delete Officer', member_type='Officer')
        self.other_member = make_member('qb-del-other', 'Someone Else')

    def _self_quote(self):
        return Quote.objects.create(
            quoted_member=self.author, text='About myself', submitted_by=self.author,
        )

    def test_self_authored_self_quoted_is_deleted_not_flagged(self):
        quote = self._self_quote()
        client = Client()
        client.force_login(self.author)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'success': True, 'deleted': True})
        self.assertFalse(Quote.objects.filter(pk=quote.pk).exists())

    def test_activity_log_records_the_deletion_structurally(self):
        quote = self._self_quote()
        quote_pk = quote.pk
        client = Client()
        client.force_login(self.author)
        client.post(reverse('flag_quote', args=[quote.pk]))
        entry = ActivityLog.objects.get(action_type='quote_deleted')
        self.assertNotIn('About myself', entry.description)
        self.assertIn(str(quote_pk), entry.description)

    def test_officer_flagging_someone_elses_self_quote_still_soft_hides(self):
        # The shortcut only applies to the actual author-and-subject
        # removing their OWN quote — an officer moderating the same quote
        # still goes through the normal flag/restore path.
        quote = self._self_quote()
        client = Client()
        client.force_login(self.officer)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'success': True, 'deleted': False})
        self.assertTrue(Quote.objects.filter(pk=quote.pk).exists())
        quote.refresh_from_db()
        self.assertIsNotNone(quote.flagged_at)

    def test_not_triggered_when_quote_is_about_someone_else(self):
        # self.author submitted it, but it's about other_member — flag,
        # not delete, even though self.author can still flag it (they're
        # the submitter).
        quote = Quote.objects.create(
            quoted_member=self.other_member, text='About someone else', submitted_by=self.author,
        )
        client = Client()
        client.force_login(self.author)
        resp = client.post(reverse('flag_quote', args=[quote.pk]))
        self.assertEqual(resp.json(), {'success': True, 'deleted': False})
        self.assertTrue(Quote.objects.filter(pk=quote.pk).exists())

    def test_delete_not_flag_flag_shown_in_quote_book_payload(self):
        self._self_quote()
        client = Client()
        client.force_login(self.author)
        resp = client.get(reverse('quote_book'))
        chapters = json.loads(resp.context['chapters_json'])
        quote_entry = chapters[0]['quotes'][0]
        self.assertTrue(quote_entry['delete_not_flag'])
        self.assertTrue(quote_entry['can_flag'])


class QuoteBookQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `quote_book()`'s own docstring claims "One query, not one per
    chapter" — select_related + defer on quoted_member/submitted_by,
    grouped in Python. That claim is exactly the kind of thing that rots
    silently (see this module's own docstring on the two 08-02-26
    regressions), so it gets a ratchet like every other page here.

    Measured 09-14-26 against 6 chapters x 4 quotes = 24 rows. The bulk of
    this (most of the ×2 device-lookup pairs in a failure's "repeated query
    shapes" list) is the same per-request 2FA/context-processor baseline
    every authenticated page pays — see this module's own `home` /
    `view_kai_reports` budgets for the same overhead. Nothing in
    `quote_book()` itself scales with chapter or quote count; the whole
    point of this ceiling is to catch it if that ever stops being true.

    28 -> 29, same day: `flagged_count` used to be officer-only (a member
    who couldn't act on it didn't pay a query for it). Mason then asked
    for the flagger/quoted-member self-service restore path, which means
    a non-officer can now have standing too — so the count is computed
    for everyone (one extra `Quote.objects.filter(...).count()` query),
    not just officers. A real, deliberate query for a real feature.
    """
    BUDGET = 29

    def setUp(self):
        enable_quote_book_toggle()
        self.viewer = make_member('qb-budget-viewer', 'Budget Viewer')
        members = [make_member(f'qb-budget-m{i}', f'Chapter Member {i}') for i in range(6)]
        submitter = make_member('qb-budget-sub', 'Budget Submitter')
        for m in members:
            for i in range(4):
                Quote.objects.create(quoted_member=m, text=f'Quote {i}', submitted_by=submitter)

    def test_quote_book_stays_within_budget(self):
        self.assert_within_budget(self.viewer, 'quote_book', self.BUDGET)


class LineBreaksSurviveRenderingTests(TestCase):
    """
    Mason: quotes submitted with a line break ("hit Enter") were rendering
    on one line — the newline character wasn't being stripped, but the
    browser's default text flow collapses it like any other whitespace.
    Fixed with `white-space: pre-wrap` on the rendered quote text/context.
    """
    def setUp(self):
        enable_quote_book_toggle()
        self.quoted = make_member('qb-lb-target', 'Line Break Target')
        self.submitter = make_member('qb-lb-sub', 'Line Break Sub')
        self.viewer = make_member('qb-lb-viewer', 'Line Break Viewer')

    def test_newline_in_quote_text_survives_into_the_response(self):
        Quote.objects.create(
            quoted_member=self.quoted,
            text='First line\nSecond line',
            submitted_by=self.submitter,
        )
        client = Client()
        client.force_login(self.viewer)
        resp = client.get(reverse('quote_book'))
        body = resp.content.decode()
        # The JSON payload carries the newline as `\n` (its JSON-escaped
        # form), which the browser then renders as a real line break
        # because of white-space: pre-wrap on the blockquote — assert both
        # the payload isn't collapsed AND the CSS rule that renders it
        # correctly is actually shipped.
        self.assertIn('First line\\nSecond line', body)
        self.assertIn('white-space: pre-wrap', body)


class FlaggedQuoteReviewTests(TestCase):
    """
    Added 09-14-26 after Mason asked where officers could review what had
    been flagged. Flagging itself still needs no approval (confirmed
    design) — this is a place to look afterward, not a gate beforehand.

    Extended the same day, second round: Mason asked "did you set perms
    so the person who submitted the flag cannot approve its removal?"
    then, regardless, said he wants the person who flagged something to
    be able to see it and un-flag it themselves. The answer that
    satisfies both: restoring your OWN flag has no approval question (you
    are not overriding anyone), and `review_flagged_quotes` /
    `restore_quote` are scoped per `Quote.can_be_restored_by` — an
    officer sees everything, anyone else sees only what they have
    standing over (a quote they flagged, or a flagged quote on their own
    chapter). An uninvolved bystander sees neither the list entry nor
    gets to restore it.
    """
    def setUp(self):
        enable_quote_book_toggle()
        self.officer = make_member('qb-rev-officer', 'Review Officer', member_type='Officer')
        self.member = make_member('qb-rev-member', 'Regular Member')
        self.quoted = make_member('qb-rev-target', 'Review Target')
        self.submitter = make_member('qb-rev-sub', 'Review Submitter')

    def _flagged_quote(self, flagger=None):
        q = Quote.objects.create(quoted_member=self.quoted, text='Flagged text', submitted_by=self.submitter)
        from django.utils import timezone
        q.flagged_at = timezone.now()
        q.flagged_by = flagger or self.quoted
        q.save(update_fields=['flagged_at', 'flagged_by'])
        return q

    def test_officer_can_view_review_page(self):
        self._flagged_quote()
        client = Client()
        client.force_login(self.officer)
        resp = client.get(reverse('review_flagged_quotes'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Flagged text', resp.content.decode())

    def test_bystander_gets_200_but_an_empty_list(self):
        """
        Not a 403 — a bystander with no standing over ANY flagged quote is
        still allowed to load the (empty) page, same as an officer with
        nothing flagged. What they must not see is the CONTENT of a quote
        they have no standing over — that's the query-scoping test below.
        """
        self._flagged_quote()  # flagged_by=self.quoted — self.member has no standing
        client = Client()
        client.force_login(self.member)
        resp = client.get(reverse('review_flagged_quotes'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Flagged text', resp.content.decode())
        self.assertEqual(len(resp.context['flagged_quotes']), 0)

    def test_quoted_member_sees_a_quote_they_flagged_themselves(self):
        self._flagged_quote(flagger=self.quoted)
        client = Client()
        client.force_login(self.quoted)
        resp = client.get(reverse('review_flagged_quotes'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Flagged text', resp.content.decode())

    def test_flagger_sees_a_quote_they_flagged_even_if_not_the_quoted_member(self):
        # An officer flags someone else's quote — the ORIGINAL flagger
        # (not the quoted member) should still see it on their own list.
        self._flagged_quote(flagger=self.officer)
        client = Client()
        client.force_login(self.officer)
        resp = client.get(reverse('review_flagged_quotes'))
        self.assertIn('Flagged text', resp.content.decode())

    def test_officer_can_restore(self):
        quote = self._flagged_quote()
        client = Client()
        client.force_login(self.officer)
        resp = client.post(reverse('restore_quote', args=[quote.pk]))
        self.assertRedirects(resp, reverse('review_flagged_quotes'))
        quote.refresh_from_db()
        self.assertIsNone(quote.flagged_at)
        self.assertIsNone(quote.flagged_by)

    def test_quoted_member_can_restore_their_own_flag(self):
        quote = self._flagged_quote(flagger=self.quoted)
        client = Client()
        client.force_login(self.quoted)
        resp = client.post(reverse('restore_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 302)
        quote.refresh_from_db()
        self.assertIsNone(quote.flagged_at)

    def test_restored_quote_reappears_in_the_book(self):
        quote = self._flagged_quote()
        client = Client()
        client.force_login(self.officer)
        client.post(reverse('restore_quote', args=[quote.pk]))

        viewer = make_member('qb-rev-viewer', 'Review Viewer')
        reader = Client()
        reader.force_login(viewer)
        resp = reader.get(reverse('quote_book'))
        self.assertIn('Flagged text', resp.content.decode())

    def test_uninvolved_bystander_cannot_restore(self):
        quote = self._flagged_quote()
        client = Client()
        client.force_login(self.member)
        resp = client.post(reverse('restore_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 403)
        quote.refresh_from_db()
        self.assertIsNotNone(quote.flagged_at)

    def test_restore_is_idempotent(self):
        quote = self._flagged_quote()
        client = Client()
        client.force_login(self.officer)
        client.post(reverse('restore_quote', args=[quote.pk]))
        # Second restore on an already-restored quote must not error or
        # write a second ActivityLog row.
        resp = client.post(reverse('restore_quote', args=[quote.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ActivityLog.objects.filter(action_type='quote_restored').count(), 1)

    def test_activity_log_never_contains_the_quote_text(self):
        quote = self._flagged_quote()
        client = Client()
        client.force_login(self.officer)
        client.post(reverse('restore_quote', args=[quote.pk]))
        entry = ActivityLog.objects.get(action_type='quote_restored')
        self.assertNotIn('Flagged text', entry.description)
        self.assertIn(str(quote.pk), entry.description)

    def test_flagged_count_scoped_by_standing(self):
        self._flagged_quote(flagger=self.quoted)
        # Give the officer something visible so has_quotes is True too.
        Quote.objects.create(quoted_member=self.quoted, text='Visible one', submitted_by=self.submitter)

        officer_client = Client()
        officer_client.force_login(self.officer)
        resp = officer_client.get(reverse('quote_book'))
        self.assertEqual(resp.context['flagged_count'], 1)

        # The quoted member flagged their own quote — they have standing,
        # so they see a count too, not just officers.
        quoted_client = Client()
        quoted_client.force_login(self.quoted)
        resp = quoted_client.get(reverse('quote_book'))
        self.assertEqual(resp.context['flagged_count'], 1)

        # An uninvolved bystander has no standing over this one — None,
        # not 0, so the template can tell "nothing" from "not relevant".
        member_client = Client()
        member_client.force_login(self.member)
        resp = member_client.get(reverse('quote_book'))
        self.assertIsNone(resp.context['flagged_count'])
