"""
The website refresh (src/ui_refresh.py) on the slating manager's setup pages:
Period Setup (and its positions panel), Positions, the application Form
Builder, Import Applications (and its two partials), Fill in answers,
Transfer Admin, and the officer transition (confirm and review).

What must hold:
* off: the old markup (no `rf-ss` classes anywhere);
* on: the page's `rf-ss` wrapper and hooks, with every link, form, field and
  script hook the page had;
* Period Setup: the settings form has the same controls under the same
  names and ids, and saving it with the refresh on changes exactly what
  saving with it off does;
* the templates' switches only wrap unchanged lines (strip them and nothing
  of the refresh is left), and carry no inline script or style; the
  stylesheet is plain, scoped and linked only with the switch.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ParliamentUser, Role, SlatingApplication, SlatingFormField, SlatingPeriod, SlatingPosition,
)
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/slating-setup.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active',
                    email=f'{uid}@samford.edu')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-n-12345!')
    user.save()
    return user


def set_flag(on):
    FeatureFlag.objects.update_or_create(
        name='web_refresh',
        defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


class FormFields(HTMLParser):
    """Every control inside the first <form> holding a control named `marker`:
    (tag, name, type, id) plus what a browser would post for it as rendered."""

    def __init__(self, marker):
        super().__init__()
        self.marker = marker
        self.forms, self.current, self.depth, self.select = [], None, 0, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            if not self.depth:
                self.current = {'controls': [], 'posted': {}, 'names': set()}
                self.forms.append(self.current)
            self.depth += 1
            return
        if not self.depth:
            return
        form = self.current
        if tag in ('input', 'select', 'textarea'):
            form['controls'].append((tag, a.get('name'), a.get('type'), a.get('id')))
            form['names'].add(a.get('name'))
            name = a.get('name')
            if tag == 'input' and name:
                if a.get('type') == 'checkbox':
                    if 'checked' in a and 'disabled' not in a:
                        form['posted'][name] = a.get('value', 'on')
                elif a.get('type') not in ('submit', 'button'):
                    form['posted'][name] = a.get('value', '')
            if tag == 'select':
                self.select = name
                self.select_first = True
        if tag == 'option' and self.select:
            # A select posts its selected option, or its first one.
            if 'selected' in a or self.select_first:
                self.current['posted'][self.select] = a.get('value', '')
            self.select_first = False

    def handle_endtag(self, tag):
        if tag == 'select':
            self.select = None
        if tag == 'form' and self.depth:
            self.depth -= 1

    def form(self):
        form = next(f for f in self.forms if self.marker in f['names'])
        form['posted'].pop('csrfmiddlewaretoken', None)  # masked afresh on every render
        return form


def settings_form(html):
    parser = FormFields('academic_term')
    parser.feed(html)
    return parser.form()


class RefreshPagesNTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user('rpn-1', name='Avery Admin', member_type='Officer', is_admin=True)
        self.other = make_user('rpn-2', name='Jonah Member')
        self.period = SlatingPeriod.objects.create(name='Spring 2027 Elections', academic_term='Spring 2027',
                                                   status='setup', description='Officer slate.')
        self.role = Role.objects.create(name='Treasurer Role RPN', code='RPNT')
        self.pres = SlatingPosition.objects.create(period=self.period, title='President', code='PRES',
                                                   display_order=1)
        self.treas = SlatingPosition.objects.create(period=self.period, title='Treasurer', code='TREAS',
                                                    display_order=2, role=self.role)
        self.field = SlatingFormField.objects.create(period=self.period, field_name='why', label='Why serve?',
                                                     field_type='textarea', is_required=True)
        self.client = Client()
        self.client.force_login(self.admin)

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, page, hooks, classes=()):
        off = self.get(url)
        self.assertNotIn('rf-ss', off, url)
        for hook in hooks:
            self.assertIn(hook, off, url)
        set_flag(True)
        try:
            on = self.get(url)
            self.assertIn(PAGE_CSS, on)
            self.assertIn(f'<div class="rf-ss {page}">', on, url)
            for cls in classes:
                self.assertIn(cls, on, url)
            for hook in hooks:
                self.assertIn(hook, on, url)
        finally:
            set_flag(False)
        return off, on

    # Period Setup -------------------------------------------------------------

    def test_period_setup(self):
        url = reverse('slating_period_setup', args=[self.period.id])
        hooks = ['name="name"', 'name="academic_term"', 'name="slating_manager"', 'name="description"',
                 'name="nominations_open_at"', 'name="nominations_close_at"', 'name="voting_open_at"',
                 'name="voting_close_at"', 'name="required_approval_percentage"', 'name="candidates_cannot_vote"',
                 'name="min_gpa_requirement"', 'name="gpa_level_2_threshold"', 'value="save"',
                 'value="add_committee_member"', 'value="delete"', 'value="nominations_open"',
                 'id="js-open-positions-modal"', 'id="positionsModal"', 'js-close-positions-modal',
                 'id="js-open-fields-modal"', 'id="fieldsModal"', 'js-close-fields-modal', 'js-confirm-submit',
                 'value="add_default_fields"',
                 reverse('slating_change_status', args=[self.period.id]),
                 reverse('slating_copy_default_positions', args=[self.period.id]),
                 reverse('slating_positions', args=[self.period.id]),
                 reverse('slating_form_builder', args=[self.period.id]),
                 reverse('slating_applications', args=[self.period.id]),
                 reverse('slating_import_applications', args=[self.period.id]),
                 reverse('slating_interviews', args=[self.period.id]),
                 reverse('slating_build_slate', args=[self.period.id]),
                 reverse('slating_manual_results', args=[self.period.id]),
                 reverse('slating_dashboard')]
        off, on = self.off_and_on(url, 'rf-ss-setup', hooks, [
            ' rf-ss-head"', ' rf-ss-card rf-ss-status"', ' rf-ss-strip"', ' rf-ss-modal"', ' rf-ss-form"',
            ' rf-ss-card"', ' rf-ss-card-head', ' rf-ss-actions"', ' rf-ss-primary"', ' rf-ss-stats"',
            ' rf-ss-nav"', ' rf-ss-danger"'])
        self.assertEqual(settings_form(on)['controls'], settings_form(off)['controls'])
        self.assertEqual(settings_form(on)['posted'], settings_form(off)['posted'])
        # The page's scripts are byte-for-byte the same (but for the nonce).
        scripts = lambda html: [re.sub(r'nonce="[^"]*"', '', s) for s in re.findall(r'(?s)<script.*?</script>', html)]
        self.assertEqual(scripts(on), scripts(off))

    def test_new_period_page(self):
        url = reverse('slating_create_period')
        off, on = self.off_and_on(url, 'rf-ss-setup', ['name="name"', 'name="academic_term"', 'Create Period'],
                                  [' rf-ss-help"'])
        self.assertEqual(settings_form(on)['controls'], settings_form(off)['controls'])

    def _snapshot(self):
        p = SlatingPeriod.objects.get(pk=self.period.pk)
        return {f.name: getattr(p, f.attname) for f in p._meta.concrete_fields if f.name not in ('updated_at',)}

    def _save(self, on, changes):
        set_flag(on)
        try:
            form = settings_form(self.get(reverse('slating_period_setup', args=[self.period.id])))
        finally:
            set_flag(False)
        payload = dict(form['posted'])
        payload.update(changes)
        response = self.client.post(reverse('slating_period_setup', args=[self.period.id]), payload)
        self.assertIn(response.status_code, (302, 303))
        return self._snapshot(), sorted(payload)

    def test_saving_with_the_refresh_on_changes_the_same_values(self):
        changes = {'name': 'Spring 2027 Officer Elections', 'required_approval_percentage': '66',
                   'min_gpa_requirement': '2.75', 'description': 'Changed.'}
        self.get(reverse('slating_period_setup', args=[self.period.id]))  # backfills the committee first
        before = SlatingPeriod.objects.get(pk=self.period.pk)
        off_result, off_keys = self._save(False, changes)
        self.assertEqual(off_result['name'], 'Spring 2027 Officer Elections')
        before.save()  # back to how it was
        self.assertNotEqual(self._snapshot()['name'], off_result['name'])
        on_result, on_keys = self._save(True, changes)
        self.assertEqual(on_keys, off_keys)
        self.assertEqual(on_result, off_result)

    def test_positions_panel(self):
        from src.models import Slate, SlateCandidate
        self.period.status = 'deliberation'
        self.period.save()
        slate = Slate.objects.create(period=self.period, is_approved=True)
        SlateCandidate.objects.create(slate=slate, position=self.pres, write_in_member=self.other)
        url = reverse('slating_period_setup', args=[self.period.id])
        off = self.get(url)
        self.assertIn('id="positions"', off)
        self.assertNotIn('rf-ss', off)
        set_flag(True)
        try:
            on = self.get(url)
        finally:
            set_flag(False)
        self.assertIn(' rf-ss-card rf-ss-panel"', on)
        self.assertIn(reverse('slating_position_action', args=[self.period.id]), on)

    # Positions and Form Builder ----------------------------------------------

    def test_positions(self):
        url = reverse('slating_positions', args=[self.period.id])
        self.off_and_on(url, 'rf-ss-positions', [
            'id="js-open-add-position-modal"', 'id="positionsList"', 'data-position-id="%s"' % self.pres.id,
            'js-open-edit-modal', reverse('slating_edit_position', args=[self.period.id, self.pres.id]),
            reverse('slating_delete_position', args=[self.period.id, self.pres.id]),
            reverse('slating_add_position', args=[self.period.id]),
            reverse('slating_copy_default_positions', args=[self.period.id]),
            reverse('slating_period_setup', args=[self.period.id]),
            'id="addPositionModal"', 'id="editPositionModal"', 'id="editPositionForm"',
            'js-close-add-position-modal', 'js-close-edit-position-modal', 'name="title"', 'name="code"',
            'name="description"', 'name="role"', 'name="min_gpa"', 'name="allow_abstain"', 'id="edit_title"',
            'id="edit_role"', 'id="edit_min_gpa"', 'id="edit_allow_abstain"', 'id="add_allow_abstain"',
            'class="position-item'], [' rf-ss-head"', ' rf-ss-primary"', ' rf-ss-card"', ' rf-ss-card-head"',
                                      ' rf-ss-list"', ' rf-ss-modal"', ' rf-ss-modal-foot"', ' rf-ss-note"'])

    def test_form_builder(self):
        url = reverse('slating_form_builder', args=[self.period.id])
        self.off_and_on(url, 'rf-ss-builder', [
            'id="js-open-add-field-modal"', 'id="fieldsList"', 'data-field-id="%s"' % self.field.id,
            'js-edit-field', 'data-field-name="why"', 'value="delete_field"', 'value="add_default_fields"',
            'id="addFieldModal"', 'id="fieldForm"', 'id="formAction"', 'id="editFieldId"', 'id="modalTitle"',
            'name="field_type"', 'name="label"', 'name="field_name"', 'name="section"', 'name="help_text"',
            'name="options"', 'id="optionsField"', 'name="is_required"', 'name="show_in_review"',
            'js-close-field-modal', 'class="drag-handle', 'class="field-item',
            reverse('slating_period_setup', args=[self.period.id])],
            [' rf-ss-head"', ' rf-ss-primary"', ' rf-ss-card"', ' rf-ss-list"', ' rf-ss-types"',
             ' rf-ss-modal"', ' rf-ss-modal-foot"'])

    # Import, fill in answers ----------------------------------------------------

    def test_import_upload_step(self):
        url = reverse('slating_import_applications', args=[self.period.id])
        self.off_and_on(url, 'rf-ss-import', [
            'id="csv-file"', 'id="csv-upload-form"', 'data-csv-target', 'name="step"', 'value="upload"',
            'id="csv-file-error"'], [' class="rf-ss-head"', ' rf-ss-card"', ' rf-ss-primary"'])

    def test_import_preview_step(self):
        url = reverse('slating_import_applications', args=[self.period.id])
        self.other.email = 'jonah@test.org'
        self.other.save()
        csv = 'Email Address,What positions are you running for?,Why serve?\njonah@test.org,President,To help\n' \
              'ghost@test.org,Treasurer,?\n'
        post = {'step': 'preview', 'csv_text': csv, 'csv_name': 'responses.csv', 'keep_unmapped': '1'}
        response = self.client.post(url, {'step': 'upload', 'csv_text': csv, 'csv_name': 'responses.csv'})
        self.assertEqual(response.status_code, 200)
        cols = response.context['columns']
        post.update({f'col_{c["index"]}': c['kind'] for c in cols})
        post.update({f'colhead_{c["index"]}': c['header'] for c in cols})
        off = self.client.post(url, post).content.decode()
        self.assertNotIn('rf-ss', off)
        set_flag(True)
        try:
            on = self.client.post(url, post).content.decode()
        finally:
            set_flag(False)
        for cls in ('<div class="rf-ss rf-ss-import">', ' rf-ss-card"', ' rf-ss-table"', ' rf-ss-sub"',
                    ' rf-ss-raw"', ' rf-ss-who"', ' rf-ss-primary"'):
            self.assertIn(cls, on)
        for hook in ('id="import-form"', 'name="csv_text"', 'name="csv_name"', 'name="keep_unmapped"',
                     'value="preview"', 'name="col_0"', 'name="colhead_0"', 'name="who_'):
            self.assertIn(hook, on)
        parse = lambda html: [f['controls'] for f in (lambda p: (p.feed(html), p)[1])(FormFields('csv_text')).forms]
        self.assertEqual(parse(on), parse(off))

    def test_fill_imported_answers(self):
        app = SlatingApplication.objects.create(period=self.period, applicant=self.other, status='submitted',
                                                imported_at=timezone.now())
        url = reverse('slating_fill_imported_answers', args=[self.period.id, app.id])
        self.off_and_on(url, 'rf-ss-fill', [
            f'name="field_{self.field.id}"', 'Save answers',
            reverse('slating_app_detail', args=[self.period.id, app.id])],
            [' class="rf-ss-head"', ' rf-ss-card rf-ss-padded"', ' rf-ss-actions"', ' rf-ss-primary"'])

    # Transfer admin, transition ---------------------------------------------------

    def test_transfer_admin(self):
        self.get(reverse('slating_period_setup', args=[self.period.id]))  # backfills the committee
        url = reverse('slating_transfer_admin', args=[self.period.id])
        self.off_and_on(url, 'rf-ss-transfer', [
            'name="new_admin"', 'name="reason"', 'csrfmiddlewaretoken',
            reverse('slating_period_setup', args=[self.period.id])],
            [' class="rf-ss-head"', ' rf-ss-note"', ' rf-ss-card"', ' rf-ss-card-head"', ' rf-ss-card-foot"'])

    def test_transition_pages(self):
        self.period.status = 'results_published'
        self.period.save()
        url = reverse('slating_transition', args=[self.period.id])
        self.off_and_on(url, 'rf-ss-transition', [
            'value="confirm"', f'name="enable_{self.treas.id}"', f'name="member_{self.treas.id}"',
            'name="timing"', 'id="timing_immediate"', 'id="timing_scheduled"', 'id="dateInputs"',
            'name="effective_date"', 'name="effective_time"', 'id="warningText"', 'transition-checkbox',
            f'member-select-{self.treas.id}', reverse('slating_results', args=[self.period.id])],
            [' class="rf-ss-head"', ' rf-ss-note"', ' class="rf-ss-form"', ' rf-ss-card"', ' rf-ss-actions"',
             ' rf-ss-primary"'])
        post = {'action': 'confirm', f'enable_{self.treas.id}': 'on', f'member_{self.treas.id}': self.other.pk,
                'timing': 'immediate'}
        off = self.client.post(url, post).content.decode()
        self.assertIn('Confirm the officer transition', off)
        self.assertNotIn('rf-ss', off)
        set_flag(True)
        try:
            on = self.client.post(url, post).content.decode()
        finally:
            set_flag(False)
        for cls in ('<div class="rf-ss rf-ss-review">', ' class="rf-ss-head"', ' rf-ss-card"', ' rf-ss-actions"',
                    ' rf-ss-primary"'):
            self.assertIn(cls, on)
        for hook in ('name="reviewed"', f'name="member_{self.treas.id}"', 'value="confirm"',
                     reverse('slating_transition', args=[self.period.id])):
            self.assertIn(hook, on)
        self.assertFalse(SlatingPeriod.objects.get(pk=self.period.pk).officer_transition_completed)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    adds classes inside an existing tag, or the wrapper <div> and its </div>
    on the slating_content block's own lines."""

    BASE = Path(settings.BASE_DIR)
    TEMPLATES = ['period_setup.html', 'positions.html', '_positions_panel.html', 'form_builder.html',
                 'import_applications.html', '_import_raw.html', '_import_who.html', 'fill_imported_answers.html',
                 'transfer_admin.html', 'transition_confirm.html', 'transition_review.html']
    WRAPPED = {'period_setup.html', 'positions.html', 'form_builder.html', 'import_applications.html',
               'fill_imported_answers.html', 'transfer_admin.html', 'transition_confirm.html',
               'transition_review.html'}

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% block slating_content %\}\{% if ui_refresh %\}<div class="rf-ss rf-ss-[\w-]+">\{% endif %\}',
                      '{% block slating_content %}', text)
        text = re.sub(r'\{% if ui_refresh %\}</div>\{% endif %\}\{% endblock %\}', '{% endblock %}', text)
        return re.sub(r'\{% if ui_refresh %\}( rf-ss[\w -]*| class="rf-ss[\w -]*")\{% endif %\}', '', text)

    def read(self, name):
        return (self.BASE / 'templates' / 'slating' / name).read_text(encoding='utf-8')

    def test_every_switch_wraps_unchanged_lines(self):
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                text = self.read(name)
                self.assertIn('{% if ui_refresh %}', text)
                stripped = self.strip_switches(text)
                self.assertNotIn('ui_refresh', stripped)
                self.assertNotIn('rf-ss', stripped)
                self.assertNotRegex(stripped, r'\brf-')
                # Every switch keeps its line: the stripped file has as many lines.
                self.assertEqual(stripped.count('\n'), text.count('\n'))
                if name in self.WRAPPED:
                    self.assertEqual(text.count('{% if ui_refresh %}<div class="rf-ss '), 1)
                    self.assertEqual(text.count('{% if ui_refresh %}</div>{% endif %}'), 1)

    def test_switches_add_no_inline_script_or_style(self):
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                for switch in re.findall(r'\{% if ui_refresh %\}.*?\{% endif %\}', self.read(name)):
                    self.assertNotIn('<script', switch)
                    self.assertNotIn('style', switch)
                    self.assertNotRegex(switch, r'\son\w+=')

    def test_switched_templates_render_without_the_flag(self):
        from django.template.loader import get_template
        for name in ('_import_raw.html',):
            html = get_template(f'slating/{name}').render({'raw': [('Q', 'A')], 'where': 'row 2'})
            self.assertNotIn('rf-ss', html)
            html = get_template(f'slating/{name}').render({'raw': [('Q', 'A')], 'where': 'row 2', 'ui_refresh': True})
            self.assertIn(' rf-ss-raw"', html)

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
        self.assertGreater(len(rules), 40)
        for rule in rules:
            for selector in self.top_level_split(rule):
                selector = selector.strip()
                if selector.startswith(':root.ui-refresh.dark'):
                    self.assertIn('.rf-ss', selector)
                    continue
                self.assertTrue(selector.startswith('.ui-refresh '), selector)
                self.assertIn('.rf-ss', selector)

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (self.BASE / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
