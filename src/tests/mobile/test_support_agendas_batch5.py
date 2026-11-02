"""
v4.5.0 — app screens that used to open the website:

* the bug tracker, a bug report's detail page, the bug admin page and its
  status/notes update (src/mobile/views_support_history.py);
* a feedback submission's detail page, the feedback admin page and its update;
* an announcement's view stats (src/mobile/views_officer.py);
* a role knowledge base page's history and its edit (src/mobile/views_role_kb.py).

Each must show what its web page shows, to the same people, and change what
the web's form changes, through the same helpers.

    python manage.py test src.tests.mobile.test_support_agendas_batch5
"""
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import (
    ActivityLog, Announcement, BugReport, FeedbackRequest, Role, RoleKnowledgeBase, RoleKnowledgeBaseRevision,
    UserAnnouncementView,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'

# A 1×1 PNG.
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82')


class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.member = make_user('b5-mem', name='Mona Member')
        self.other = make_user('b5-oth', name='Otto Other')
        self.admin = make_user('73', name='Ada Admin', member_type='Officer')
        _, self.member_access, _ = make_device(self.member)
        _, self.other_access, _ = make_device(self.other)
        _, self.admin_access, _ = make_device(self.admin)
        self.api = Client()

    def get(self, path, access, **params):
        return self.api.get(API + path, params, **bearer(access))

    def post(self, path, access, body):
        return self.api.post(API + path, body, content_type='application/json', **bearer(access))

    def put(self, path, access, body):
        return self.api.put(API + path, body, content_type='application/json', **bearer(access))


# ── Bug reports ─────────────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class BugTrackerTests(_Base):

    def setUp(self):
        super().setUp()
        self.new = BugReport.objects.create(submitted_by=self.member, description='Login loops', issue_type='ui',
                                            priority='high', admin_notes='secret note', browser_info='Safari 17',
                                            device_type='mobile', browser='Safari', operating_system='iOS',
                                            steps_to_reproduce='Tap sign in', page_url='/login/')
        self.wip = BugReport.objects.create(submitted_by=self.other, description='Slow page', status='in_progress')
        self.done = BugReport.objects.create(submitted_by=self.other, description='Fixed thing', status='resolved',
                                             device_type='desktop')

    def test_tracker_lists_every_report_in_the_pages_order_with_counts(self):
        r = self.get('support/bug-tracker/', self.other_access)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([x['id'] for x in body['reports']], [self.wip.id, self.new.id, self.done.id])
        self.assertEqual(body['counts'], {'total': 3, 'new': 1, 'in_progress': 1, 'resolved': 1})
        self.assertFalse(body['can_manage'])
        self.assertIsNone(body['links']['admin'])
        self.assertNotIn('secret note', str(body))
        self.assertNotIn('Safari 17', str(body))   # the tracker table doesn't show it

    def test_tracker_rows_match_the_web_page(self):
        web = Client()
        web.force_login(self.member)
        ctx = web.get(reverse('bug_tracker'), {'status': 'new'}).context
        body = self.get('support/bug-tracker/', self.member_access, status='new').json()
        self.assertEqual([x['id'] for x in body['reports']], [b.id for b in ctx['bug_reports']])
        self.assertEqual(body['counts']['total'], ctx['total_count'])

    def test_tracker_filters_and_ignores_unknown_values(self):
        body = self.get('support/bug-tracker/', self.member_access, status='resolved').json()
        self.assertEqual([x['id'] for x in body['reports']], [self.done.id])
        body = self.get('support/bug-tracker/', self.member_access, status='bogus', type='nope').json()
        self.assertEqual((body['status'], body['type'], len(body['reports'])), ('', '', 3))

    def test_detail_shows_what_the_page_shows_admin_notes_to_the_admin_only(self):
        body = self.get(f'support/bug-tracker/{self.new.id}/', self.other_access).json()
        rep = body['report']
        self.assertEqual((rep['description'], rep['steps_to_reproduce'], rep['browser_info'], rep['page_url']),
                         ('Login loops', 'Tap sign in', 'Safari 17', '/login/'))
        self.assertIsNone(rep['admin_notes'])
        self.assertFalse(body['can_manage'])
        admin = self.get(f'support/bug-tracker/{self.new.id}/', self.admin_access).json()
        self.assertEqual(admin['report']['admin_notes'], 'secret note')
        self.assertTrue(admin['can_manage'])

    def test_unknown_report_is_404(self):
        r = self.get('support/bug-tracker/999999/', self.member_access)
        self.assertEqual((r.status_code, r.json()['error']), (404, 'not_found'))

    def test_admin_page_is_the_admins_only(self):
        r = self.get('support/bug-tracker/admin/', self.member_access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        body = self.get('support/bug-tracker/admin/', self.admin_access, device_type='desktop').json()
        self.assertEqual([x['id'] for x in body['reports']], [self.done.id])
        self.assertEqual([c['value'] for c in body['device_type_choices']], ['desktop', 'mobile'])
        body = self.get('support/bug-tracker/admin/', self.admin_access).json()
        self.assertIn('secret note', [x['admin_notes'] for x in body['reports']])

    def test_update_is_the_admins_only_and_changes_nothing_otherwise(self):
        r = self.post(f'support/bug-tracker/{self.new.id}/update/', self.member_access,
                      {'status': 'resolved', 'admin_notes': 'hi'})
        self.assertEqual(r.status_code, 403)
        self.new.refresh_from_db()
        self.assertEqual((self.new.status, self.new.admin_notes), ('new', 'secret note'))

    def test_update_resolves_and_unresolves_as_the_web_does(self):
        r = self.post(f'support/bug-tracker/{self.new.id}/update/', self.admin_access,
                      {'status': 'resolved', 'admin_notes': 'shipped'})
        self.assertEqual(r.status_code, 200, r.content)
        self.new.refresh_from_db()
        self.assertEqual((self.new.status, self.new.admin_notes, self.new.resolved_by), ('resolved', 'shipped', self.admin))
        self.assertIsNotNone(self.new.resolved_at)
        self.assertEqual(r.json()['report']['resolved_by'], 'Ada Admin')
        # Blank notes keep the notes; an unknown status keeps the status.
        self.post(f'support/bug-tracker/{self.new.id}/update/', self.admin_access, {'status': 'bogus', 'admin_notes': ''})
        self.new.refresh_from_db()
        self.assertEqual((self.new.status, self.new.admin_notes), ('resolved', 'shipped'))
        self.post(f'support/bug-tracker/{self.new.id}/update/', self.admin_access, {'status': 'acknowledged'})
        self.new.refresh_from_db()
        self.assertEqual(self.new.status, 'acknowledged')
        self.assertIsNone(self.new.resolved_at)
        self.assertIsNone(self.new.resolved_by)

    def test_web_update_still_works(self):
        web = Client()
        web.force_login(self.admin)
        web.post(reverse('bug_admin_update', args=[self.wip.id]), {'status': 'resolved', 'admin_notes': 'done'})
        self.wip.refresh_from_db()
        self.assertEqual((self.wip.status, self.wip.resolved_by), ('resolved', self.admin))

    def test_no_toggle_but_pledge_restriction(self):
        toggle('bug_tracker', False)
        self.assertEqual(self.get('support/bug-tracker/', self.member_access).status_code, 200)
        restrict('bug_tracker', [])
        restrict('bug_report_detail', [])
        pledge = make_user('b5-pl', member_type='Pledge')
        _, access, _ = make_device(pledge)
        cache.clear()
        for path in ('support/bug-tracker/', f'support/bug-tracker/{self.new.id}/'):
            r = self.get(path, access)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'support/bug-tracker/').status_code, 401)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class BugScreenshotTests(_Base):

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.report = BugReport.objects.create(
            submitted_by=self.member, description='With a picture',
            screenshot=SimpleUploadedFile('shot.png', PNG, content_type='image/png'))

    def test_reporter_and_admin_get_the_screenshot_others_dont(self):
        body = self.get(f'support/bug-tracker/{self.report.id}/', self.member_access).json()
        self.assertEqual(body['report']['screenshot'], f'support/bug-tracker/{self.report.id}/screenshot/')
        self.assertIsNone(self.get(f'support/bug-tracker/{self.report.id}/', self.other_access).json()['report']['screenshot'])
        for access, code in ((self.member_access, 200), (self.admin_access, 200), (self.other_access, 404)):
            r = self.get(f'support/bug-tracker/{self.report.id}/screenshot/', access)
            self.assertEqual(r.status_code, code)
            if code == 200:
                self.assertEqual(b''.join(r.streaming_content), PNG)
                self.assertEqual(r['Content-Type'], 'image/png')


# ── Feedback ────────────────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class FeedbackDetailAdminTests(_Base):

    def setUp(self):
        super().setUp()
        self.idea = FeedbackRequest.objects.create(submitted_by=self.member, title='Dark mode', description='Please',
                                                   request_type='feature_idea', admin_notes='maybe later')
        self.ticket = FeedbackRequest.objects.create(submitted_by=self.member, title='Locked out', description='Help',
                                                     request_type='support_ticket', priority='urgent')

    def test_an_idea_is_anyones_a_ticket_its_submitters_and_the_admins(self):
        self.assertEqual(self.get(f'support/feedback/{self.idea.id}/', self.other_access).status_code, 200)
        r = self.get(f'support/feedback/{self.ticket.id}/', self.other_access)
        self.assertEqual((r.status_code, r.json()['error']), (404, 'not_found'))
        mine = self.get(f'support/feedback/{self.ticket.id}/', self.member_access).json()
        self.assertTrue(mine['is_owner'])
        self.assertEqual(mine['submission']['title'], 'Locked out')
        self.assertEqual(self.get(f'support/feedback/{self.ticket.id}/', self.admin_access).status_code, 200)
        # The web page agrees.
        web = Client()
        web.force_login(self.other)
        self.assertEqual(web.get(reverse('feedback_request_detail', args=[self.ticket.id])).status_code, 404)

    def test_admin_notes_to_the_admin_only(self):
        self.assertIsNone(self.get(f'support/feedback/{self.idea.id}/', self.member_access).json()['submission']['admin_notes'])
        body = self.get(f'support/feedback/{self.idea.id}/', self.admin_access).json()
        self.assertEqual((body['submission']['admin_notes'], body['can_manage']), ('maybe later', True))

    def test_admin_page_lists_both_kinds_with_counts(self):
        self.assertEqual(self.get('support/feedback/admin/', self.member_access).status_code, 403)
        body = self.get('support/feedback/admin/', self.admin_access).json()
        self.assertEqual({x['id'] for x in body['submissions']}, {self.idea.id, self.ticket.id})
        self.assertEqual((body['counts']['ideas'], body['counts']['tickets'], body['counts']['total']), (1, 1, 2))
        body = self.get('support/feedback/admin/', self.admin_access, type='support_ticket').json()
        self.assertEqual([x['id'] for x in body['submissions']], [self.ticket.id])
        web = Client()
        web.force_login(self.admin)
        ctx = web.get(reverse('feedback_admin')).context
        self.assertEqual([x['id'] for x in self.get('support/feedback/admin/', self.admin_access).json()['submissions']],
                         [f.id for f in ctx['feedback_requests']])

    def test_update(self):
        self.assertEqual(self.post(f'support/feedback/{self.ticket.id}/update/', self.member_access,
                                   {'status': 'resolved'}).status_code, 403)
        r = self.post(f'support/feedback/{self.ticket.id}/update/', self.admin_access,
                      {'status': 'resolved', 'admin_notes': 'reset his password'})
        self.assertEqual(r.status_code, 200, r.content)
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.admin_notes, self.ticket.resolved_by),
                         ('resolved', 'reset his password', self.admin))
        self.assertEqual(self.post('support/feedback/999999/update/', self.admin_access, {}).status_code, 404)

    def test_attachment_follows_the_detail_rule(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media):
            self.ticket.attachment = SimpleUploadedFile('note.txt', b'hello', content_type='text/plain')
            self.ticket.save()
            path = f'support/feedback/{self.ticket.id}/attachment/'
            self.assertEqual(self.get(f'support/feedback/{self.ticket.id}/', self.member_access)
                             .json()['submission']['attachment'], path)
            self.assertEqual(self.get(path, self.other_access).status_code, 404)
            r = self.get(path, self.member_access)
            self.assertEqual((r.status_code, b''.join(r.streaming_content)), (200, b'hello'))


# ── Announcement stats ──────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AnnouncementStatsTests(_Base):

    def setUp(self):
        super().setUp()
        self.officer = make_user('b5-off', name='Olive Officer', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        self.a = Announcement.objects.create(title='Chapter Sunday', content='Be there', posted_by=self.officer)

    def test_officers_only(self):
        r = self.get(f'officer/announcements/{self.a.id}/stats/', self.member_access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertEqual(self.get('officer/announcements/999999/stats/', self.officer_access).status_code, 404)

    def test_same_figures_and_names_as_the_web_page(self):
        self.a.ensure_target_audience_snapshot()
        UserAnnouncementView.objects.create(user=self.member, announcement=self.a, view_source='email')
        UserAnnouncementView.objects.create(user=self.other, announcement=self.a, view_source='site')
        body = self.get(f'officer/announcements/{self.a.id}/stats/', self.officer_access).json()
        web = Client()
        web.force_login(self.officer)
        ctx = web.get(reverse('announcement_stats', args=[self.a.id])).context
        stats = ctx['stats']
        self.assertEqual((body['stats']['site_views'], body['stats']['email_views'], body['stats']['total_views'],
                          body['stats']['target_audience']),
                         (stats['site_views'], stats['email_views'], stats['total_views'], stats['target_audience']))
        self.assertEqual(body['stats']['email_views'], 1)
        self.assertEqual({v['user_id'] for v in body['viewers']}, {v.user.user_id for v in ctx['viewers']})
        self.assertEqual({v['source'] for v in body['viewers']}, {'site', 'email'})
        self.assertEqual({u['user_id'] for u in body['non_viewers']}, {u.user_id for u in ctx['non_viewers']})
        self.assertNotIn(self.member.user_id, {u['user_id'] for u in body['non_viewers']})
        self.assertEqual(body['announcement']['id'], self.a.id)


# ── Role knowledge base ─────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class RoleKbHistoryEditTests(_Base):

    def setUp(self):
        super().setUp()
        self.role = Role.objects.create(name='Zz Historian', code='ZZH')
        self.empty = Role.objects.create(name='Zz Empty', code='ZZE')
        self.officer = make_user('b5-rko', name='Rita Officer', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        kb = RoleKnowledgeBase.objects.create(role=self.role)
        self.first = RoleKnowledgeBaseRevision.objects.create(knowledge_base=kb, content='v1', edited_by=self.officer)
        self.second = RoleKnowledgeBaseRevision.objects.create(knowledge_base=kb, content='v2\n\nmore', edited_by=None)

    def test_history_newest_first_for_any_member(self):
        body = self.get(f'role-knowledge-base/{self.role.id}/history/', self.member_access).json()
        self.assertEqual([r['id'] for r in body['revisions']], [self.second.id, self.first.id])
        self.assertEqual(body['revisions'][0]['paragraphs'], ['v2', 'more'])
        self.assertIsNone(body['revisions'][0]['edited_by'])
        self.assertEqual(body['revisions'][1]['edited_by'], 'Rita Officer')
        web = Client()
        web.force_login(self.member)
        ctx = web.get(reverse('role_knowledge_base_history', args=[self.role.id])).context
        self.assertEqual([r['id'] for r in body['revisions']], [r.id for r in ctx['revisions']])

    def test_history_of_an_empty_page_creates_no_row(self):
        body = self.get(f'role-knowledge-base/{self.empty.id}/history/', self.member_access).json()
        self.assertEqual(body['revisions'], [])
        self.assertFalse(RoleKnowledgeBase.objects.filter(role=self.empty).exists())
        self.assertEqual(self.get('role-knowledge-base/999999/history/', self.member_access).status_code, 404)

    def test_members_cannot_edit(self):
        r = self.put(f'role-knowledge-base/{self.role.id}/', self.member_access, {'content': 'vandalism'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertEqual(RoleKnowledgeBaseRevision.objects.filter(content='vandalism').count(), 0)

    def test_officer_edit_adds_a_revision_and_logs_it(self):
        r = self.put(f'role-knowledge-base/{self.empty.id}/', self.officer_access, {'content': '# Notes\n\n- one'})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['current']['content'], body['revision_count'], body['current']['edited_by']),
                         ('# Notes\n\n- one', 1, 'Rita Officer'))
        self.assertTrue(RoleKnowledgeBase.objects.filter(role=self.empty).exists())
        self.assertTrue(ActivityLog.objects.filter(user=self.officer,
                                                   metadata__action='edit_role_knowledge_base').exists())
        self.put(f'role-knowledge-base/{self.role.id}/', self.officer_access, {'content': 'v3'})
        self.assertEqual(RoleKnowledgeBaseRevision.objects.filter(knowledge_base__role=self.role).count(), 3)

    def test_edit_validation(self):
        for bad in ({}, {'content': 5}, {'content': None}):
            r = self.put(f'role-knowledge-base/{self.role.id}/', self.officer_access, bad)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertEqual(self.put('role-knowledge-base/999999/', self.officer_access, {'content': 'x'}).status_code, 404)

    def test_web_edit_still_works(self):
        web = Client()
        web.force_login(self.officer)
        web.post(reverse('edit_role_knowledge_base', args=[self.role.id]), {'content': 'from the web'})
        self.assertEqual(self.role.knowledge_base.revisions.first().content, 'from the web')

    def test_edit_honours_the_edit_pages_pledge_restriction(self):
        restrict('edit_role_knowledge_base', [])
        pledge_officer = make_user('b5-plo', member_type='Pledge')
        _, access, _ = make_device(pledge_officer)
        cache.clear()
        r = self.put(f'role-knowledge-base/{self.role.id}/', access, {'content': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
