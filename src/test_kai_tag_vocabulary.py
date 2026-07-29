"""
Kai tags are a closed vocabulary — regression tests (v3.16.3, 07-28-26).

WHY
---
`KaiReport.tags` was free text. `_kai_search_q` searches tags with no permission
gate, the report list card renders them, and the CSV export writes them — all at
`can_view_report_list` level. So a chair typing "smith-incident" into a tag
handed a name to exactly the reviewers the app denies `submitted_by` and
`targeted_to`, walking straight through the redaction v3.16.2 and v3.16.3 built.

The vocabulary is what makes the unconditional `Q(tags__icontains=...)` in
`_kai_search_q` safe. These tests are the thing standing between that comment
and a future free-text field.
"""

from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from .models import (
    ParliamentUser, Committee, KaiReport, KaiReportActivity,
    KaiReportTemplate, KaiMemberPermission,
)


class NormalizeTagsTests(SimpleTestCase):
    """KaiReport.normalize_tags is the single chokepoint — exercise it directly."""

    def test_accepts_vocabulary_values(self):
        accepted, rejected = KaiReport.normalize_tags(['urgent', 'follow-up'])
        self.assertEqual(accepted, ['urgent', 'follow-up'])
        self.assertEqual(rejected, [])

    def test_accepts_a_comma_separated_string(self):
        accepted, rejected = KaiReport.normalize_tags('urgent, escalated')
        self.assertEqual(accepted, ['urgent', 'escalated'])
        self.assertEqual(rejected, [])

    def test_is_case_and_separator_insensitive(self):
        for raw in ('FOLLOW-UP', 'Follow Up', 'follow_up', '  follow-up  ', 'Follow-Up Needed'):
            accepted, rejected = KaiReport.normalize_tags([raw])
            self.assertEqual(accepted, ['follow-up'], 'failed on %r' % raw)
            self.assertEqual(rejected, [])

    def test_rejects_anything_outside_the_vocabulary(self):
        accepted, rejected = KaiReport.normalize_tags(
            ['urgent', 'smith-incident', 'John Doe'])
        self.assertEqual(accepted, ['urgent'])
        self.assertEqual(rejected, ['smith-incident', 'John Doe'])

    def test_drops_duplicates_and_blanks_but_keeps_order(self):
        accepted, rejected = KaiReport.normalize_tags('escalated, , urgent, Escalated,')
        self.assertEqual(accepted, ['escalated', 'urgent'])
        self.assertEqual(rejected, [])

    def test_handles_none_and_empty(self):
        self.assertEqual(KaiReport.normalize_tags(None), ([], []))
        self.assertEqual(KaiReport.normalize_tags([]), ([], []))
        self.assertEqual(KaiReport.normalize_tags(''), ([], []))

    def test_vocabulary_has_no_duplicate_values(self):
        self.assertEqual(len(KaiReport.ALLOWED_TAGS), len(set(KaiReport.ALLOWED_TAGS)))

    def test_no_vocabulary_entry_looks_like_a_person(self):
        """
        Rule 1 on the model: tags are visible to every list-level reviewer, so
        none of them may name or describe an individual. This can't be enforced
        mechanically, but it can be made loud — if you add a tag and this fails,
        read the comment on KaiReport.TAG_CHOICES before changing the test.
        """
        for value in KaiReport.ALLOWED_TAGS:
            self.assertNotIn(' ', value, 'tag values are slugs: %r' % value)
            self.assertEqual(value, value.lower())


class TagModelValidationTests(TestCase):
    """clean() is the backstop for surfaces that don't exist yet (admin, future forms)."""

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='tsub', name='Tag Submitter', username='tsub', member_type='Member')

    def _report(self, tags):
        return KaiReport(
            title='t', category='behavioral', description='d',
            submitted_by=self.submitter, tags=tags,
        )

    def test_clean_accepts_vocabulary(self):
        self._report(['urgent']).clean()  # must not raise

    def test_clean_rejects_free_text(self):
        with self.assertRaises(ValidationError) as ctx:
            self._report(['urgent', 'smith-incident']).clean()
        self.assertIn('tags', ctx.exception.message_dict)
        self.assertIn('smith-incident', str(ctx.exception))

    def test_template_suggested_tags_obey_the_same_vocabulary(self):
        template = KaiReportTemplate(
            name='n', description='d', category='behavioral',
            title_template='t', description_template='d',
            suggested_tags=['definitely not a tag'],
        )
        with self.assertRaises(ValidationError) as ctx:
            template.clean()
        self.assertIn('suggested_tags', ctx.exception.message_dict)


class TagWriteSiteTests(TestCase):
    """Every write path must go through the vocabulary, not just the model."""

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='wsub', name='Write Submitter', username='wsub', member_type='Member')
        self.chair = ParliamentUser.objects.create_user(
            user_id='wchair', name='Wanda Chair', username='wchair', member_type='Officer')

        self.committee = Committee.objects.create(
            name='Kai Committee (tags)', code='KAITAG', is_kai_committee=True)
        self.committee.chairs.add(self.chair)

        self.report = KaiReport.objects.create(
            title='Taggable case', category='behavioral',
            description='body', submitted_by=self.submitter,
        )
        self.client.force_login(self.chair)

    def test_update_tags_accepts_vocabulary_values(self):
        self.client.post(
            reverse('manage_kai_report', kwargs={'report_id': self.report.id}),
            {'action': 'update_tags', 'tags': ['urgent', 'escalated']},
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.tags, ['urgent', 'escalated'])

    def test_update_tags_refuses_a_name_and_changes_nothing(self):
        """The attack this whole vocabulary exists to stop."""
        self.report.tags = ['urgent']
        self.report.save(update_fields=['tags'])

        self.client.post(
            reverse('manage_kai_report', kwargs={'report_id': self.report.id}),
            {'action': 'update_tags', 'tags': ['urgent', 'Zebediah Submitter']},
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.tags, ['urgent'])

    def test_update_tags_can_clear_all_tags(self):
        self.report.tags = ['urgent']
        self.report.save(update_fields=['tags'])

        self.client.post(
            reverse('manage_kai_report', kwargs={'report_id': self.report.id}),
            {'action': 'update_tags'},
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.tags, [])

    def test_template_create_refuses_free_text_suggested_tags(self):
        self.client.post(reverse('create_kai_template'), {
            'name': 'Bad template', 'description': 'd', 'category': 'behavioral',
            'title_template': 't', 'description_template': 'd',
            'suggested_tags': ['not-a-real-tag'],
        })
        self.assertFalse(KaiReportTemplate.objects.filter(name='Bad template').exists())

    def test_template_create_accepts_vocabulary_suggested_tags(self):
        self.client.post(reverse('create_kai_template'), {
            'name': 'Good template', 'description': 'd', 'category': 'behavioral',
            'title_template': 't', 'description_template': 'd',
            'suggested_tags': ['urgent'],
        })
        template = KaiReportTemplate.objects.get(name='Good template')
        self.assertEqual(template.suggested_tags, ['urgent'])


class TagLeakSurfaceTests(TestCase):
    """
    Legacy free-text tags must not be readable by a list-only reviewer once
    normalized. This is the end-to-end version of the whole point.
    """

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='gsub', name='Gil Submitter', username='gsub', member_type='Member')
        self.reviewer = ParliamentUser.objects.create_user(
            user_id='grev', name='Gina Reviewer', username='grev', member_type='Member')

        self.committee = Committee.objects.create(
            name='Kai Committee (leak)', code='KAILEAK', is_kai_committee=True)
        self.committee.members.add(self.reviewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer, can_view_report_list=True)

        # A row as it would exist before v3.16.3 — written directly, bypassing clean().
        self.report = KaiReport.objects.create(
            title='Legacy tagged case', category='behavioral',
            description='body', submitted_by=self.submitter,
            tags=['urgent', 'gil-submitter-incident'],
        )

    def test_normalization_removes_the_identifying_tag(self):
        call_command('normalize_kai_tags', '--apply', stdout=StringIO())
        self.report.refresh_from_db()
        self.assertEqual(self.report.tags, ['urgent'])

    def test_after_normalization_the_name_is_not_searchable(self):
        call_command('normalize_kai_tags', '--apply', stdout=StringIO())
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('view_kai_reports'), {'search': 'gil-submitter'})

        # Assert on the result set, not the page body: the dashboard's
        # recent-activity panel renders report titles regardless of the search
        # filter, and normalization writes an activity row for this very report.
        # (Titles are list-level information, so that panel is not a leak — but
        # it does make a whole-page assertion the wrong instrument here.)
        self.assertEqual(list(resp.context['reports']), [])

    def test_a_control_search_still_finds_the_case_by_title(self):
        """Guard: the assertion above must fail for the right reason."""
        call_command('normalize_kai_tags', '--apply', stdout=StringIO())
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('view_kai_reports'), {'search': 'Legacy tagged'})
        self.assertEqual([r.pk for r in resp.context['reports']], [self.report.pk])

    def test_after_normalization_the_name_is_not_rendered(self):
        call_command('normalize_kai_tags', '--apply', stdout=StringIO())
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('view_kai_reports'))
        self.assertContains(resp, 'Legacy tagged case')
        self.assertNotContains(resp, 'gil-submitter-incident')


class NormalizeCommandTests(TestCase):
    """The cleanup command defaults to a dry run and audits what it removes."""

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='nsub', name='Norm Submitter', username='nsub', member_type='Member')
        self.report = KaiReport.objects.create(
            title='Case', category='behavioral', description='d',
            submitted_by=self.submitter, tags=['urgent', 'bogus-tag'],
        )

    def test_dry_run_is_the_default_and_writes_nothing(self):
        out = StringIO()
        call_command('normalize_kai_tags', stdout=out)
        self.report.refresh_from_db()
        self.assertEqual(self.report.tags, ['urgent', 'bogus-tag'])
        self.assertIn('DRY RUN', out.getvalue())
        self.assertIn('bogus-tag', out.getvalue())

    def test_apply_writes_and_records_an_activity_entry(self):
        call_command('normalize_kai_tags', '--apply', stdout=StringIO())
        self.report.refresh_from_db()
        self.assertEqual(self.report.tags, ['urgent'])

        activity = KaiReportActivity.objects.filter(
            report=self.report, action='tags_updated').latest('timestamp')
        # The removed value survives on the detail-gated timeline, not on the list.
        self.assertIn('bogus-tag', activity.details)

    def test_is_idempotent(self):
        call_command('normalize_kai_tags', '--apply', stdout=StringIO())
        out = StringIO()
        call_command('normalize_kai_tags', '--apply', stdout=out)
        self.assertIn('Nothing to do', out.getvalue())

    def test_no_audit_flag_skips_the_activity_entry(self):
        call_command('normalize_kai_tags', '--apply', '--no-audit', stdout=StringIO())
        self.assertFalse(
            KaiReportActivity.objects.filter(
                report=self.report, action='tags_updated').exists()
        )
