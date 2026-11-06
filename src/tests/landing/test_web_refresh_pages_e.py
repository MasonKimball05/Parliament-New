"""
The website refresh (src/ui_refresh.py) on the member's own tools and the
reading pages: My Attendance, Excuses (and the submit form), Kai (dashboard,
a report, submit), My Tasks, Polls, Notifications, Quote Book (and its add
form), Search, the Guide (index and an article), Constitution & Bylaws and
Robert's Rules.

What must hold:
* off: the old markup (no page stylesheet, switch classes or app rows);
* on: the app-style markup and the page stylesheet, with every link, form,
  field, permission and script hook the pages had;
* cancelling an excuse with the refresh on still works;
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

from src.models import Announcement, Attendance, AttendanceExcuse, Event, ParliamentUser
from src.models.announcements import AnnouncementPoll
from src.models.education import PledgeTask
from src.models.kai import KaiReport
from src.models.notifications import Notification
from src.models.quote_book import Quote
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/member-tools.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-e-12345!')
    user.save()
    return user


class RefreshPagesETests(TestCase):
    def setUp(self):
        cache.clear()
        self.member = make_user('rpe-1', name='Casey Attendwell')
        self.other = make_user('rpe-2', name='Morgan Officerly')
        now = timezone.now()
        self.past = Event.objects.create(title='Chapter Meeting Past', date_time=now - timedelta(days=3),
                                         location='Chapter Room', created_by=self.other, is_active=True,
                                         requires_attendance=True, allow_excuses=True)
        Attendance.objects.create(user=self.member, event=self.past, attendance_type='event', status='late')
        self.future = Event.objects.create(title='Founders Day Dinner', date_time=now + timedelta(days=5),
                                           location='Chapter House', created_by=self.other, is_active=True,
                                           requires_attendance=True, allow_excuses=True)
        self.open_event = Event.objects.create(title='Alumni Weekend', date_time=now + timedelta(days=8),
                                               location='Chapter House', created_by=self.other, is_active=True,
                                               requires_attendance=False, allow_excuses=True)
        self.excuse = AttendanceExcuse.objects.create(event=self.future, user=self.member, status='pending',
                                                      reason='Biology lab practical that afternoon.')
        self.report = KaiReport.objects.create(title='Noise after quiet hours', submitted_by=self.member,
                                               category='behavioral', description='Details.', status='pending',
                                               targeted_to=self.other)
        self.named = KaiReport.objects.create(title='Late dues payment', submitted_by=self.other,
                                              category='financial', description='Details.', status='reviewed',
                                              targeted_to=self.member, deliberation_outcome='heard')
        ann = Announcement.objects.create(title='Formal theme announcement', content='Vote below.',
                                          posted_by=self.other)
        self.poll_ann = ann
        AnnouncementPoll.objects.create(announcement=ann, title='Formal theme vote', is_open=True,
                                        is_anonymous=True, closes_at=now + timedelta(days=2), created_by=self.other)
        closed = Announcement.objects.create(title='Shirt announcement', content='Done.', posted_by=self.other)
        AnnouncementPoll.objects.create(announcement=closed, title='Shirt color', is_open=False,
                                        created_by=self.other)
        self.unread = Notification.objects.create(recipient=self.member, notification_type='announcement',
                                                  title='New announcement: Rush week', message='Schedule is up.',
                                                  link=reverse('announcements'))
        self.read = Notification.objects.create(recipient=self.member, notification_type='security',
                                                title='New sign-in from Safari', message='Was this you?',
                                                is_read=True)
        Quote.objects.create(quoted_member=self.other, text='I am not lost, I am exploring.', submitted_by=self.member)
        self.client = Client()
        self.client.force_login(self.member)

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

    def assert_hooks(self, html, hooks):
        for hook in hooks:
            self.assertIn(hook, html)

    def off_and_on(self, url, page_class, hooks, off_markers=(), on_markers=()):
        """Off: no stylesheet, no page class, the old markers. On: all three
        flipped. Both: every hook."""
        html = self.get(url)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn('rf-mp', html)
        for marker in off_markers:
            self.assertIn(marker, html)
        self.assert_hooks(html, hooks)
        self.set_flag(True)
        html = self.get(url)
        self.assertIn(PAGE_CSS, html)
        self.assertIn(f'rf-mp {page_class}"', html)
        for marker in off_markers:
            self.assertNotIn(marker, html)
        for marker in on_markers:
            self.assertIn(marker, html)
        self.assert_hooks(html, hooks)
        self.set_flag(False)
        return html

    # My Attendance ---------------------------------------------------------------

    def test_attendance(self):
        self.off_and_on(
            reverse('my_attendance'), 'rf-att',
            ['id="rangeFilter"', 'value="365"', reverse('my_excuses'), 'Chapter Meeting Past',
             'function updateRange(', 'Attendance Tips', 'Manage Excuses'],
            off_markers=['<!-- Attendance History -->'],
            on_markers=['class="rf-card rf-att-list"', '<span class="rf-chip is-warning">Late</span>',
                        ' rf-att-stats"', ' rf-att-tips"'])

    # Excuses ------------------------------------------------------------------------

    def excuse_hooks(self):
        return [reverse('submit_excuse', args=[self.open_event.id]), reverse('cancel_excuse', args=[self.excuse.id]),
                'class="js-confirm-submit"', 'data-confirm-msg="Are you sure you want to cancel this excuse request?"',
                'csrfmiddlewaretoken', reverse('my_attendance'), 'Biology lab practical that afternoon.',
                'Alumni Weekend', 'Optional Attendance', 'Pending Review', 'Cancel Request']

    def test_excuses(self):
        html = self.off_and_on(
            reverse('my_excuses'), 'rf-exc', self.excuse_hooks(),
            off_markers=['<h4 class="text-lg font-semibold text-gray-900 dark:text-white truncate">'],
            on_markers=['rf-row rf-exc-event', 'rf-card rf-exc-request', '<span class="rf-chip is-warning">Pending Review</span>'])
        self.assertIn('rf-exc-counts', html)

    def test_cancelling_an_excuse_with_the_refresh_on_works(self):
        self.set_flag(True)
        self.assertIn('rf-exc-request', self.get(reverse('my_excuses')))
        response = self.client.post(reverse('cancel_excuse', args=[self.excuse.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceExcuse.objects.filter(pk=self.excuse.pk, status='pending').exists())

    def test_submit_excuse_form_keeps_its_fields(self):
        html = self.off_and_on(reverse('submit_excuse', args=[self.open_event.id]), 'rf-exc-form',
                               ['name="reason"', 'enctype="multipart/form-data"', 'csrfmiddlewaretoken'])
        self.assertIn('Alumni Weekend', html)

    # Kai -------------------------------------------------------------------------------

    def test_kai_dashboard(self):
        hooks = [reverse('user_view_kai_report', kwargs={'report_id': self.report.id}),
                 reverse('user_view_kai_report', kwargs={'report_id': self.named.id}),
                 reverse('submit_kai_report'), 'Noise after quiet hours', 'Late dues payment',
                 "Reports Where I'm Named", 'Regarding: ', 'About Kai Reports']
        html = self.off_and_on(reverse('user_kai_dashboard'), 'rf-kai', hooks,
                               off_markers=['<!-- Desktop table -->'],
                               on_markers=['rf-row rf-kai-row', 'rf-chip rf-kai-cat is-behavioral',
                                           'rf-chip rf-kai-cat is-financial'])
        # The Review Dashboard link stays permission-gated.
        self.assertNotIn(reverse('view_kai_reports'), html)

    def test_kai_report_and_submit_pages(self):
        self.off_and_on(reverse('user_view_kai_report', kwargs={'report_id': self.report.id}), 'rf-kai-report',
                        [reverse('user_kai_dashboard'), 'Noise after quiet hours'])
        self.off_and_on(reverse('submit_kai_report'), 'rf-kai-submit',
                        ['name="title"', 'name="description"', 'name="category"', reverse('submit_kai_commendation'),
                         'role="tablist"', 'csrfmiddlewaretoken'])

    # My Tasks --------------------------------------------------------------------------

    def test_my_tasks(self):
        pledge = make_user('rpe-3', name='Dmitri Pledgeson', member_type='Pledge')
        PledgeTask.objects.create(title='Learn the chapter history', task_type='reading', is_required=True,
                                  points=10, is_published=True, is_active=True, created_by=self.other)
        self.client.force_login(pledge)
        self.off_and_on(reverse('my_pledge_tasks'), 'rf-tasks', ['Learn the chapter history', 'My Tasks'])

    # Polls -------------------------------------------------------------------------------

    def test_polls(self):
        self.off_and_on(
            reverse('polls_view'), 'rf-polls',
            [reverse('take_poll', args=[self.poll_ann.id]), 'Formal theme vote', 'Shirt color', 'Anonymous',
             'Take Poll', 'Not yet answered', "You didn't respond", reverse('announcements')],
            off_markers=['<!-- Open Polls -->\n    <div class="mb-8">'],
            on_markers=['rf-row rf-poll-row', 'rf-row rf-poll-row is-closed'])

    # Notifications -----------------------------------------------------------------------

    def test_notifications_keep_every_script_hook(self):
        hooks = ['notif-row', f'data-id="{self.unread.id}"', f'data-id="{self.read.id}"', 'notif-link',
                 'class="notif-dot', 'js-mark-read', 'js-delete-notif', f'data-notif-id="{self.unread.id}"',
                 'id="mark-all-read-page"', '?filter=unread', '?filter=security', 'function markRead(',
                 'function deleteNotification(', reverse('announcements')]
        html = self.off_and_on(reverse('notifications'), 'rf-notifs', hooks,
                               off_markers=['<!-- Type Icon -->'],
                               on_markers=['rf-card rf-notif-list', 'notif-row rf-row rf-notif-row',
                                           ' rf-notif-tabs"'])
        self.assertEqual(html.count('js-mark-read'), 2)  # one unread row (+ the script's selector)

    def test_notifications_empty_state(self):
        Notification.objects.all().delete()
        self.set_flag(True)
        html = self.get(reverse('notifications') + '?filter=unread')
        self.assertIn('rf-empty-title">No notifications', html)
        self.assertIn("You're all caught up!", html)

    # Quote Book ------------------------------------------------------------------------------

    def test_quote_book_and_add_form(self):
        self.off_and_on(reverse('quote_book'), 'rf-quotes',
                        [reverse('submit_quote'), 'id="quote-book-stage"', 'id="quote-book-prev"',
                         'id="quote-book-next"', 'id="quote-book-toc"', 'page-flip.browser.js'],
                        on_markers=[' rf-quotes-head"'])
        self.off_and_on(reverse('submit_quote'), 'rf-quote-form',
                        ['name="quoted_member"', 'name="text"', 'name="context"', 'csrfmiddlewaretoken',
                         reverse('quote_book')])

    # Search, Guide, reading pages ----------------------------------------------------------------

    def test_search(self):
        url = reverse('global_search')
        self.off_and_on(url + '?q=Founders', 'rf-search',
                        [f'action="{url}"', 'name="q"', 'value="Founders"', 'Founders Day Dinner'])
        self.off_and_on(url, 'rf-search', ['Search Parliament', '?q=calendar'])

    def test_guide_index_and_reading_pages(self):
        self.off_and_on(reverse('guide_index'), 'rf-guide',
                        ['x-data="guideSearch"', 'x-model="q"', reverse('guide_events'), reverse('guide_excuses'),
                         reverse('guide_officer_hub'), "x-show=\"m('Events Management"])
        self.off_and_on(reverse('roberts_rules'), 'rf-read rf-rr',
                        ['id="searchInput"', 'id="tocNav"', 'href="#quorum"', 'id="mainContent"',
                         reverse('constitution_bylaws')])
        self.off_and_on(reverse('constitution_bylaws'), 'rf-read rf-cnb',
                        ['data-tab="document"', 'data-tab="resolutions"', 'id="tab-document"',
                         reverse('cnb_document_pdf')])


class GuideArticleTests(TestCase):
    def test_article_off_and_on(self):
        from src.models.guide import GuideArticle
        user = make_user('rpe-9')
        GuideArticle.objects.create(slug='rpe-article', title='Submitting an excuse', category='members',
                                    summary='How to ask off.', content='<p>Body text.</p>', is_published=True)
        client = Client()
        client.force_login(user)
        url = reverse('guide_article', kwargs={'slug': 'rpe-article'})
        html = client.get(url).content.decode()
        self.assertNotIn('rf-guide-article', html)
        FeatureFlag.objects.update_or_create(name='web_refresh', defaults={
            'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': True})
        cache.clear()
        html = client.get(url).content.decode()
        self.assertIn('rf-mp rf-guide-article"', html)
        self.assertIn(PAGE_CSS, html)
        self.assertIn(reverse('guide_index'), html)
        self.assertIn('Body text.', html)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    either adds classes (`{% if ui_refresh %} rf-…{% endif %}`) or puts an
    include in front of the unchanged old block, closed by `{# /rf #}`."""

    TEMPLATES = ['templates/my_attendance.html', 'templates/my_excuses.html', 'templates/submit_excuse.html',
                 'templates/kai/user_dashboard.html', 'templates/kai/user_view_report.html',
                 'templates/kai/submit_report.html', 'templates/pledge/my_tasks.html', 'templates/polls.html',
                 'templates/notifications.html', 'templates/quote_book/book.html',
                 'templates/quote_book/submit.html', 'templates/global_search.html', 'templates/guide/index.html',
                 'templates/guide/article.html', 'templates/roberts_rules.html', 'templates/cnb/viewer.html']
    NEW = ['templates/attendance_refresh/_history.html', 'templates/excuses_refresh/_available.html',
           'templates/excuses_refresh/_requests.html', 'templates/kai_refresh/_reports.html',
           'templates/polls_refresh/_polls.html', 'templates/notifications_refresh/_list.html',
           'static/css/refresh-pages/member-tools.css']

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
