"""
The website refresh (src/ui_refresh.py) on the slating committee's working
pages: applications review and detail, interviews (list, schedule, record),
the slate builder, slate preview, edit approved slate, attendance, paper
ballots, manual results and the next-round form on period setup.

What must hold:
* off: the old markup (no rf-sc classes);
* on: the page's rf-sc classes, with every link, form, field and script hook
  the page had (the builder's assign modal, attendance.js's hooks, the
  replace / vote-mode radios);
* the templates' switches only add classes (strip them and the template has
  no `ui_refresh` or `rf-` left), and none of them adds inline script or
  style;
* the stylesheet carries no !important, @import or remote URL, and every
  rule is scoped to an rf- class.
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse

from src.models import Slate, SlateCandidate, SlatingApplication, SlatingInterview, SlatingPeriod, SlatingPosition
from src.models_feature_flags import FeatureFlag
from src.tests.slating.test_slating_review_fixes import _Base

PAGE_CSS = 'css/refresh-pages/slating-committee.css'
TEMPLATES = ['applications_review.html', 'application_detail.html', 'interviews.html', 'schedule_interview.html',
             'complete_interview.html', 'slate_builder.html', 'slate_preview.html', 'edit_approved_slate.html',
             'attendance.html', 'paper_ballots.html', 'manual_results.html', '_next_round.html']
SWITCH = re.compile(r'\{% if ui_refresh %\}(?: [\w -]+| class="[\w -]+")\{% endif %\}')


def set_flag(on):
    FeatureFlag.objects.update_or_create(
        name='web_refresh',
        defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


class SlatingCommitteeRefreshTests(_Base):
    """_Base: a period in voting_open with an approved slate, the manager
    logged in below."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager)
        self.interview = SlatingInterview.objects.create(application=self.app, location='Chapter Room')

    def draft_period(self):
        """A period before any voting, its slate still a draft with one
        position filled."""
        draft = SlatingPeriod.objects.create(name='Spring', status='deliberation', slating_manager=self.manager,
                                             slating_committee=self.committee)
        pos = SlatingPosition.objects.create(period=draft, title='Treasurer', code='T')
        app = SlatingApplication.objects.create(period=draft, applicant=self.committee_member, status='submitted')
        slate = Slate.objects.create(period=draft, slate_type='draft', name='Draft Slate', is_approved=False)
        SlateCandidate.objects.create(slate=slate, position=pos, application=app)
        return draft, pos

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, classes, hooks):
        html = self.get(url)
        self.assertNotIn('rf-sc', html, url)
        for hook in hooks:
            self.assertIn(hook, html, f'{url} (off): {hook}')
        set_flag(True)
        try:
            html = self.get(url)
            self.assertIn(PAGE_CSS, html)
            self.assertIn('rf-sc rf-sc-head', html, url)
            for cls in classes:
                self.assertIn(cls, html, url)
            for hook in hooks:
                self.assertIn(hook, html, f'{url} (on): {hook}')
        finally:
            set_flag(False)
        return html

    def test_applications_pages(self):
        p = self.period.id
        self.off_and_on(reverse('slating_applications', args=[p]),
                        [' rf-sc rf-sc-apps"', ' rf-sc-filters"', ' rf-sc-stats"', ' rf-sc-bulk"', ' rf-sc-rows"'],
                        ['name="search"', 'name="status"', 'name="position"', 'name="gpa_level"', 'id="bulkForm"',
                         'data-check-all="application_ids"', 'name="application_ids"', 'csrfmiddlewaretoken',
                         reverse('slating_bulk_update', args=[p]), reverse('slating_import_applications', args=[p]),
                         reverse('slating_app_detail', args=[p, self.app.id]), '?status='])
        self.off_and_on(reverse('slating_app_detail', args=[p, self.app.id]), [' rf-sc rf-sc-app"'],
                        [reverse('slating_submit_review', args=[p, self.app.id]), 'name="review_notes"',
                         'name="verify_gpa"', reverse('slating_applications', args=[p])])

    def test_interview_pages(self):
        p = self.period.id
        self.off_and_on(reverse('slating_interviews', args=[p]),
                        [' rf-sc rf-sc-interviews"', ' rf-sc-tabbar"', ' rf-sc-tabs"', ' rf-sc-rows"',
                         ' rf-sc-danger"'],
                        ['?status=scheduled', '?status=completed', '?status=pending',
                         reverse('slating_complete_interview', args=[self.interview.id]),
                         reverse('slating_destroy_notes', args=[p]), 'name="confirm"', 'pattern="DESTROY"'])
        self.off_and_on(reverse('slating_schedule_interview', args=[self.app.id]),
                        [' rf-sc rf-sc-form"', ' rf-sc-picks"'],
                        ['name="scheduled_at"', 'name="location"', 'csrfmiddlewaretoken',
                         reverse('slating_interviews', args=[p])])
        self.off_and_on(reverse('slating_complete_interview', args=[self.interview.id]),
                        [' rf-sc rf-sc-form"', ' rf-sc-picks"'],
                        ['name="notes"', 'name="strengths"', 'name="concerns"', 'name="recommendation"',
                         'name="recommended_positions"',
                         reverse('slating_app_detail', args=[p, self.app.id])])

    def test_slate_pages(self):
        p = self.period.id
        # The builder on a period whose slate is still a draft: the assign
        # controls and their modal.
        draft, _ = self.draft_period()
        self.off_and_on(reverse('slating_build_slate', args=[draft.id]),
                        [' rf-sc rf-sc-builder"', ' rf-sc rf-sc-modal"'],
                        ['id="assignModal"', 'js-open-assign-modal', 'js-close-assign-modal', 'data-mode="assign"',
                         'data-mode="assign_runoff"', 'id="modalAction"', 'id="modalPositionId"',
                         'name="application_id"', 'value="unassign"', 'name="assignment_id"',
                         reverse('slating_approve_slate', args=[draft.id]), 'js-confirm-submit',
                         reverse('slating_period_setup', args=[draft.id])])
        self.off_and_on(reverse('slating_slate_preview', args=[p, self.slate.id]), [' rf-sc rf-sc-preview"'],
                        [reverse('slating_copy_slate', args=[p, self.slate.id]), reverse('slating_build_slate', args=[p])])
        self.off_and_on(reverse('slating_edit_approved_slate', args=[p]), [' rf-sc rf-sc-editslate"'],
                        ['js-replace-form', 'js-candidate-type-radio', f'id="applicant_{self.pres.id}"',
                         f'id="writein_{self.pres.id}"', 'name="member_id"', 'name="candidate_type"',
                         f'id="type_hidden_{self.pres.id}"', reverse('slating_period_setup', args=[p])])

    def test_day_of_pages(self):
        p = self.period.id
        self.off_and_on(reverse('slating_attendance', args=[p]),
                        [' rf-sc rf-sc-att"', ' rf-sc-sticky"', ' rf-sc-roster"'],
                        ['id="attendance-page"', 'data-total=', 'id="js-present-count"', 'id="js-attendance-search"',
                         'js-search-submit', 'id="js-mark-all-form"', 'id="js-mark-all-ids"', 'id="js-mark-all-btn"',
                         'id="js-attendance-list"', 'js-attendance-row', 'js-dot', 'js-attendance-toggle',
                         'data-present-class=', 'data-absent-class=', 'id="js-no-match"', 'js/slating/attendance.js'])
        self.off_and_on(reverse('slating_paper_ballots', args=[p]), [' rf-sc rf-sc-paper"'],
                        ['name="note"', 'csrfmiddlewaretoken', reverse('slating_period_setup', args=[p])])
        # Manual results: only for an election that never voted online.
        draft, pos = self.draft_period()
        self.off_and_on(reverse('slating_manual_results', args=[draft.id]), [' rf-sc rf-sc-manual"'],
                        ['id="manual-results-form"', 'js-vote-mode-radio', 'value="per_position"',
                         'id="overall-vote-inputs"', 'per-position-votes', f'name="winner_{pos.id}"',
                         f'name="approve_{pos.id}"', 'name="overall_approve"', 'js-confirm-submit'])

    def test_next_round_form_on_period_setup(self):
        self.period.status = 'deliberation'
        self.period.current_voting_attempt = 0
        self.period.save()
        url = reverse('slating_period_setup', args=[self.period.id])
        html = self.get(url)
        self.assertNotIn('rf-sc', html)
        self.assertIn('name="vote_type"', html)
        set_flag(True)
        try:
            html = self.get(url)
            self.assertIn(' js-confirm-submit rf-sc rf-sc-next"', html)
            for hook in ['name="vote_type"', 'value="voting_open"', 'name="quorum"', 'id="quorumNext"',
                         'data-attendance', reverse('slating_change_status', args=[self.period.id])]:
                self.assertIn(hook, html)
        finally:
            set_flag(False)


class SlatingCommitteeTemplateTests(SimpleTestCase):
    """Strip the switches out and nothing of the refresh is left."""

    BASE = Path(settings.BASE_DIR)

    def test_every_switch_only_adds_classes(self):
        for name in TEMPLATES:
            text = (self.BASE / 'templates' / 'slating' / name).read_text(encoding='utf-8')
            self.assertGreaterEqual(text.count('{% if ui_refresh %}'), 1, name)
            self.assertEqual(text.count('{% if ui_refresh %}'), len(SWITCH.findall(text)), name)
            stripped = SWITCH.sub('', text)
            self.assertNotIn('ui_refresh', stripped, name)
            self.assertNotRegex(stripped, r'\brf-', name)
            # No switch carries markup of its own: no inline style or script.
            for switch in SWITCH.findall(text):
                self.assertNotIn('style', switch)
                self.assertNotIn('<', switch)

    def test_no_inline_style_attributes(self):
        for name in TEMPLATES:
            text = (self.BASE / 'templates' / 'slating' / name).read_text(encoding='utf-8')
            self.assertNotRegex(text, r'\sstyle=', name)

    @staticmethod
    def top_level_split(rule):
        parts, depth, cur = [], 0, ''
        for ch in rule:
            depth += (ch == '(') - (ch == ')')
            if ch == ',' and depth == 0:
                parts.append(cur)
                cur = ''
            else:
                cur += ch
        return parts + [cur]

    def test_stylesheet_is_plain_and_scoped(self):
        css = (self.BASE / 'static' / PAGE_CSS).read_text(encoding='utf-8')
        css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        self.assertNotRegex(css, r'!\s*important')
        self.assertNotIn('@import', css)
        self.assertNotIn('http', css)
        self.assertNotIn('url(', css)
        rules = re.findall(r'(?m)^([^{}@\n][^{}]*)\{', css)
        self.assertGreater(len(rules), 50)
        for rule in rules:
            for selector in self.top_level_split(rule):
                selector = selector.strip()
                if not selector or selector in (':root.ui-refresh', ':root.ui-refresh.dark'):
                    continue
                self.assertRegex(selector, r'^(\.ui-refresh|:root\.ui-refresh\.dark) .*\.rf-sc', selector)

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (self.BASE / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
