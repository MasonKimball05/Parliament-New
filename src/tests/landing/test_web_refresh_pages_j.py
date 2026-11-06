"""
The website refresh (src/ui_refresh.py) on the officers' Service Hours pages
(dashboard, submissions and a submission's review, periods, expectations,
service events and their form, detail and attendance, the form builder),
the member's submission view and edit pages, Chapter Documents (library,
manage, upload, one document, the viewer) and the legislation pages (a
bill's detail, the tracker, edit, a draft, assign role, vote results,
verify a receipt, passed resolutions).

What must hold:
* off: the old markup (no page stylesheet, no switch classes or app rows);
* on: the app-style markup and the page stylesheet, with every link, form,
  field, permission and script hook the pages had;
* bulk-approving submissions with the refresh on still works;
* the templates' switches only wrap unchanged lines (strip them and no
  `ui_refresh` or `rf-` is left), and the new includes carry no inline
  script or style (CSP).
"""
import re
import shutil
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import CommitteeDocument, Event, Legislation, ParliamentUser, Vote
from src.models.documents import ChapterFolder
from src.models.legislation import LegislationDraft
from src.models.service import (ServiceEvent, ServiceHoursSubmission, ServiceMemberExpectation,
                                ServicePeriod)
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/service-docs-legislation.css'
MEDIA = tempfile.mkdtemp(prefix='rf-pages-j-')


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active',
                    email=f'{uid}@samford.edu')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-j-12345!')
    user.save()
    return user


def pdf(name):
    return SimpleUploadedFile(name, b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n',
                              content_type='application/pdf')


def set_flag(on):
    FeatureFlag.objects.update_or_create(
        name='web_refresh',
        defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


@override_settings(MEDIA_ROOT=MEDIA)
class RefreshPagesJTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.officer = make_user('rpj-1', name='Avery Lindqvist', member_type='Officer', is_admin=True)
        self.member = make_user('rpj-2', name='Theo Castellanos')
        self.pledge = make_user('rpj-3', name='Dmitri Halvorsen', member_type='Pledge')
        today = timezone.localdate()
        now = timezone.now()
        self.period = ServicePeriod.objects.create(name='Fall 2026', start_date=today - timedelta(days=30),
                                                   end_date=today + timedelta(days=60),
                                                   default_hours_required=Decimal('10'), created_by=self.officer)
        self.pending = ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=self.member, hours=Decimal('3'), service_date=today - timedelta(days=2),
            organization='Campus Food Pantry', description='Sorted donations.', status='pending')
        batch = uuid.uuid4()
        self.batch = [ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=self.pledge, hours=Decimal('2'), service_date=today - timedelta(days=d),
            organization='Soup Kitchen', description='Lunch service.', status='pending', batch_id=batch)
            for d in (4, 11)]
        self.rejected = ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=self.member, hours=Decimal('2'), service_date=today - timedelta(days=6),
            organization='Animal Shelter', description='Walked dogs.', status='rejected', reviewer_notes='Add a contact.')
        ServiceMemberExpectation.objects.create(period=self.period, member=self.pledge, expected_hours=Decimal('5'),
                                                reason='First semester', created_by=self.officer)
        upcoming = Event.objects.create(title='Habitat Build Day', date_time=now + timedelta(days=5),
                                        location='Community Center', created_by=self.officer, is_active=True)
        past = Event.objects.create(title='Park Cleanup', date_time=now - timedelta(days=9),
                                    location='Riverside Park', created_by=self.officer, is_active=True)
        self.se = ServiceEvent.objects.create(event=upcoming, period=self.period, hours_awarded=Decimal('4'),
                                              created_by=self.officer)
        self.se_past = ServiceEvent.objects.create(event=past, period=self.period, hours_awarded=Decimal('3'),
                                                   hours_applied=True, created_by=self.officer)
        self.folder = ChapterFolder.objects.create(name='Policies', description='House rules', created_by=self.officer)
        self.doc = CommitteeDocument.objects.create(
            title='Risk Management Policy', document=pdf('risk.pdf'), uploaded_by=self.officer,
            chapter_folder=self.folder, published_to_chapter=True, document_type='policy', description='Guests.')
        self.draft_doc = CommitteeDocument.objects.create(
            title='Treasurer Report Q3', document=pdf('treasurer.pdf'), uploaded_by=self.officer,
            published_to_chapter=False, document_type='report')
        self.bill = Legislation.objects.create(
            title='Amend the Standing Rules on Dues', description='A bill.', posted_by=self.officer,
            available_at=now - timedelta(days=10), status='passed', voting_closed=True, passed=True,
            voting_ended_at=now - timedelta(days=2), required_percentage='67', admin_note='Takes effect next term.')
        for user, choice in [(self.officer, 'yes'), (self.member, 'yes'), (self.pledge, 'no')]:
            Vote.objects.create(user=user, legislation=self.bill, vote_choice=choice)
        self.pending_bill = Legislation.objects.create(
            title='Formal Venue Selection', description='Pick a venue.', posted_by=self.officer,
            available_at=now - timedelta(days=1), status='pending')
        self.draft = LegislationDraft.objects.create(author=self.officer, title='Create a Scholarship Committee',
                                                     description='Establishes a committee.')
        self.client = Client()
        self.client.force_login(self.officer)

    # helpers --------------------------------------------------------------------

    def get(self, url, client=None):
        response = (client or self.client).get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, page_class, hooks, off_markers=(), on_markers=(), client=None):
        html = self.get(url, client)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn('rf-sdl', html)
        for marker in off_markers:
            self.assertIn(marker, html)
        for hook in hooks:
            self.assertIn(hook, html)
        set_flag(True)
        html = self.get(url, client)
        self.assertIn(PAGE_CSS, html)
        self.assertRegex(html, r'rf-sdl( rf-sdl-form)? ' + re.escape(page_class) + '"')
        for marker in off_markers:
            self.assertNotIn(marker, html)
        for marker in on_markers:
            self.assertIn(marker, html)
        for hook in hooks:
            self.assertIn(hook, html)
        set_flag(False)
        return html

    # Service Hours: officer pages ---------------------------------------------------

    def test_dashboard(self):
        hooks = [reverse('service_form_builder'), reverse('manage_service_periods'), reverse('service_events_list'),
                 reverse('view_service_submissions') + '?status=pending',
                 reverse('manage_member_expectations', args=[self.period.id]),
                 reverse('manage_service_submission', args=[self.pending.id]),
                 'js-open-adjust-modal', f'data-member-id="{self.member.user_id}"', 'data-member-name="Theo Castellanos"',
                 'id="adjust-modal"', 'id="adjust-form"', 'name="hours"', 'name="reason"', 'name="period_id"',
                 'js-close-adjust-modal', 'id="period"', reverse('add_service_adjustment'), reverse('user_service_dashboard')]
        self.off_and_on(reverse('service_dashboard'), 'rf-shd', hooks,
                        off_markers=['<!-- Member Progress -->', '<!-- Recent Submissions -->'],
                        on_markers=['class="rf-card rf-shd-members"', '<progress class="rf-shd-bar"',
                                    'class="rf-row rf-shd-recent-row"', ' rf-sdl-stats"', ' rf-sdl-head"'])

    def test_submissions_keep_the_bulk_form_and_its_script_hooks(self):
        hooks = ['id="bulk-form"', f'action="{reverse("bulk_actions_service")}"', 'name="bulk_action"', 'id="bulk-submit"',
                 'id="select-all"', 'id="selected-count"', 'name="submission_ids"', 'submission-checkbox',
                 f'value="{self.pending.id}"', f'data-batch-id="{self.batch[0].batch_id}"', 'select-batch-btn',
                 'select all 2', 'multi-date (2)', reverse('manage_service_submission', args=[self.pending.id]),
                 reverse('export_service_csv'), 'name="period"', 'name="status"', 'name="member"', 'Review',
                 'Campus Food Pantry']
        html = self.off_and_on(reverse('view_service_submissions'), 'rf-shsubs', hooks,
                               off_markers=['<!-- Submissions Table -->'],
                               on_markers=['class="rf-card rf-shsubs-list"', 'rf-row rf-row-group rf-shsubs-row'])
        self.assertIn('function updateUI(', html)

    def test_bulk_approve_with_the_refresh_on(self):
        set_flag(True)
        self.assertIn('rf-shsubs-row', self.get(reverse('view_service_submissions')))
        response = self.client.post(reverse('bulk_actions_service'),
                                    {'bulk_action': 'approve', 'submission_ids': [s.id for s in self.batch]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ServiceHoursSubmission.objects.filter(batch_id=self.batch[0].batch_id, status='approved').count(), 2)

    def test_review_page(self):
        self.off_and_on(reverse('manage_service_submission', args=[self.pending.id]), 'rf-shsub',
                        ['id="review-form"', 'name="reviewer_notes"', 'id="approve-btn"', 'id="reject-btn"',
                         'id="action-input"', reverse('view_service_submissions'), 'Campus Food Pantry'])

    def test_periods(self):
        hooks = ['id="create-form"', 'name="action"', 'value="create"', 'value="toggle_active"', 'value="delete"',
                 'toggle-form', 'delete-form', 'data-period-name="Fall 2026"', 'Deactivate', 'Delete',
                 reverse('edit_service_period', args=[self.period.id]),
                 reverse('manage_member_expectations', args=[self.period.id]), reverse('service_dashboard')]
        self.off_and_on(reverse('manage_service_periods'), 'rf-shper', hooks,
                        on_markers=['class="rf-card rf-shper-list"', 'rf-shper-row is-active'])
        self.off_and_on(reverse('edit_service_period', args=[self.period.id]), 'rf-shper-edit',
                        ['id="edit-form"', 'id="save-btn"', 'name="name"', 'name="default_hours_required"',
                         reverse('manage_service_periods')])
        self.off_and_on(reverse('manage_member_expectations', args=[self.period.id]), 'rf-shexp',
                        ['id="add-form"', 'name="expected_hours"', 'name="member"', 'delete-form',
                         'data-member="Dmitri Halvorsen"', 'First semester', reverse('manage_service_periods')])

    def test_events(self):
        hooks = [reverse('service_event_detail', args=[self.se.id]), reverse('edit_service_event', args=[self.se.id]),
                 reverse('service_event_detail', args=[self.se_past.id]), reverse('create_service_event'),
                 'Habitat Build Day', 'Park Cleanup', 'Finalized', 'Pending', 'Recent (last 20)']
        html = self.off_and_on(reverse('service_events_list'), 'rf-shev', hooks,
                               on_markers=['rf-row rf-row-group rf-shev-row', 'class="rf-date"'])
        self.assertNotIn(reverse('edit_service_event', args=[self.se_past.id]), html)
        self.off_and_on(reverse('create_service_event'), 'rf-shev-form',
                        ['name="title"', 'name="date_time"', 'name="period"', 'name="hours_awarded"',
                         'name="reminder_1_enabled"', 'name="email_reminder_body"', 'x-model="enabled"'])
        self.off_and_on(reverse('service_event_detail', args=[self.se.id]), 'rf-shev-detail',
                        [reverse('service_event_attendance', args=[self.se.id]), reverse('delete_service_event', args=[self.se.id]),
                         'data-confirm="Delete this service event?', 'Attendance Roster'])
        self.off_and_on(reverse('service_event_attendance', args=[self.se.id]), 'rf-shev-att',
                        ['id="attendance-form"', 'member-checkbox', 'name="present"', 'id="select-all-btn"',
                         'id="clear-all-btn"', 'id="present-counter"', 'id="finalize-btn"', 'value="finalize"',
                         f'name="hours_override_{self.member.user_id}"'])

    def test_form_builder_keeps_its_script(self):
        html = self.off_and_on(reverse('service_form_builder'), 'rf-shfb',
                               ['id="js-open-preview-modal"', 'id="js-open-add-field-modal"', 'id="fieldForm"',
                                'js-close-field-modal', reverse('service_dashboard')])
        self.assertIn('<script nonce=', html)

    # Service Hours: the member's own submission ----------------------------------------

    def test_member_submission_view_and_edit(self):
        client = Client()
        client.force_login(self.member)
        self.off_and_on(reverse('user_view_service_submission', args=[self.rejected.id]), 'rf-shsub',
                        [reverse('edit_service_submission', args=[self.rejected.id]), 'Submission Rejected',
                         'Add a contact.', reverse('user_service_dashboard')], client=client)
        self.off_and_on(reverse('edit_service_submission', args=[self.rejected.id]), 'rf-shsub-edit',
                        ['id="edit-form"', 'name="hours"', 'name="organization"', 'enctype="multipart/form-data"',
                         'Resubmitting After Rejection'], client=client)

    # Chapter documents --------------------------------------------------------------

    def test_chapter_documents(self):
        hooks = [reverse('view_chapter_document', args=[self.doc.id]), reverse('manage_chapter_document', args=[self.doc.id]),
                 self.doc.document.url, reverse('manage_chapter_documents'), reverse('upload_chapter_document'),
                 reverse('delete_folder', args=[self.folder.id]), 'js-confirm-submit', 'js-stop-propagation',
                 'id="createFolderModal"', 'name="folder_name"', 'Risk Management Policy', 'Policies']
        html = self.off_and_on(reverse('chapter_documents'), 'rf-docs', hooks,
                               on_markers=['rf-row rf-row-group rf-docs-row', 'rf-chip rf-docs-type is-policy'])
        self.assertIn(' download>Download</a>', html)

    def test_chapter_documents_member_sees_no_officer_links(self):
        client = Client()
        client.force_login(self.member)
        set_flag(True)
        html = self.get(reverse('chapter_documents'), client)
        self.assertIn('rf-docs-row', html)
        self.assertNotIn(reverse('manage_chapter_document', args=[self.doc.id]), html)
        self.assertNotIn(reverse('upload_chapter_document'), html)

    def test_manage_documents_and_forms(self):
        hooks = [reverse('manage_chapter_document', args=[self.doc.id]),
                 reverse('manage_chapter_document', args=[self.draft_doc.id]), self.draft_doc.document.url,
                 'Draft Documents (Not Published)', 'Treasurer Report Q3', 'id="js-show-create-folder-modal"',
                 reverse('chapter_documents')]
        self.off_and_on(reverse('manage_chapter_documents'), 'rf-docs rf-docs-manage', hooks,
                        on_markers=['<span class="rf-chip is-warning">Draft</span>', ' rf-docs-drafts"'])
        self.off_and_on(reverse('upload_chapter_document'), 'rf-docs-form',
                        ['enctype="multipart/form-data"', 'name="title"', 'name="file"', 'name="document_type"',
                         'name="chapter_folder"', 'name="publish_now"', reverse('chapter_documents')])
        self.off_and_on(reverse('manage_chapter_document', args=[self.doc.id]), 'rf-docs-form',
                        ['value="update"', 'value="delete"', 'name="published_to_chapter"', 'js-confirm-submit',
                         'Danger Zone'])

    def test_document_viewer(self):
        self.off_and_on(reverse('view_chapter_document', args=[self.doc.id]), 'rf-docview',
                        ['id="pdf-desktop-iframe"', 'id="pdf-mobile-view"', ' download ', 'Risk Management Policy'],
                        on_markers=[' rf-docview-bar"'])

    # Legislation ----------------------------------------------------------------------

    def test_legislation_detail_pages(self):
        for url in (reverse('legislation_detail', args=[self.bill.id]),
                    reverse('passed_legislation_detail', args=[self.bill.id])):
            self.off_and_on(url, 'rf-leg',
                            [reverse('passed_legislation'),
                             'id="voteChart"',
                             'Takes effect next term.', 'Vote Results'])

    def test_tracker(self):
        hooks = ['?status=all', '?status=pending', '?status=personal', 'js-toggle-voting-details',
                 f'id="voting-details-{self.bill.id}"', 'id="js-open-add-legislation-modal"', 'js-show-note-editor',
                 reverse('passed_legislation_detail', kwargs={'pk': self.bill.id}), 'Amend the Standing Rules on Dues']
        self.off_and_on(reverse('passed_legislation'), 'rf-legs', hooks, on_markers=[' rf-legs-tabs"'])
        self.off_and_on(reverse('passed_legislation') + '?status=personal', 'rf-legs',
                        ['js-receipt-token', 'js-copy-receipt', reverse('verify_vote_receipt')])

    def test_legislation_forms(self):
        self.off_and_on(reverse('edit_legislation', args=[self.pending_bill.id]), 'rf-leg-edit',
                        ['enctype="multipart/form-data"', 'name="title"', 'name="description"',
                         reverse('delete_chapter_legislation', args=[self.pending_bill.id])])
        self.off_and_on(reverse('edit_legislation_draft', args=[self.draft.id]), 'rf-leg-draft',
                        ['name="title"', reverse('publish_legislation_draft', args=[self.draft.id]),
                         reverse('view_legislation_history') + '?tab=drafts'])
        self.off_and_on(reverse('verify_vote_receipt'), 'rf-receipt',
                        ['name="receipt"', 'id="receipt-back-btn"', 'Verify Receipt', 'csrfmiddlewaretoken'])

    def test_assign_appointment(self):
        from src.models import Role
        role = Role.objects.create(name='Social Chair', code='SOCIAL_CHAIR_RPJ')
        bill = Legislation.objects.create(
            title='Appoint a Social Chair', description='Appointment.', posted_by=self.officer,
            available_at=timezone.now() - timedelta(days=3), status='passed', passed=True, voting_closed=True,
            legislation_type='appointment', appointment_role=role, appointment_member=self.member)
        self.off_and_on(reverse('assign_appointment', kwargs={'legislation_id': bill.id}), 'rf-leg-assign',
                        ['Appointment Vote Passed', 'Social Chair', 'Assign Role', reverse('passed_legislation')],
                        on_markers=[' rf-leg-assign-head"'])

    def test_vote_result(self):
        for on in (False, True):
            set_flag(on)
            bill = Legislation.objects.create(
                title=f'Adopt the Composite Policy {on}', description='Policy.', posted_by=self.officer,
                available_at=timezone.now() - timedelta(days=1), status='active', required_percentage='51')
            Vote.objects.create(user=self.member, legislation=bill, vote_choice='yes')
            html = self.client.post(reverse('end_vote', args=[bill.id])).content.decode()
            self.assertIn(reverse('vote'), html)
            self.assertIn('Vote Results', html)
            self.assertEqual('rf-sdl rf-vres"' in html, on)
            self.assertEqual(PAGE_CSS in html, on)

    def test_passed_resolutions(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from src.models.landing import PassedResolution
        from src.view.archive.passed_resolutions import passed_resolutions
        PassedResolution.objects.create(title='Resolution 2026-03', description='Dues schedule.',
                                        date_passed=timezone.localdate(), is_active=True)
        for on in (False, True):
            set_flag(on)
            request = RequestFactory().get('/')
            request.user = self.officer
            request.session = SessionStore()
            request._messages = FallbackStorage(request)
            html = passed_resolutions(request).content.decode()
            self.assertIn('Resolution 2026-03', html)
            self.assertIn(reverse('constitution_bylaws'), html)
            self.assertEqual('rf-sdl rf-res"' in html, on)
            self.assertEqual(' rf-res-hero"' in html, on)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    either adds classes (`{% if ui_refresh %} rf-…{% endif %}`) or puts an
    include in front of the unchanged old block, closed by `{# /rf #}`."""

    TEMPLATES = ['templates/service_hours/' + name + '.html' for name in (
        'create_service_event', 'dashboard', 'edit_period', 'edit_submission', 'form_builder', 'manage_expectations',
        'manage_periods', 'manage_submission', 'service_event_attendance', 'service_event_detail', 'service_events',
        'user_view_submission', 'view_submissions')] + [
        'templates/chapter_documents.html', 'templates/manage_chapter_document.html',
        'templates/manage_chapter_documents.html', 'templates/upload_chapter_document.html',
        'templates/view_document.html', 'templates/src/legislation_detail.html', 'templates/edit_legislation.html',
        'templates/assign_appointment.html', 'templates/legislation_draft_edit.html',
        'templates/passed_legislation.html', 'templates/passed_resolutions.html', 'templates/vote_result.html',
        'templates/vote_receipt_verify.html']
    NEW = ['templates/service_hours_refresh/_officer_progress.html', 'templates/service_hours_refresh/_officer_recent.html',
           'templates/service_hours_refresh/_officer_submissions.html', 'templates/service_hours_refresh/_events.html',
           'templates/service_hours_refresh/_periods.html', 'templates/documents_refresh/_doc_row.html',
           'templates/documents_refresh/_manage_row.html', 'static/css/refresh-pages/service-docs-legislation.css']

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
