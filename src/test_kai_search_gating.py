"""
Regression tests for the v3.16.3 fix batch.

Three failures are covered here, all found by the 07-26-26 auto-run:

1. `templates/global_search.html` linked Kai results with
   {% url 'kai_report_detail' %} — a route that has never existed. {% url %}
   raises NoReverseMatch, which Django does not silence, so rendering the Kai
   block 500'd the entire search page.

2. The same result card rendered `item.description` — the allegation body —
   with no `can_view_report_details` check, while the view itself already
   refused to *search* that field for list-only reviewers. Search was strictly
   more permissive than the detail view it linked to.

3. `view_kai_reports` and `export_kai_reports_csv` filtered unconditionally on
   `submitted_by__name`, `targeted_to__name` and `description` while redacting
   exactly those columns in their output. A filter predicate is a join key:
   the redacted values were recoverable by searching for them.

4. (07-28-26 auto-run) The Kai report LIST page — `templates/kai/view_reports.html`
   — rendered the allegation body with no `can_view_report_details` check, while
   gating submitter and accused identity on the two lines directly below it. That
   made every fix above cosmetic: a list-only reviewer read 30 words of every
   allegation just by opening the page, no search required. Covered by
   `KaiListPageDescriptionTests`.

5. (07-28-26 auto-run) The search placeholder hardcoded the full field list, so
   after fix 3 a list-only reviewer would search a member's name, get nothing,
   and infer that member has no cases. Covered by `KaiSearchPlaceholderTests`.

The last test class is deliberately generic. Findings 1, 2 and 4 are all
instances of "a template branch that no test ever renders", which is the
recurring shape of bugs in this codebase (see the 07-26 weekly report). The
URL-name scan catches the whole class cheaply.
"""

import re
from pathlib import Path

from django.test import TestCase, SimpleTestCase
from django.urls import reverse, get_resolver

from .models import ParliamentUser, Committee, KaiReport, KaiMemberPermission
from .view.kai_reports import _kai_search_q, _kai_search_placeholder


ALL_PERMS = [
    'can_view_report_list', 'can_view_report_details',
    'can_view_submitter_identity', 'can_view_accused_identity',
    'can_edit_open_cases', 'can_add_activity', 'can_close_cases',
]


def access(**overrides):
    """Build a kai_access dict with everything False except the named flags."""
    d = {f: False for f in ALL_PERMS}
    d['is_full_access'] = False
    d.update(overrides)
    return d


def q_lookups(q):
    """Flatten a Q object to the set of lookup names it references."""
    found = set()
    for child in q.children:
        if hasattr(child, 'children'):
            found |= q_lookups(child)
        else:
            found.add(child[0])
    return found


class KaiSearchPredicateTests(SimpleTestCase):
    """_kai_search_q must gate each searchable field on the flag that governs reading it."""

    def test_list_only_reviewer_searches_title_and_tags_only(self):
        lookups = q_lookups(_kai_search_q('needle', access(can_view_report_list=True)))
        self.assertEqual(lookups, {'title__icontains', 'tags__icontains'})

    def test_description_searchable_only_with_detail_permission(self):
        without = q_lookups(_kai_search_q('needle', access(can_view_report_list=True)))
        self.assertNotIn('description__icontains', without)

        with_ = q_lookups(_kai_search_q(
            'needle', access(can_view_report_list=True, can_view_report_details=True)))
        self.assertIn('description__icontains', with_)

    def test_submitter_name_searchable_only_with_submitter_permission(self):
        without = q_lookups(_kai_search_q('needle', access(can_view_report_list=True)))
        self.assertNotIn('submitted_by__name__icontains', without)

        with_ = q_lookups(_kai_search_q(
            'needle', access(can_view_report_list=True, can_view_submitter_identity=True)))
        self.assertIn('submitted_by__name__icontains', with_)

    def test_accused_name_searchable_only_with_accused_permission(self):
        without = q_lookups(_kai_search_q('needle', access(can_view_report_list=True)))
        self.assertNotIn('targeted_to__name__icontains', without)

        with_ = q_lookups(_kai_search_q(
            'needle', access(can_view_report_list=True, can_view_accused_identity=True)))
        self.assertIn('targeted_to__name__icontains', with_)

    def test_full_access_searches_everything(self):
        lookups = q_lookups(_kai_search_q('needle', access(**{f: True for f in ALL_PERMS})))
        self.assertEqual(lookups, {
            'title__icontains', 'tags__icontains', 'description__icontains',
            'submitted_by__name__icontains', 'targeted_to__name__icontains',
        })


class KaiSearchOracleTests(TestCase):
    """End-to-end: the redacted fields must not be reachable through the filter."""

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='sub1', name='Zebediah Submitter', username='sub1', member_type='Member')
        self.accused = ParliamentUser.objects.create_user(
            user_id='acc1', name='Quintus Accused', username='acc1', member_type='Member')
        self.reviewer = ParliamentUser.objects.create_user(
            user_id='rev1', name='Rita Reviewer', username='rev1', member_type='Member')

        self.committee = Committee.objects.create(
            name='Kai Committee (test)', code='KAITEST', is_kai_committee=True)
        self.committee.members.add(self.reviewer)

        # List-only: can see that cases exist, nothing else.
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer, can_view_report_list=True)

        self.report = KaiReport.objects.create(
            title='Chapter house incident',
            category='behavioral',
            description='Alleged conduct involving a distinctive marker word: pomegranate.',
            submitted_by=self.submitter,
            targeted_to=self.accused,
        )
        self.client.force_login(self.reviewer)

    def test_list_only_reviewer_can_still_search_titles(self):
        """The gate must not break legitimate use."""
        resp = self.client.get(reverse('view_kai_reports'), {'search': 'incident'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Chapter house incident')

    def test_searching_submitter_name_does_not_reveal_their_cases(self):
        resp = self.client.get(reverse('view_kai_reports'), {'search': 'Zebediah'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Chapter house incident')

    def test_searching_accused_name_does_not_reveal_their_cases(self):
        resp = self.client.get(reverse('view_kai_reports'), {'search': 'Quintus'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Chapter house incident')

    def test_searching_allegation_body_does_not_match(self):
        resp = self.client.get(reverse('view_kai_reports'), {'search': 'pomegranate'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Chapter house incident')

    def test_csv_export_filter_is_gated_too(self):
        """The export duplicated the filter; it must use the same gate."""
        resp = self.client.get(reverse('export_kai_reports_csv'), {'search': 'Zebediah'})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn('Chapter house incident', body)

    def test_csv_export_redacts_and_still_returns_title_matches(self):
        resp = self.client.get(reverse('export_kai_reports_csv'), {'search': 'incident'})
        body = resp.content.decode()
        self.assertIn('Chapter house incident', body)
        self.assertIn('[Redacted]', body)
        self.assertNotIn('Zebediah', body)
        self.assertNotIn('Quintus', body)
        self.assertNotIn('pomegranate', body)


class GlobalSearchKaiCardTests(TestCase):
    """The search page must render, and must not leak the allegation body."""

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='sub2', name='Sam Submitter', username='sub2', member_type='Member')
        self.reviewer = ParliamentUser.objects.create_user(
            user_id='rev2', name='Rae Reviewer', username='rev2', member_type='Member')
        self.chair = ParliamentUser.objects.create_user(
            user_id='chair2', name='Cleo Chair', username='chair2', member_type='Officer')

        self.committee = Committee.objects.create(
            name='Kai Committee (search test)', code='KAISEARCH', is_kai_committee=True)
        self.committee.members.add(self.reviewer)
        self.committee.chairs.add(self.chair)

        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer, can_view_report_list=True)

        self.report = KaiReport.objects.create(
            title='Unmistakable searchable title',
            category='behavioral',
            description='Body text containing the marker word rutabaga.',
            submitted_by=self.submitter,
        )

    def test_search_page_renders_kai_results_without_error(self):
        """Regression: {% url 'kai_report_detail' %} raised NoReverseMatch here."""
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('global_search'), {'q': 'Unmistakable'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Unmistakable searchable title')

    def test_kai_card_links_to_the_real_detail_route(self):
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('global_search'), {'q': 'Unmistakable'})
        self.assertContains(
            resp, reverse('manage_kai_report', kwargs={'report_id': self.report.id}))

    def test_list_only_reviewer_does_not_see_the_description(self):
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('global_search'), {'q': 'Unmistakable'})
        self.assertNotContains(resp, 'rutabaga')

    def test_chair_does_see_the_description(self):
        """The gate must not hide the preview from someone entitled to it."""
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('global_search'), {'q': 'Unmistakable'})
        self.assertContains(resp, 'rutabaga')

    def test_description_is_not_searchable_for_list_only_reviewer(self):
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('global_search'), {'q': 'rutabaga'})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Unmistakable searchable title')


class TemplateUrlNameTests(SimpleTestCase):
    """
    Every literal {% url 'name' %} in every template must name a real route.

    This is the generic guard for the failure that produced finding 1: a
    template branch that only renders under a specific permission, referencing
    a URL name that does not exist. Nothing catches that until the branch runs
    in production. Names containing ':' (namespaced, e.g. admin:) are skipped
    — they resolve through a separate namespace registry.

    On first run this found four more of the same bug beyond the Kai one:
    'service_user_dashboard' (transposed), 'manage_signups',
    'manage_pledge_tasks' and 'pledge_progress_overview' — all in
    unconditional blocks on live guide pages, i.e. three more hard 500s.

    KNOWN LIMITATION: this checks that the *name* is registered, not that the
    tag passes the right arguments. `{% url 'education_home' %}` with no args
    would still raise NoReverseMatch at render time and pass this test.
    Catching that needs an actual render, which is what the view tests above
    do for the pages they cover.

    SKIPPED_DIRS holds template trees that no view renders, so a stale URL
    name in them cannot 500 anything.
    """

    URL_TAG_RE = re.compile(r"""\{%\s*url\s+(['"])([^'"]+)\1""")
    SKIPPED_DIRS = {'archive'}

    def test_all_literal_url_names_resolve(self):
        template_dir = Path(__file__).resolve().parent.parent / 'templates'
        reverse_dict = get_resolver().reverse_dict

        broken = []
        scanned = 0
        for path in template_dir.rglob('*.html'):
            if self.SKIPPED_DIRS & set(path.relative_to(template_dir).parts[:-1]):
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for _, name in self.URL_TAG_RE.findall(text):
                scanned += 1
                if ':' in name:
                    continue
                if name not in reverse_dict:
                    broken.append(f'{path.relative_to(template_dir)} -> {name!r}')

        self.assertGreater(scanned, 0, 'No {% url %} tags scanned — did templates/ move?')
        self.assertEqual(
            broken, [],
            'Templates reference URL names that do not exist. Each of these raises '
            'NoReverseMatch (a 500) the moment its branch renders:\n  '
            + '\n  '.join(sorted(broken)),
        )


class KaiListPageDescriptionTests(TestCase):
    """
    The Kai report LIST page must gate the allegation body on can_view_report_details.

    07-28-26: this was the hole that made the rest of v3.16.2/v3.16.3 cosmetic.
    The CSV redacted `description`, the search predicate stopped matching it, and
    the global-search card hid it — while `/kai/reports/` printed the first 30
    words of every report to anyone holding `can_view_report_list`. No search
    term, no exploit: load the page and read down it.
    """

    MARKER = 'pomegranate'

    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='lsub', name='Leo Submitter', username='lsub', member_type='Member')
        self.reviewer = ParliamentUser.objects.create_user(
            user_id='lrev', name='Lena Reviewer', username='lrev', member_type='Member')
        self.chair = ParliamentUser.objects.create_user(
            user_id='lchair', name='Lucia Chair', username='lchair', member_type='Officer')

        self.committee = Committee.objects.create(
            name='Kai Committee (list test)', code='KAILIST', is_kai_committee=True)
        self.committee.members.add(self.reviewer)
        self.committee.chairs.add(self.chair)

        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer, can_view_report_list=True)

        self.report = KaiReport.objects.create(
            title='Distinctive list-page title',
            category='behavioral',
            description='Alleged conduct involving a marker word: %s.' % self.MARKER,
            submitted_by=self.submitter,
        )

    def test_list_only_reviewer_sees_the_case_but_not_the_allegation(self):
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('view_kai_reports'))
        self.assertEqual(resp.status_code, 200)
        # They are entitled to know the case exists...
        self.assertContains(resp, 'Distinctive list-page title')
        # ...and not to read what it alleges.
        self.assertNotContains(resp, self.MARKER)

    def test_chair_still_sees_the_preview(self):
        """The gate must not hide the preview from someone entitled to it."""
        self.client.force_login(self.chair)
        resp = self.client.get(reverse('view_kai_reports'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.MARKER)

    def test_reviewer_with_detail_permission_sees_the_preview(self):
        perm = KaiMemberPermission.objects.get(committee=self.committee, user=self.reviewer)
        perm.can_view_report_details = True
        perm.save(update_fields=['can_view_report_details'])

        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('view_kai_reports'))
        self.assertContains(resp, self.MARKER)

    def test_identity_gating_on_the_same_card_still_works(self):
        """Guard against the fix being applied by removing the surrounding block."""
        self.client.force_login(self.reviewer)
        resp = self.client.get(reverse('view_kai_reports'))
        self.assertNotContains(resp, 'Leo Submitter')
        self.assertContains(resp, 'Anonymous')


class KaiSearchPlaceholderTests(SimpleTestCase):
    """
    The placeholder must describe exactly what _kai_search_q will search.

    Otherwise a list-only reviewer searches a member's name, gets zero rows, and
    concludes that member has no cases — a false inference the UI invited.
    """

    def test_list_only_reviewer_is_offered_title_and_tags_only(self):
        text = _kai_search_placeholder(access(can_view_report_list=True))
        self.assertIn('title', text)
        self.assertIn('tags', text)
        for absent in ('description', 'submitter', 'targeted person'):
            self.assertNotIn(absent, text)

    def test_full_access_is_offered_everything(self):
        text = _kai_search_placeholder(access(**{f: True for f in ALL_PERMS}))
        for present in ('title', 'description', 'submitter', 'targeted person', 'tags'):
            self.assertIn(present, text)

    def test_placeholder_matches_the_predicate_across_the_matrix(self):
        """The two must not drift — they are one decision rendered twice."""
        field_for_lookup = {
            'title__icontains': 'title',
            'tags__icontains': 'tags',
            'description__icontains': 'description',
            'submitted_by__name__icontains': 'submitter',
            'targeted_to__name__icontains': 'targeted person',
        }
        for details in (False, True):
            for submitter in (False, True):
                for accused in (False, True):
                    acc = access(
                        can_view_report_list=True,
                        can_view_report_details=details,
                        can_view_submitter_identity=submitter,
                        can_view_accused_identity=accused,
                    )
                    searched = {
                        field_for_lookup[l] for l in q_lookups(_kai_search_q('x', acc))
                    }
                    text = _kai_search_placeholder(acc)
                    offered = {
                        name for name in field_for_lookup.values() if name in text
                    }
                    self.assertEqual(
                        searched, offered,
                        'placeholder and predicate disagree for %r' % (acc,),
                    )
