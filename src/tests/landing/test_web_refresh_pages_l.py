"""
The website refresh (src/ui_refresh.py) on the member-facing slating pages:
the shared slating header (base_slating.html), the dashboard, applying, my
applications and an application, the whole-slate and position ballots, the
results and the committee's results summary.

What must hold:
* off: the old markup (no page stylesheet, no `rf-sl` classes);
* on: the page's switch classes and the stylesheet, with every link, form,
  field and script hook the page had (the ballots above all: vote_choice,
  rejected_positions, the password, the passkey and ballot.js hooks);
* the templates' switches only add classes (strip them and no `ui_refresh`
  or `rf-` is left, so the flag-off output is the old output byte for byte),
  except one small block on the Reject card that states the
  at-least-one-position rule; no switch adds a script or a style attribute;
* the stylesheet carries no !important, @import or remote URL, and every rule
  is scoped to an `rf-` class.

    python manage.py test src.tests.landing.test_web_refresh_pages_l
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from src.models import SlatingFormField, SlatingPeriod, SlatingPosition
from src.models_feature_flags import FeatureFlag
from src.tests.slating.test_slating_review_fixes import _Base, member

PAGE_CSS = 'css/refresh-pages/slating-member.css'
SHELL = 'rf-sl-shell'


def set_flag(on):
    FeatureFlag.objects.update_or_create(
        name='web_refresh',
        defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


class RefreshPagesLTests(_Base):
    """_Base: period 'Fall' with voting open (round 1), an approved slate
    (President: self.voter's application; VP: the manager, a write-in), and
    self.voter marked present."""

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, classes, hooks, on_only=()):
        html = self.get(url)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn('rf-sl', html)
        for hook in hooks:
            self.assertIn(hook, html, url)
        set_flag(True)
        try:
            html = self.get(url)
            self.assertIn(PAGE_CSS, html)
            self.assertIn(f' {SHELL}"', html, url)
            self.assertIn(f' {classes}"', html, url)
            for hook in list(hooks) + list(on_only):
                self.assertIn(hook, html, url)
        finally:
            set_flag(False)
        return html

    # Dashboard, applying, applications ----------------------------------------------

    def test_dashboard(self):
        self.client.force_login(self.voter)
        self.off_and_on(reverse('slating_dashboard'), 'rf-sl rf-sl-dash',
                        [reverse('slating_my_applications'), reverse('slating_vote', args=[self.period.id]),
                         'Officer Slating', 'Active Elections'],
                        on_only=[' rf-sl-hero"', ' rf-sl-card"', ' rf-sl-cta"'])

    def test_apply(self):
        period = SlatingPeriod.objects.create(name='Spring', status='nominations_open', slating_manager=self.manager,
                                              slating_committee=self.committee, required_approval_percentage=66)
        SlatingPosition.objects.create(period=period, title='Secretary', code='S', display_order=1)
        SlatingFormField.objects.create(period=period, field_name='why', label='Why run?', field_type='textarea',
                                        is_required=True, display_order=1)
        SlatingFormField.objects.create(period=period, field_name='prefs', label='Positions',
                                        field_type='position_preference', display_order=2)
        applicant = member('rpl-applicant')
        self.client.force_login(applicant)
        field = period.form_fields.get(field_name='why')
        self.off_and_on(reverse('slating_apply', args=[period.id]), 'rf-sl rf-sl-apply',
                        ['enctype="multipart/form-data"', 'csrfmiddlewaretoken', 'id="formAction"',
                         f'name="field_{field.id}"', 'value="save_draft"', 'formnovalidate',
                         'id="js-submit-application"', 'id="positionPreferencesContainer"', 'class="position-row',
                         'data-tier="first_choice"', 'data-tier="do_not_want"', 'class="position-input"'],
                        on_only=[' rf-sl-actions"', ' rf-sl-legend"'])

    def test_my_applications(self):
        self.client.force_login(self.voter)
        self.off_and_on(reverse('slating_my_applications'), 'rf-sl rf-sl-mine',
                        ['Fall', 'Position Preferences', 'js-confirm-submit'], on_only=[' rf-sl-pill'])

    def test_view_application_template(self):
        """Not routed today (src/view/slating/apply.py view_application), so
        rendered straight from the template with the view's context."""
        self.client.force_login(self.voter)
        request = self.client.get(reverse('slating_my_applications')).wsgi_request
        template = get_template('slating/view_application.html')
        context = {'application': self.app, 'period': self.period, 'fields': self.period.form_fields.all(),
                   'responses': {}, 'tiered_positions': None, 'selected_positions': []}
        off = template.render({**context, 'ui_refresh': False}, request)
        self.assertNotIn('rf-sl', off)
        on = template.render({**context, 'ui_refresh': True}, request)
        self.assertIn(' rf-sl rf-sl-app"', on)
        self.assertIn(' rf-sl-head"', on)
        self.assertIn(' rf-sl-timeline"', on)
        for html in (off, on):
            self.assertIn(reverse('slating_my_applications'), html)
            self.assertIn('Application Status', html)

    # Ballots ---------------------------------------------------------------------------

    BALLOT_HOOKS = ['js-vote-form', 'js-ballot-form', 'js-ballot-hint', 'name="password"', 'id="password"',
                    'autocomplete="current-password"', 'csrfmiddlewaretoken', 'js/slating/ballot.js',
                    'data-confirm="Cast your ballot?']

    def test_slate_ballot(self):
        self.client.force_login(self.voter)
        html = self.off_and_on(
            reverse('slating_vote', args=[self.period.id]), 'rf-sl rf-sl-vote',
            self.BALLOT_HOOKS + [
                'name="vote_choice"', 'id="vote_approve"', 'id="vote_reject"', 'value="approve"', 'value="reject"',
                'js-radio-card', 'js-vote-radio', 'data-radio-id="vote_reject"', 'id="rejection_reasons"',
                'name="rejected_positions"', f'value="{self.pres.id}"', 'rejected-position-checkbox',
                'Select at least one position to continue.', 'id="slate-update-banner"', 'data-slate-fingerprint',
                'id="js-reload-page"', reverse('slating_view_approved_slate', args=[self.period.id]),
                'Secret Ballot', 'Cast Vote'],
            on_only=[' rf-sl-choice rf-sl-approve"', ' rf-sl-choice rf-sl-reject-choice"', ' rf-sl-reject"',
                     ' rf-sl-req"', ' rf-sl-confirm"', ' rf-sl-submit"', ' rf-sl-head"'])
        # The No-needs-a-position rule is on the Reject card before Reject is picked.
        self.assertIn('class="rf-sl-choice-req"', html)
        self.assertIn('at least one position you object to', html)
        # The reasons box still starts hidden for the page's script to open.
        self.assertRegex(html, r'id="rejection_reasons" class="hidden ')

    def test_slate_ballot_without_the_flag_has_no_rule_line(self):
        self.client.force_login(self.voter)
        html = self.get(reverse('slating_vote', args=[self.period.id]))
        self.assertNotIn('at least one position you object to', html)

    def test_position_ballot(self):
        self.client.force_login(self.manager)
        close = self.client.post(reverse('slating_close_voting', args=[self.period.id]))  # no votes: slate fails
        self.assertEqual(close.status_code, 302)
        self.client.post(reverse('slating_change_status', args=[self.period.id]),
                         {'status': 'voting_open', 'vote_type': 'individual', 'ballot_positions': [self.pres.id]})
        self.period.refresh_from_db()
        self.assertEqual((self.period.status, self.period.vote_type), ('voting_open', 'individual'))
        self.client.force_login(self.voter)
        self.off_and_on(
            reverse('slating_vote_individual', args=[self.period.id]), 'rf-sl rf-sl-ivote',
            self.BALLOT_HOOKS + ['id="individual-vote-form"', f'name="vote_{self.pres.id}"', 'value="approve"',
                                 'value="reject"', 'role="radiogroup"', 'Submit All Votes'],
            on_only=[' rf-sl-pos"', ' rf-sl-opts"', ' rf-sl-opt rf-sl-approve"', ' rf-sl-opt rf-sl-reject-choice"',
                     ' rf-sl-confirm"', ' rf-sl-submit"', 'class="rf-sl-head"'])

    # Results -----------------------------------------------------------------------------

    def test_results_and_summary(self):
        self.client.force_login(self.manager)
        self.off_and_on(reverse('slating_results', args=[self.period.id]), 'rf-sl rf-sl-results',
                        [reverse('slating_results_summary', args=[self.period.id]),
                         reverse('slating_close_voting', args=[self.period.id]), 'js-confirm-submit',
                         'Vote Summary', 'Approval Rate', 'Required: 66% approval'],
                        on_only=[' rf-sl-stats"', ' rf-sl-meter"', ' rf-sl-verdict"', ' rf-sl-head"'])
        self.off_and_on(reverse('slating_results_summary', args=[self.period.id]), 'rf-sl rf-sl-rsum',
                        [reverse('slating_results', args=[self.period.id]),
                         reverse('slating_period_setup', args=[self.period.id]), 'Eligible Voters', '<table'],
                        on_only=[' rf-sl-tiles"', ' rf-sl-table"', ' rf-sl-stats"'])


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left."""

    BASE = Path(settings.BASE_DIR)
    NAMES = ['slating/base_slating.html', 'slating/dashboard.html', 'slating/apply.html',
             'slating/my_applications.html', 'slating/view_application.html', 'slating/vote.html',
             'slating/vote_individual.html', 'slating/results.html', 'slating/results_summary.html']
    # A switch adds classes to a class attribute, a class attribute to a bare
    # tag, or (once, vote.html) the Reject card's at-least-one-position line.
    SWITCH = re.compile(r'\{% if ui_refresh %\}( [\w -]+| class="[\w -]+"|<span class="rf-sl-choice-req">'
                        r'[^<>{}]+</span>)\{% endif %\}')

    def test_every_switch_only_adds_classes(self):
        for name in self.NAMES:
            text = (self.BASE / 'templates' / name).read_text(encoding='utf-8')
            self.assertGreaterEqual(text.count('{% if ui_refresh %}'), 1, name)
            stripped = self.SWITCH.sub('', text)
            self.assertNotIn('ui_refresh', stripped, name)
            self.assertNotRegex(stripped, r'\brf-', name)
            for switch in self.SWITCH.findall(text):
                self.assertNotIn('style', switch)
                self.assertNotIn('script', switch)
                self.assertRegex(switch, r'rf-sl')

    def test_the_rule_line_is_the_only_added_text(self):
        added = [m for name in self.NAMES
                 for m in self.SWITCH.findall((self.BASE / 'templates' / name).read_text(encoding='utf-8'))
                 if m.startswith('<')]
        self.assertEqual(len(added), 1)

    def test_page_roots_carry_the_page_class(self):
        expected = {'dashboard.html': 'rf-sl rf-sl-dash', 'apply.html': 'rf-sl rf-sl-apply',
                    'my_applications.html': 'rf-sl rf-sl-mine', 'view_application.html': 'rf-sl rf-sl-app',
                    'vote.html': 'rf-sl rf-sl-vote', 'vote_individual.html': 'rf-sl rf-sl-ivote',
                    'results.html': 'rf-sl rf-sl-results', 'results_summary.html': 'rf-sl rf-sl-rsum'}
        for name, classes in expected.items():
            text = (self.BASE / 'templates/slating' / name).read_text(encoding='utf-8')
            block = text.split('{% block slating_content %}', 1)[1].lstrip()
            first_tag = block.split('>', 1)[0]
            self.assertIn('{% if ui_refresh %} ' + classes + '{% endif %}', first_tag, name)

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
        selectors = 0
        for rule in re.findall(r'(?m)^\s*([^{}@\s][^{}]*)\{', css):
            for selector in self.top_level_split(rule):
                selector = selector.strip()
                if not selector:
                    continue
                selectors += 1
                self.assertRegex(selector, r'\.rf-sl', selector)
        self.assertGreater(selectors, 100)

    def test_stylesheet_never_unhides_script_toggled_boxes(self):
        """#rejection_reasons and friends are shown by toggling `hidden`; a
        `display` on their hooks would override it."""
        css = re.sub(r'/\*.*?\*/', '', (self.BASE / 'static' / PAGE_CSS).read_text(encoding='utf-8'), flags=re.S)
        for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
            if re.search(r'\.rf-sl-reject(?![\w-])\s*(,|$)', selector.strip()) or 'js-passkey-error' in selector:
                self.assertNotIn('display', body, selector)

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (self.BASE / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
