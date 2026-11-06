"""
The website refresh (src/ui_refresh.py) on the officer tools, batch A:
Officer Home, the announcement pages (manage, create, edit, delete, confirm
email, stats, poll editor, poll results, poll QR), the event pages (manage,
create, edit, delete, all, archived, event time), attendance (event list,
mark attendance, QR check-in, dashboard, a member's detail) and Review
Excuses.

What must hold:
* off: the old markup (no page stylesheet, switch classes or app rows);
* on: the app-style markup and the page stylesheet, with every link, form,
  field, permission and script hook the pages had;
* reviewing an excuse and toggling an announcement with the refresh on still
  work;
* the templates' switches only wrap unchanged lines (strip them and no
  `ui_refresh` or `rf-` is left), and the new includes carry no inline
  script or style (CSP).
"""
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Announcement, Attendance, AttendanceExcuse, ContactSubmission, Event, ParliamentUser
from src.models.announcements import AnnouncementPoll, AnnouncementPollOption, AnnouncementPollQuestion
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/officer-a.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active',
                    email=f'{uid}@example.com')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-f-12345!')
    user.save()
    return user


def set_flag(name, on):
    FeatureFlag.objects.update_or_create(
        name=name, defaults={'display_name': name, 'description': 'test', 'category': 'ui', 'is_enabled': on})
    cache.clear()


class RefreshPagesFTests(TestCase):
    def setUp(self):
        cache.clear()
        self.officer = make_user('rpf-1', name='Avery Officerton', member_type='Officer', is_admin=True)
        self.member = make_user('rpf-2', name='Casey Attendwell')
        now = timezone.now()
        self.past = Event.objects.create(title='Chapter Meeting Past', date_time=now - timedelta(days=3),
                                         location='Chapter Room', created_by=self.officer, is_active=True,
                                         requires_attendance=True, allow_excuses=True)
        Attendance.objects.create(user=self.member, event=self.past, attendance_type='event', status='present')
        self.future = Event.objects.create(title='Founders Day Dinner', date_time=now + timedelta(days=5),
                                           location='Chapter House', created_by=self.officer, is_active=True,
                                           requires_attendance=True, allow_excuses=True,
                                           description='Business casual.')
        self.signup = Event.objects.create(title='Philanthropy 5K', date_time=now + timedelta(days=9),
                                           created_by=self.officer, is_active=True, requires_signup=True,
                                           max_signups=40)
        self.series = Event.objects.create(title='Weekly Study Hall', date_time=now + timedelta(days=2),
                                           created_by=self.officer, is_active=True, is_recurring=True,
                                           recurrence_type='weekly', checkin_min_minutes=60)
        self.instance = Event.objects.create(title='Weekly Study Hall', date_time=now + timedelta(days=9),
                                             created_by=self.officer, is_active=True, parent_event=self.series)
        self.archived = Event.objects.create(title='Spring Formal 2024', date_time=now - timedelta(days=500),
                                             created_by=self.officer, archived=True)
        self.excuse = AttendanceExcuse.objects.create(event=self.future, user=self.member, status='pending',
                                                      reason='Biology lab practical that afternoon.')
        self.ann = Announcement.objects.create(title='Rush week schedule posted', content='Sign up for two.',
                                               posted_by=self.officer, event_date=now + timedelta(days=4))
        self.poll_ann = Announcement.objects.create(title='Formal theme vote', content='Vote below.',
                                                    posted_by=self.officer)
        poll = AnnouncementPoll.objects.create(announcement=self.poll_ann, title='Formal theme', is_open=True,
                                               created_by=self.officer)
        question = AnnouncementPollQuestion.objects.create(poll=poll, text='Which theme?', question_type='single')
        AnnouncementPollOption.objects.create(question=question, text='Casino Night')
        ContactSubmission.objects.create(name='Pat Parent', email='pat@example.com', topic='Recruitment',
                                         message='How does rush work?')
        set_flag('qr_attendance_checkin', True)
        self.client = Client()
        self.client.force_login(self.officer)

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, page_class, hooks, off_markers=(), on_markers=()):
        """Off: no stylesheet, no page class, the old markers. On: all three
        flipped. Both: every hook."""
        set_flag('web_refresh', False)
        html = self.get(url)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn('rf-op', html)
        for marker in off_markers:
            self.assertIn(marker, html)
        for hook in hooks:
            self.assertIn(hook, html)
        set_flag('web_refresh', True)
        html = self.get(url)
        self.assertIn(PAGE_CSS, html)
        self.assertIn(f'rf-mp rf-op {page_class}"', html)
        for marker in off_markers:
            self.assertNotIn(marker, html)
        for marker in on_markers:
            self.assertIn(marker, html)
        for hook in hooks:
            self.assertIn(hook, html)
        set_flag('web_refresh', False)
        return html

    # Officer Home ----------------------------------------------------------------

    def test_officer_home(self):
        hooks = [reverse('create_announcement'), reverse('create_event'), reverse('manage_announcements'),
                 reverse('chapter_minutes_list'), reverse('committee_minutes_list', args=['EXEC']),
                 reverse('slating_dashboard'), reverse('view_all_reports'), reverse('upload_report'),
                 reverse('view_all_events'), reverse('calendar'), reverse('view_all_activity'),
                 reverse('contact_submissions'), reverse('manage_events'), reverse('event_attendance_list'),
                 reverse('user_list'), reverse('chapter_stats'), reverse('officer_mobile_app'),
                 reverse('manage_roles'), reverse('role_transitions'), reverse('manage_committees'),
                 reverse('edit_landing_page'), reverse('manage_resolutions'), reverse('admin_v2_dashboard'),
                 'Officer Portal', 'Founders Day Dinner', 'Pat Parent', 'Quick Links']
        self.off_and_on(reverse('officer_home'), 'rf-ohome', hooks,
                        off_markers=['<!-- ── CREATE ACTIONS', '<!-- ── QUICK LINKS'],
                        on_markers=['class="rf-op-hero"', 'rf-tile is-orange', 'rf-row rf-op-contact is-unread',
                                    'class="rf-pill"'])

    def test_officer_home_keeps_its_permission_gates(self):
        advisor = make_user('rpf-3', member_type='Advisor')
        self.client.force_login(advisor)
        set_flag('web_refresh', True)
        response = self.client.get(reverse('officer_home'))
        if response.status_code != 200:  # advisors may be gated by a page toggle
            return
        html = response.content.decode()
        self.assertNotIn(reverse('admin_v2_dashboard'), html.split('rf-ohome', 1)[1])
        self.assertNotIn(reverse('manage_roles') + '" class="rf-pill"', html)

    # Announcements ----------------------------------------------------------------

    def test_manage_announcements(self):
        hooks = [reverse('edit_announcement', args=[self.ann.id]), reverse('announcement_stats', args=[self.ann.id]),
                 reverse('delete_announcement', args=[self.ann.id]),
                 reverse('toggle_announcement_status', args=[self.ann.id]),
                 reverse('create_or_edit_poll', args=[self.ann.id]), reverse('poll_results', args=[self.poll_ann.id]),
                 reverse('create_announcement'), 'js-toggle-announcement', f'data-id="{self.ann.id}"',
                 'data-active="true"', 'announcement-content-wrapper', 'announcement-content', 'expand-btn hidden',
                 'line-clamp-4', 'Deactivate', 'Rush week schedule posted', 'View Details']
        self.off_and_on(reverse('manage_announcements'), 'rf-oann', hooks,
                        off_markers=['<!-- View Stats Summary -->'],
                        on_markers=['rf-card rf-oann-card', '<span class="rf-chip is-success">Active</span>',
                                    '<span class="rf-chip is-accent">Event</span>'])

    def test_toggling_an_announcement_with_the_refresh_on_works(self):
        set_flag('web_refresh', True)
        self.assertIn('rf-oann-card', self.get(reverse('manage_announcements')))
        response = self.client.post(reverse('toggle_announcement_status', args=[self.ann.id]),
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.ann.refresh_from_db()
        self.assertFalse(self.ann.is_active)

    def test_announcement_forms_and_pages(self):
        self.off_and_on(reverse('create_announcement'), 'rf-oann-form',
                        ['name="title"', 'name="content"', 'name="publish_at"', 'name="visible_to"',
                         'name="send_email"', 'id="poll-toggle-btn"', 'id="inactive-toggle-btn"', 'id="submit-btn"',
                         'csrfmiddlewaretoken', reverse('manage_announcements')])
        self.off_and_on(reverse('edit_announcement', args=[self.ann.id]), 'rf-oann-form',
                        ['name="title"', 'name="content"', 'csrfmiddlewaretoken', 'Rush week schedule posted'])
        self.off_and_on(reverse('delete_announcement', args=[self.ann.id]), 'rf-oconfirm',
                        ['csrfmiddlewaretoken', reverse('manage_announcements'), 'Rush week schedule posted'])
        self.off_and_on(reverse('confirm_announcement_email', args=[self.ann.id]), 'rf-oemail',
                        [reverse('send_announcement_emails', args=[self.ann.id]), 'id="send-email-form"',
                         'id="js-confirm-skip"', 'id="skipModal"'],
                        on_markers=[' rf-op-banner"', ' rf-op-stats"'])
        self.off_and_on(reverse('announcement_stats', args=[self.ann.id]), 'rf-oann-views',
                        ['View Statistics', reverse('manage_announcements')], on_markers=[' rf-op-stats"'])

    def test_poll_pages(self):
        self.off_and_on(reverse('create_or_edit_poll', args=[self.poll_ann.id]), 'rf-opoll-form',
                        ['id="poll-form"', 'name="poll_title"', 'name="question_text_0"', 'js-confirm-submit',
                         'csrfmiddlewaretoken'])
        self.off_and_on(reverse('poll_results', args=[self.poll_ann.id]), 'rf-opoll-results',
                        ['Formal theme', 'Which theme?', reverse('manage_poll_qr', args=[self.poll_ann.id])])
        self.off_and_on(reverse('manage_poll_qr', args=[self.poll_ann.id]), 'rf-oqr',
                        [reverse('poll_results', args=[self.poll_ann.id])])

    # Events ------------------------------------------------------------------------

    def test_manage_events(self):
        hooks = [reverse('mark_event_attendance', args=[self.future.id]), reverse('edit_event', args=[self.future.id]),
                 reverse('delete_event', args=[self.future.id]), reverse('archive_event', args=[self.future.id]),
                 reverse('event_signup_list', args=[self.signup.id]), reverse('view_archived_events'),
                 reverse('create_event'), 'js-archive-event', f'data-event-id="{self.future.id}"',
                 f'id="event-card-{self.future.id}"', '?filter=upcoming', '?filter=series', '?filter=instances',
                 f'?series={self.series.id}', 'Sign-Ups (0/40)', 'Founders Day Dinner']
        self.off_and_on(reverse('manage_events'), 'rf-oev', hooks,
                        off_markers=['<!-- Desktop Table View (hidden on mobile) -->',
                                     f'id="event-row-{self.future.id}"'],
                        on_markers=['rf-row rf-row-group rf-oev-row', ' rf-op-tabs"',
                                    '<span class="rf-chip is-success">Upcoming</span>'])

    def test_event_forms(self):
        set_flag('location_checkin', True)
        form_hooks = ['name="title"', 'name="description"', 'name="date_time"', 'name="location"',
                      'csrfmiddlewaretoken', 'data-checkin-area', 'data-checkin-search', 'data-checkin-address',
                      'data-checkin-find', 'data-checkin-slider', 'data-checkin-map', 'data-checkin-off',
                      'data-checkin-here', 'id="checkin-places"', 'js/officer/checkin_area_map.js']
        self.off_and_on(reverse('create_event'), 'rf-oev-form', form_hooks)
        self.off_and_on(reverse('edit_event', args=[self.instance.id]), 'rf-oev-form',
                        form_hooks + ['name="scope"', 'value="following"'])
        self.off_and_on(reverse('delete_event', args=[self.future.id]), 'rf-oconfirm',
                        ['csrfmiddlewaretoken', 'Founders Day Dinner'])

    def test_all_and_archived_events(self):
        self.off_and_on(reverse('view_all_events'), 'rf-oallev',
                        [reverse('edit_event', args=[self.future.id]), reverse('delete_event', args=[self.past.id]),
                         reverse('create_event'), reverse('officer_home'), 'Founders Day Dinner',
                         'Chapter Meeting Past'],
                        off_markers=['grid gap-4 md:grid-cols-2 lg:grid-cols-3'],
                        on_markers=['rf-card rf-oev-list', 'Upcoming Events (', 'Past Events ('])
        self.off_and_on(reverse('view_archived_events'), 'rf-oarch',
                        [reverse('unarchive_event', args=[self.archived.id]), 'js-unarchive-event',
                         f'data-event-id="{self.archived.id}"', f'id="archived-event-{self.archived.id}"',
                         'Spring Formal 2024', 'Total Archived'],
                        on_markers=['class="rf-op-tile"', 'rf-row rf-row-group rf-oev-row'])

    def test_event_time(self):
        self.off_and_on(reverse('event_time', args=[self.series.id]), 'rf-oevtime',
                        ['name="user_id"', 'name="minutes"', 'name="reason"', 'csrfmiddlewaretoken'],
                        on_markers=[' rf-op-banner"'])

    # Attendance ----------------------------------------------------------------------

    def test_attendance_pages(self):
        self.off_and_on(reverse('event_attendance_list'), 'rf-oatt-list',
                        [reverse('attendance_dashboard'), reverse('review_excuses'),
                         reverse('mark_event_attendance', args=[self.future.id])])
        self.off_and_on(reverse('mark_event_attendance', args=[self.past.id]), 'rf-oatt-mark',
                        ['csrfmiddlewaretoken', reverse('manage_qr_checkin', args=[self.past.id]),
                         'Casey Attendwell'], on_markers=[' rf-op-stats"'])
        self.off_and_on(reverse('manage_qr_checkin', args=[self.future.id]), 'rf-oqr',
                        [reverse('mark_event_attendance', args=[self.future.id])])
        self.off_and_on(reverse('attendance_dashboard'), 'rf-oatt-dash',
                        ['id="rangeFilter"', 'id="js-print-btn"', 'id="at-risk-section"', 'function updateRange(',
                         reverse('review_excuses'), reverse('officer_member_attendance', args=[self.member.user_id])],
                        on_markers=[' rf-op-stats"', ' rf-op-quick"'])
        self.off_and_on(reverse('officer_member_attendance', args=[self.member.user_id]), 'rf-oatt-member',
                        ['Casey Attendwell', 'Chapter Meeting Past'], on_markers=[' rf-op-stats"'])

    # Review Excuses ----------------------------------------------------------------------

    def test_review_excuses(self):
        hooks = ['js-quick-action', f'data-excuse-id="{self.excuse.id}"', 'data-action="approve"',
                 'data-action="deny"', 'data-user-name="Casey Attendwell"', f'id="excuse-{self.excuse.id}"',
                 'id="showArchivedToggle"', 'id="actionModal"', 'id="actionForm"', 'name="excuse_id"',
                 'name="notes"', 'id="confirmBtn"', '?status=pending', 'Biology lab practical that afternoon.',
                 'Founders Day Dinner', 'function quickAction(']
        self.off_and_on(reverse('review_excuses'), 'rf-oexc', hooks,
                        off_markers=['<!-- Member Info -->'],
                        on_markers=['rf-card is-padded rf-oexc-card', '<span class="rf-chip is-warning">1 pending</span>',
                                    ' rf-op-filterbar"', ' rf-op-modal"'])

    def test_approving_an_excuse_with_the_refresh_on_works(self):
        set_flag('web_refresh', True)
        self.assertIn('rf-oexc-card', self.get(reverse('review_excuses')))
        response = self.client.post(reverse('review_excuses'),
                                    {'excuse_id': self.excuse.id, 'action': 'approve', 'notes': 'Good luck.'})
        self.assertIn(response.status_code, (200, 302))
        self.excuse.refresh_from_db()
        self.assertEqual(self.excuse.status, 'approved')


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    either adds classes (`{% if ui_refresh %} rf-…{% endif %}`) or puts an
    include in front of the unchanged old block, closed by `{# /rf #}`."""

    TEMPLATES = ['templates/officer_home.html'] + [f'templates/officer/{n}.html' for n in (
        'manage_announcements', 'create_announcement', 'edit_announcement', 'delete_announcement',
        'confirm_announcement_email', 'announcement_stats', 'announcement_poll_edit', 'announcement_poll_results',
        'manage_poll_qr', 'manage_events', 'create_event', 'edit_event', 'delete_event', 'view_all_events',
        'view_archived_events', 'event_time', 'event_attendance_list', 'mark_event_attendance',
        'manage_qr_checkin', 'attendance_dashboard', 'member_attendance_detail', 'review_excuses')]
    NEW = [f'templates/officer_refresh/{n}.html' for n in (
        '_home_top', '_home_lists', '_announcements', '_events', '_all_events', '_archived_events', '_excuses')] + [
        'static/css/refresh-pages/officer-a.css']

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
