"""
The website refresh (src/ui_refresh.py) on officer tools, batch B: Chapter
Minutes (list and editor), Passed Resolutions (list, form, delete) and their
Section Impacts (list, form, delete), Roles, a role's Knowledge Base and its
history, Role Transitions and a checklist, All Documents, Upload Report, All
Activity, Chapter Stats, Contact Messages, GroupMe, Mobile App and Edit
Landing Page.

What must hold:
* off: the old markup (no page stylesheet, switch classes or app rows);
* on: the app-style markup and the page stylesheet, with every link, form,
  field, permission and script hook the pages had;
* marking a contact message read and deleting a section impact still work
  with the refresh on;
* the templates' switches only wrap unchanged lines (strip them and no
  `ui_refresh` or `rf-` is left), and the new includes carry no inline
  script or style (CSP).
"""
import re
from datetime import date, time, timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import ParliamentUser
from src.models.committees import Committee
from src.models.documents import ChapterMinutes, CommitteeDocument
from src.models.landing import ContactSubmission, PassedResolution, ResolutionSectionImpact
from src.models.legislation import Legislation
from src.models.transitions import (RoleKnowledgeBase, RoleKnowledgeBaseRevision, TransitionChecklistItem,
                                    TransitionChecklistStatus)
from src.models.users import Role, RoleHistory
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/officer-b.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-g-12345!')
    user.save()
    return user


class RefreshPagesGTests(TestCase):
    def setUp(self):
        cache.clear()
        self.officer = make_user('rpg-1', name='Pat Presidente', member_type='Officer', is_admin=True,
                                 email='rpg1@example.test')
        self.member = make_user('rpg-2', name='Jordan Treasurer', email='rpg2@example.test')
        self.role = Role.objects.create(name='Treasurer', code='TREASG', one_per_chapter=True,
                                        description='Collects dues.')
        self.admin_role = Role.objects.create(name='President', code='PRESG', grants_admin=True)
        self.member.roles.add(self.role)
        self.history = RoleHistory.objects.create(user=self.member, role_name='Treasurer', start_semester='Fall 2026')
        item = TransitionChecklistItem.objects.create(role=self.role, text='Hand over the bank login', order=0)
        self.status = TransitionChecklistStatus.objects.create(item=item, role_history=self.history)
        kb = RoleKnowledgeBase.objects.create(role=self.role)
        RoleKnowledgeBaseRevision.objects.create(knowledge_base=kb, content='Dues are due Friday.', edited_by=self.member)
        RoleKnowledgeBaseRevision.objects.create(knowledge_base=kb, content='Dues are due the second Friday.',
                                                 edited_by=self.officer)
        self.resolution = PassedResolution.objects.create(title='Resolution 2026-03: Officer terms',
                                                          description='Changes officer terms.',
                                                          date_passed=date(2026, 10, 12), border_color='blue',
                                                          created_by=self.officer)
        self.hidden = PassedResolution.objects.create(title='Retired rule', description='Gone.',
                                                      date_passed=date(2025, 4, 1), is_active=False)
        self.impact = ResolutionSectionImpact.objects.create(resolution=self.resolution,
                                                             section_name='Constitution Article IV',
                                                             section_type='constitution',
                                                             section_anchor='#const-leadership')
        self.minutes = ChapterMinutes.objects.create(title='Chapter Meeting Nov 2', date=date(2026, 11, 2),
                                                     start_time=time(19, 0), created_by=self.officer, status='draft')
        self.published = ChapterMinutes.objects.create(title='Chapter Meeting Oct 26', date=date(2026, 10, 26),
                                                       start_time=time(19, 0), created_by=self.officer,
                                                       status='published', attendance_taken=True)
        self.contact = ContactSubmission.objects.create(name='Pat Parent', email='parent@example.test',
                                                        topic='Recruitment', message='When is rush?')
        committee = Committee.objects.create(name='Finance Committee', code='FING')
        self.doc = CommitteeDocument(committee=committee, title='October budget report', uploaded_by=self.member,
                                     document_type='report', published_to_chapter=False)
        self.doc.document.save('rpg-report.pdf', ContentFile(b'%PDF-1.4 test'), save=False)
        self.doc.save()
        self.leg = Legislation.objects.create(title='Spring formal budget', description='Approves the budget.',
                                              posted_by=self.officer, available_at=timezone.now() - timedelta(days=1),
                                              status='draft')
        self.client = Client()
        self.client.force_login(self.officer)

    def tearDown(self):
        self.doc.document.delete(save=False)

    def set_flag(self, on):
        FeatureFlag.objects.update_or_create(
            name='web_refresh',
            defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
        )
        cache.clear()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, page_class, hooks, off_markers=(), on_markers=()):
        """Off: no stylesheet, no page class, the old markers. On: all three
        flipped. Both: every hook."""
        html = self.get(url)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn('rf-ob', html)
        for marker in off_markers:
            self.assertIn(marker, html)
        for hook in hooks:
            self.assertIn(hook, html)
        self.set_flag(True)
        html = self.get(url)
        self.assertIn(PAGE_CSS, html)
        self.assertIn(f'rf-mp rf-ob {page_class}"', html)
        for marker in off_markers:
            self.assertNotIn(marker, html)
        for marker in on_markers:
            self.assertIn(marker, html)
        for hook in hooks:
            self.assertIn(hook, html)
        self.set_flag(False)
        return html

    # Minutes ---------------------------------------------------------------------

    def test_minutes_list(self):
        self.off_and_on(
            reverse('chapter_minutes_list'), 'rf-ob-minutes',
            [reverse('edit_chapter_minutes', args=[self.minutes.id]), reverse('create_chapter_minutes'),
             'id="js-open-create-modal"', 'js-show-delete-modal', f'data-minutes-id="{self.minutes.id}"',
             'data-minutes-status="published"', 'name="title"', 'name="start_time"', 'id="deleteForm"',
             'Chapter Meeting Nov 2'],
            off_markers=['hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors">'],
            on_markers=['<span class="rf-chip is-warning">Draft</span>', 'class="rf-card rf-ob-list"',
                        ' rf-ob-modal"'])

    def test_minutes_editor(self):
        self.off_and_on(
            reverse('edit_chapter_minutes', args=[self.minutes.id]), 'rf-ob-editor',
            ['id="previewPdfBtn"', 'id="downloadPdfBtn"', 'id="saveDraftBtn"', 'id="attendanceTableBody"',
             'id="sectionsContainer"', 'id="publishForm"', reverse('publish_chapter_minutes', args=[self.minutes.id]),
             'name="visibility"', 'id="endTimeInput"'],
            on_markers=[' rf-ob-editor-actions"', ' rf-ob-crumbs"'])

    # Resolutions and section impacts ---------------------------------------------

    def test_resolutions(self):
        self.off_and_on(
            reverse('manage_resolutions'), 'rf-ob-res-page',
            [reverse('create_resolution'), reverse('manage_section_impacts', args=[self.resolution.id]),
             reverse('edit_resolution', args=[self.resolution.id]),
             reverse('delete_resolution', args=[self.hidden.id]), 'Constitution Article IV', reverse('officer_home')],
            off_markers=['Display Order: 0 | Border Color'],
            on_markers=['rf-card rf-ob-res is-blue', '<span class="rf-chip is-warning">Hidden</span>',
                        'No section impacts added yet'])

    def test_resolution_forms_and_delete(self):
        self.off_and_on(reverse('edit_resolution', args=[self.resolution.id]), 'rf-ob-form-page',
                        ['name="title"', 'name="description"', 'name="date_passed"', 'name="border_color"',
                         'name="display_order"', 'name="is_active"', 'enctype="multipart/form-data"',
                         reverse('manage_resolutions')],
                        on_markers=[' rf-ob-form"', ' rf-ob-actions"'])
        self.off_and_on(reverse('delete_resolution', args=[self.resolution.id]), 'rf-ob-confirm',
                        ['Yes, Delete Resolution', reverse('manage_resolutions'), 'csrfmiddlewaretoken'],
                        on_markers=[' rf-ob-note is-danger"', ' rf-ob-confirm-card"'])

    def test_section_impacts(self):
        self.off_and_on(
            reverse('manage_section_impacts', args=[self.resolution.id]), 'rf-ob-impacts',
            [reverse('add_section_impact', args=[self.resolution.id]), reverse('edit_section_impact', args=[self.impact.id]),
             reverse('delete_section_impact', args=[self.impact.id]), '#const-leadership'],
            off_markers=['<table class="min-w-full divide-y divide-gray-200">'],
            on_markers=['rf-row rf-ob-impact', 'rf-ob-danger-link'])
        self.off_and_on(reverse('edit_section_impact', args=[self.impact.id]), 'rf-ob-form-page',
                        ['name="section_name"', 'name="section_type"', 'name="section_anchor"',
                         'name="external_url"', 'name="display_order"'],
                        on_markers=[' rf-ob-crumbs"', ' rf-ob-note"'])
        self.off_and_on(reverse('delete_section_impact', args=[self.impact.id]), 'rf-ob-confirm',
                        ['Yes, Delete Tag', reverse('manage_section_impacts', args=[self.resolution.id])],
                        on_markers=[' rf-ob-facts"'])

    def test_delete_section_impact_with_refresh_on(self):
        self.set_flag(True)
        response = self.client.post(reverse('delete_section_impact', args=[self.impact.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ResolutionSectionImpact.objects.filter(id=self.impact.id).exists())

    # Roles, knowledge base, transitions --------------------------------------------

    def test_roles(self):
        self.off_and_on(
            reverse('manage_roles'), 'rf-ob-roles',
            ['id="js-open-add-role-modal"', 'js-open-edit-role-modal', 'js-open-delete-role-modal',
             f'data-role-id="{self.role.id}"', 'data-holder-count="1"', 'data-role-name="Treasurer"',
             reverse('role_knowledge_base', kwargs={'role_id': self.role.id}), 'id="add-role-form"',
             'id="edit-role-form"', 'id="delete-role-submit"', 'TREASG', 'Jordan Treasurer'],
            off_markers=['<th scope="col"'],
            on_markers=['rf-row rf-ob-role', '<span class="rf-chip rf-ob-chip-purple">Grants Admin</span>'])

    def test_knowledge_base_and_history(self):
        self.off_and_on(reverse('role_knowledge_base', kwargs={'role_id': self.role.id}), 'rf-ob-kb',
                        [reverse('edit_role_knowledge_base', kwargs={'role_id': self.role.id}), 'name="content"',
                         reverse('role_knowledge_base_history', kwargs={'role_id': self.role.id}),
                         'id="kb-view"', 'Dues are due the second Friday.'],
                        on_markers=[' rf-ob-kb-text"'])
        self.off_and_on(reverse('role_knowledge_base_history', kwargs={'role_id': self.role.id}), 'rf-ob-kb',
                        [reverse('role_knowledge_base', kwargs={'role_id': self.role.id}), 'Dues are due Friday.'],
                        on_markers=[' rf-chip is-success"'])

    def test_transitions_and_checklist(self):
        self.off_and_on(
            reverse('role_transitions'), 'rf-ob-transitions',
            ['js-open-transfer-modal', f'data-role-id="{self.role.id}"', 'id="transfer-form"',
             reverse('transition_checklist', kwargs={'role_history_id': self.history.id}),
             reverse('role_knowledge_base', kwargs={'role_id': self.role.id}), 'const ROLES ='],
            off_markers=['<th class="px-6 py-3 text-left'],
            on_markers=['rf-row rf-ob-transition', '<span class="rf-chip is-warning">Vacant</span>', ' rf-ob-stats"'])
        self.off_and_on(reverse('transition_checklist', kwargs={'role_history_id': self.history.id}),
                        'rf-ob-checklist',
                        [f'data-status-id="{self.status.id}"', 'data-toggle-item', 'id="progress-bar"',
                         'id="progress-done"', 'Hand over the bank login'],
                        on_markers=[' rf-ob-card rf-ob-progress"'])

    # Documents, upload, activity, stats -------------------------------------------

    def test_all_documents(self):
        self.off_and_on(
            reverse('view_all_reports'), 'rf-ob-reports',
            ['data-tab="reports"', 'id="content-all"', 'tab-button', reverse('upload_report'), self.doc.document.url,
             'October budget report'],
            off_markers=['hover:shadow-md transition-shadow'],
            on_markers=['rf-row rf-ob-doc', '<span class="rf-chip is-warning">Not Published</span>', ' rf-ob-tabs"'])

    def test_upload_report(self):
        self.off_and_on(reverse('upload_report'), 'rf-ob-form-page',
                        ['name="committee"', 'name="document_type"', 'name="title"', 'name="document"',
                         'name="meeting_date"', 'name="published_to_chapter"', 'enctype="multipart/form-data"'],
                        on_markers=[' rf-ob-form"', ' rf-ob-btn2"'])

    def test_all_activity(self):
        self.off_and_on(
            reverse('view_all_activity'), 'rf-ob-activity',
            ['data-activity-tab="committee-docs"', 'data-sub-tab-status="draft"', 'id="chapter-draft"',
             'chapter-subtab', reverse('legislation_detail', args=[self.leg.id]), 'Spring formal budget',
             'October budget report'],
            off_markers=['hover:shadow-md transition-shadow'],
            on_markers=['rf-row rf-ob-leg', 'rf-row rf-ob-doc', ' rf-ob-tabs is-underline"'])

    def test_chapter_stats(self):
        self.off_and_on(reverse('chapter_stats'), 'rf-ob-wide rf-ob-stats-page',
                        ['?days=30', '?days=365', reverse('officer_home')],
                        on_markers=[' rf-ob-range"', ' rf-ob-tiles"'])

    # Contact, GroupMe, Mobile App, Landing page ------------------------------------

    def test_contact_messages(self):
        self.off_and_on(
            reverse('contact_submissions'), 'rf-ob-contact-page',
            [reverse('mark_all_contact_read'), reverse('mark_contact_read', args=[self.contact.pk]),
             'mailto:parent@example.test', 'When is rush?'],
            off_markers=['border-blue-300 dark:border-blue-700'],
            on_markers=['rf-ob-contact is-unread', 'class="rf-ob-dot"'])

    def test_mark_contact_read_with_refresh_on(self):
        self.set_flag(True)
        response = self.client.post(reverse('mark_contact_read', args=[self.contact.pk]))
        self.assertIn(response.status_code, (200, 302))
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_read)

    def test_groupme(self):
        self.off_and_on(reverse('groupme_dashboard'), 'rf-ob-groupme',
                        [reverse('groupme_save_settings'), 'id="gm-token"', 'id="gm-new-name"'],
                        on_markers=[' rf-ob-note is-warning"'])

    def test_mobile_app(self):
        html = self.off_and_on(reverse('officer_mobile_app'), 'rf-ob-wide rf-ob-mobile',
                               ['?tab=notifications'], on_markers=[' rf-ob-hero-tabs"', ' rf-ob-tiles"'])
        self.assertIn('?tab=location', html)

    def test_edit_landing_page(self):
        self.off_and_on(reverse('edit_landing_page'), 'rf-ob-landing',
                        ['id="content-form"', 'id="quill-wwa"', 'name="who_we_are_html"', 'id="save-btn"',
                         'id="img-controls"', 'quill.min.js'])

    def test_officers_subnav(self):
        """admin_base.html's sub-nav, shared by every officer page (batch A's
        Manage Events included): app pills with the refresh on, the current
        page marked as before, every link kept."""
        for name in ('manage_roles', 'manage_events'):
            with self.subTest(page=name):
                html = self.get(reverse(name))
                self.assertNotIn('rf-osub', html)
                self.set_flag(True)
                html = self.get(reverse(name))
                self.assertIn('text-gray-300 rf-osub"', html)
                for link in ('officer_home', 'user_list', 'manage_roles', 'manage_events', 'chapter_minutes_list',
                             'review_excuses', 'manage_resolutions', 'groupme_dashboard', 'admin_v2_dashboard'):
                    self.assertIn(f'href="{reverse(link)}"', html)
                self.assertIn('text-white bg-gray-700', html)
                self.set_flag(False)

    def test_member_cannot_open_officer_pages_with_refresh_on(self):
        self.set_flag(True)
        client = Client()
        client.force_login(self.member)
        for name in ('manage_roles', 'chapter_minutes_list', 'manage_resolutions'):
            with self.subTest(page=name):
                self.assertNotEqual(client.get(reverse(name)).status_code, 200)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    either adds classes (`{% if ui_refresh %} rf-…{% endif %}`) or puts an
    include in front of the unchanged old block, closed by `{# /rf #}`."""

    TEMPLATES = ['templates/officer/' + name for name in (
        'chapter_minutes_list.html', 'chapter_minutes_editor.html', 'manage_resolutions.html',
        'resolution_form.html', 'confirm_delete_resolution.html', 'manage_section_impacts.html',
        'section_impact_form.html', 'confirm_delete_section_impact.html', 'manage_roles.html',
        'role_knowledge_base.html', 'role_knowledge_base_history.html', 'role_transitions.html',
        'transition_checklist.html', 'view_all_reports.html', 'upload_report.html', 'view_all_activity.html',
        'chapter_stats.html', 'contact_submissions.html', 'groupme_dashboard.html', 'mobile_app.html',
        'edit_landing_page.html', 'partials/document_list.html', 'partials/legislation_list.html')] + [
        'templates/admin_base.html']
    NEW = ['templates/officer_refresh/' + name for name in (
        '_minutes_list.html', '_resolutions.html', '_section_impacts.html', '_roles.html', '_transitions.html',
        '_contact.html', '_document_list.html', '_legislation_list.html')] + ['static/' + PAGE_CSS]

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% if ui_refresh %\}\{% include "[^"]+"[^%]*%\}\{% else %\}', '', text)
        text = text.replace('{% endif %}{# /rf #}', '')
        return re.sub(r'\{% if ui_refresh %\}( [\w -]+)\{% endif %\}', '', text)

    def test_every_switch_wraps_unchanged_lines(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                text = (base / name).read_text(encoding='utf-8')
                self.assertIn('{% if ui_refresh %}', text)
                stripped = self.strip_switches(text)
                self.assertNotIn('ui_refresh', stripped)
                self.assertNotRegex(stripped, r'\brf-')
                self.assertNotIn('{# /rf #}', stripped)

    def test_new_files_exist_and_carry_no_inline_script_or_style(self):
        base = Path(settings.BASE_DIR)
        for name in self.NEW:
            with self.subTest(file=name):
                text = (base / name).read_text(encoding='utf-8')
                if name.endswith('.html'):
                    self.assertNotIn('<script', text)
                    self.assertNotIn('<style', text)
                    self.assertNotIn('style="', text)
                    self.assertNotRegex(text, r'\son[a-z]+=')
                else:
                    css = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
                    self.assertNotRegex(css, r'!\s*important')
                    self.assertNotIn('@import', css)
                    self.assertNotIn('http', css)

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (Path(settings.BASE_DIR) / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
