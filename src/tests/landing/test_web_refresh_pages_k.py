"""
The website refresh (src/ui_refresh.py) on the remaining pages: chat
management (a channel's chrome, create / edit / delete, muted members,
reports), bug reports and feedback, meeting agendas, the C&B manager,
songbook admin, roadmap and changelog, the house map, the role knowledge
base, Kai requests / commendation / form builder, flagged quotes, an event's
sign-up list, announcement polls, log out, the error and notice pages, and
the guide's category hub and topic pages.

What must hold:
* off: the old markup (no page stylesheet, no switch classes);
* on: the page's switch classes and the stylesheet, with every link, form,
  field and script hook the page had;
* the templates' switches only add classes (strip them and no `ui_refresh`
  or `rf-` is left), and the stylesheet carries no !important, @import or
  remote URL;
* 500.html renders with no context at all (no request, no processors).
"""
import re
from datetime import timedelta, time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.template.loader import get_template
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Announcement, Event, ParliamentUser
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/misc.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active',
                    email=f'{uid}@samford.edu')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-k-12345!')
    user.save()
    return user


def set_flag(on):
    FeatureFlag.objects.update_or_create(
        name='web_refresh',
        defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


class RefreshPagesKTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user('rpk-1', name='Avery Admin', member_type='Officer', is_admin=True)
        self.other = make_user('rpk-2', name='Jonah Member')
        self.third = make_user('rpk-3', name='Malik Member')
        self.client = Client()
        self.client.force_login(self.admin)

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def off_and_on(self, url, classes, hooks):
        html = self.get(url)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn('rf-mx', html)
        for hook in hooks:
            self.assertIn(hook, html, url)
        set_flag(True)
        try:
            html = self.get(url)
            self.assertIn(PAGE_CSS, html)
            self.assertIn(f' {classes}"', html, url)
            for hook in hooks:
                self.assertIn(hook, html, url)
        finally:
            set_flag(False)
        return html

    # Chat ----------------------------------------------------------------------

    def test_chat_pages(self):
        from src.models.chat import ChatChannel, ChatMessage, ChatMessageReport, ChatUserMute
        channel = ChatChannel.objects.create(name='Rush Planning', description='Rush week.', channel_type='custom',
                                             access_type='open', created_by=self.admin)
        msg = ChatMessage.objects.create(channel=channel, sender=self.other, message='Off topic rant.')
        ChatMessageReport.objects.create(message=msg, reporter=self.third, reason='spam', details='Not rush.')
        ChatUserMute.objects.create(muter=self.admin, muted=self.third)
        # The conversation page itself was restyled by batch d (its own stylesheet,
        # no classes in <main>); see test_web_refresh_pages_d.
        self.off_and_on(reverse('create_channel'), 'rf-mp rf-mx rf-chat-form',
                        ['name="name"', 'name="access_type"', 'csrfmiddlewaretoken'])
        self.off_and_on(reverse('edit_channel', args=[channel.id]), 'rf-mp rf-mx rf-chat-form',
                        ['name="name"', 'id="user-perm-list"'])
        self.off_and_on(reverse('delete_channel', args=[channel.id]), 'rf-mp rf-mx rf-confirm',
                        ['method="post"', 'Yes, Delete Channel'])
        self.off_and_on(reverse('chat_muted_members'), 'rf-mp rf-mx rf-chat-muted',
                        [reverse('chat_unmute_member', args=[self.third.user_id]), 'Malik Member'])
        self.off_and_on(reverse('chat_reports'), 'rf-mp rf-mx rf-chat-reports',
                        [reverse('chat_review_report', args=[msg.reports.first().id]), 'value="dismiss"',
                         'value="delete_message"', 'name="note"', '?status='])

    # Bug reports and feedback ---------------------------------------------------

    def test_bug_and_feedback_pages(self):
        from src.models.security import BugReport, FeedbackRequest
        bug = BugReport.objects.create(description='The vote button stays disabled.', issue_type='functionality',
                                       priority='high', status='new', submitted_by=self.admin)
        idea = FeedbackRequest.objects.create(title='CSV export', request_type='feature', priority='medium',
                                              description='Export attendance.', submitted_by=self.admin)
        self.off_and_on(reverse('bug_report'), 'rf-mp rf-mx rf-support-form',
                        ['name="description"', 'name="issue_type"', 'name="screenshot"', 'id="browser_info"',
                         'js-toggle-section', reverse('bug_tracker'), reverse('my_bug_reports')])
        self.off_and_on(reverse('bug_tracker'), 'rf-mp rf-mx rf-tracker',
                        [reverse('bug_report_detail', args=[bug.id]), 'name="status"', 'name="type"',
                         reverse('bug_report')])
        self.off_and_on(reverse('bug_report_detail', args=[bug.id]), 'rf-mp rf-mx rf-support-detail',
                        [reverse('bug_tracker'), 'The vote button stays disabled.'])
        self.off_and_on(reverse('bug_report_success', args=[bug.id]), 'rf-mp rf-mx rf-done',
                        [reverse('bug_report')])
        self.off_and_on(reverse('my_bug_reports'), 'rf-mp rf-mx rf-mine', ['#' + str(bug.id), reverse('bug_report')])
        self.off_and_on(reverse('feedback_request'), 'rf-mp rf-mx rf-support-form',
                        ['name="title"', 'name="description"', 'name="request_type"'])
        self.off_and_on(reverse('feedback_tracker'), 'rf-mp rf-mx rf-tracker', [reverse('feedback_request')])
        self.off_and_on(reverse('my_feedback_requests'), 'rf-mp rf-mx rf-mine', ['CSV export'])
        self.off_and_on(reverse('feedback_request_detail', args=[idea.id]), 'rf-mp rf-mx rf-support-detail',
                        ['CSV export'])
        self.off_and_on(reverse('feedback_request_success', args=[idea.id]), 'rf-mp rf-mx rf-done',
                        [reverse('feedback_request')])

    # Agendas ------------------------------------------------------------------------

    def test_agenda_pages(self):
        from src.models import AgendaItem, MeetingAgenda
        agenda = MeetingAgenda.objects.create(title='Chapter Meeting', date=timezone.localdate() + timedelta(days=3),
                                              start_time=time(19, 0), status='published', created_by=self.admin)
        a = AgendaItem.objects.create(agenda=agenda, order=0, title='Call to order', duration_minutes=1)
        AgendaItem.objects.create(agenda=agenda, order=1, title='Officer reports', duration_minutes=15)
        self.off_and_on(reverse('agenda_list'), 'rf-mp rf-mx rf-agendas',
                        [reverse('create_agenda'), 'name="standard_order"', reverse('edit_agenda', args=[agenda.pk])])
        self.off_and_on(reverse('agenda_detail', args=[agenda.pk]), 'rf-mp rf-mx rf-agenda',
                        ['Call to order', reverse('agenda_list'), reverse('edit_agenda', args=[agenda.pk])])
        self.off_and_on(reverse('edit_agenda', args=[agenda.pk]), 'rf-mp rf-mx rf-agenda rf-agenda-edit',
                        [reverse('set_agenda_status', args=[agenda.pk]), reverse('add_agenda_item', args=[agenda.pk]),
                         reverse('update_agenda_item', args=[agenda.pk, a.pk]), 'value="down"', 'value="delete"',
                         reverse('start_minutes_from_agenda', args=[agenda.pk])])

    # C&B manager ----------------------------------------------------------------------

    def test_cnb_manager_pages(self):
        from src.models.cnb import Article, GoverningDocument, Resolution, Section
        doc = GoverningDocument.objects.create(doc_type='constitution', title='Constitution')
        art = Article.objects.create(document=doc, number='I', title='Name and Purpose')
        sec = Section.objects.create(article=art, number='1', title='Name', content='The name shall be Alpha Mu.')
        res = Resolution.objects.create(title='On the Spring Formal Budget',
                                        resolution_type=Resolution._meta.get_field('resolution_type').choices[0][0],
                                        status='draft', resolved_text='RESOLVED that funds be set aside.',
                                        created_by=self.admin)
        self.off_and_on(reverse('cnb_manage_document', kwargs={'doc_type': 'constitution'}), 'rf-mp rf-mx rf-cnbm',
                        [reverse('cnb_edit_section', args=[sec.id]), 'The name shall be Alpha Mu.'])
        self.off_and_on(reverse('cnb_edit_section', args=[sec.id]), 'rf-mp rf-mx rf-cnbm', ['name="content"'])
        self.off_and_on(reverse('cnb_resolution_detail', args=[res.id]), 'rf-mp rf-mx rf-cnbm',
                        ['On the Spring Formal Budget', reverse('cnb_edit_resolution', args=[res.id])])
        self.off_and_on(reverse('cnb_resolution_document_preview', args=[res.id]),
                        'rf-mp rf-mx rf-cnbm rf-cnbm-preview', ['On the Spring Formal Budget'])
        self.off_and_on(reverse('cnb_create_resolution'), 'rf-mp rf-mx rf-cnbm', ['name="title"', 'name="whereas_clauses"'])
        self.off_and_on(reverse('cnb_section_history', args=[sec.id]), 'rf-mp rf-mx rf-cnbm', [reverse('constitution_bylaws')])

    # Reading and misc pages -------------------------------------------------------

    def test_misc_pages(self):
        from src.models.events import EventSignup
        from src.models.announcements import AnnouncementPoll
        from src.models.songs import SongCategory
        SongCategory.objects.create(name='Fight Songs', color='blue')
        self.off_and_on(reverse('manage_song_categories'), 'rf-mp rf-mx rf-song-admin', ['name="name"', 'Fight Songs'])
        self.off_and_on(reverse('song_create'), 'rf-mp rf-mx rf-song-admin', ['name="title"'])
        self.off_and_on(reverse('roadmap'), 'rf-mp rf-mx rf-roadmap', [reverse('changelog')])
        self.off_and_on(reverse('changelog'), 'rf-mp rf-mx rf-changelog',
                        ['id="external-filter-btn"', 'changelog-group-toggle', reverse('roadmap')])
        self.off_and_on(reverse('house_map'), 'rf-mp rf-mx rf-house', ['House Map'])
        self.off_and_on(reverse('role_knowledge_base_index'), 'rf-mp rf-mx rf-rolekb', ['name="q"'])
        self.off_and_on(reverse('kai_form_builder'), 'rf-mx-lite rf-kai-builder', ['Form Builder'])
        self.off_and_on(reverse('submit_kai_commendation'), 'rf-mp rf-mx rf-kai-commend', ['csrfmiddlewaretoken'])
        self.off_and_on(reverse('review_flagged_quotes'), 'rf-mp rf-mx rf-quotes-flagged', [reverse('quote_book')])
        event = Event.objects.create(title='Philanthropy 5K', date_time=timezone.now() + timedelta(days=4),
                                     location='Campus Green', created_by=self.admin, is_active=True,
                                     requires_signup=True)
        EventSignup.objects.create(event=event, user=self.other)
        self.off_and_on(reverse('event_signup_list', args=[event.id]), 'rf-mp rf-mx rf-signups',
                        ['Jonah Member', reverse('event_signup_export', args=[event.id])])
        ann = Announcement.objects.create(title='Formal theme', content='Vote below.', posted_by=self.admin)
        AnnouncementPoll.objects.create(announcement=ann, title='Pick the theme', is_open=True, created_by=self.admin)
        self.off_and_on(reverse('take_poll', args=[ann.id]), 'rf-mp rf-mx rf-ann-poll', ['Pick the theme'])
        self.off_and_on(reverse('poll_confirmation', args=[ann.id]), 'rf-mp rf-mx rf-notice', [reverse('announcements')])
        self.off_and_on(reverse('logout'), 'rf-mp rf-mx rf-notice', [f'action="{reverse("logout")}"', reverse('home')])

    def test_kai_requests(self):
        from src.models.kai import KaiReport
        report = KaiReport.objects.create(title='Noise', submitted_by=self.admin, category='behavioral',
                                          description='Details.', status='reviewed', targeted_to=self.other,
                                          deliberation_outcome='heard')
        url = reverse('kai_request_closure', args=[report.id])
        if self.client.get(url).status_code == 200:
            self.off_and_on(url, 'rf-mp rf-mx rf-kai-request', ['name="reason"'])
        url = reverse('kai_request_drop', args=[report.id])
        if self.client.get(url).status_code == 200:
            self.off_and_on(url, 'rf-mp rf-mx rf-kai-request', ['name="reason"'])

    def test_guide_pages(self):
        for name, cls in [('guide_officer_hub', 'rf-guide-hub'), ('guide_officer_handoff', 'rf-guide-topic'),
                          ('guide_chat', 'rf-guide-topic'), ('guide_kai', 'rf-guide-topic'),
                          ('guide_2fa', 'rf-guide-topic'), ('guide_events', 'rf-guide-topic')]:
            self.off_and_on(reverse(name), f'rf-mp rf-mx {cls}', [reverse('guide_index')])

    # Error and notice pages --------------------------------------------------------

    def test_error_pages_render_without_any_context(self):
        for name in ('500.html', '404.html', '403.html'):
            html = get_template(name).render({})
            self.assertNotIn('rf-mx', html)
        html = get_template('500.html').render({'ui_refresh': True})
        self.assertIn('rf-mp rf-mx rf-notice rf-error"', html)
        self.assertIn('id="js-reload-btn"', html)

    def test_notice_templates_with_the_switch(self):
        request = self.client.get(reverse('home')).wsgi_request
        for name in ('403.html', '404.html', 'feature_disabled.html', 'page_disabled.html'):
            self.assertNotIn('rf-mx', get_template(name).render({'ui_refresh': False}, request))
            self.assertIn('rf-notice rf-error"', get_template(name).render({'ui_refresh': True}, request))
        for name in ('geo_restricted.html', 'errors/pledge_restricted.html', 'event_qr_checkin_result.html'):
            self.assertNotIn('rf-mx', get_template(name).render({'ui_refresh': False}, request))
            self.assertIn('rf-mp rf-mx rf-notice"', get_template(name).render({'ui_refresh': True}, request))


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    only adds classes (`{% if ui_refresh %} rf-…{% endif %}`) on the page's
    root element."""

    BASE = Path(settings.BASE_DIR)
    NAMES = ['chat/create_channel.html', 'chat/edit_channel.html', 'chat/delete_channel.html',
             'chat/muted_members.html', 'chat/reports.html', 'bug_report.html', 'bug_report_detail.html',
             'bug_report_success.html', 'bug_tracker.html', 'my_bug_reports.html', 'feedback_request.html',
             'feedback_request_detail.html', 'feedback_request_success.html', 'feedback_tracker.html',
             'my_feedback_requests.html', 'meetings/agenda_list.html', 'meetings/agenda_detail.html',
             'meetings/agenda_edit.html', 'cnb/dashboard.html', 'cnb/edit_section.html', 'cnb/manage_document.html',
             'cnb/resolution_detail.html', 'cnb/resolution_document_preview.html', 'cnb/resolution_form.html',
             'cnb/resolution_list.html', 'cnb/section_history.html', 'songbook_categories.html', 'songbook_form.html',
             'roadmap.html', 'changelog.html', 'changelog_detail.html', 'house_map.html',
             'role_knowledge_base_index.html', 'kai/request_closure.html', 'kai/request_drop.html',
             'kai/submit_commendation.html', 'kai/form_builder.html', 'quote_book/flagged.html',
             'calendar/event_signup_list.html', 'announcement_poll.html', 'announcement_poll_confirmation.html',
             'event_qr_checkin_result.html', 'logout_confirm.html', '403.html', '404.html', '500.html',
             'feature_disabled.html', 'page_disabled.html', 'geo_restricted.html', 'errors/pledge_restricted.html',
             'guide/category_hub.html', 'guide/handoff.html']

    def templates(self):
        names = list(self.NAMES)
        for folder in ('guide/members', 'guide/officers'):
            names += sorted(f'{folder}/{p.name}' for p in (self.BASE / 'templates' / folder).glob('*.html'))
        return names

    def test_every_switch_only_adds_classes(self):
        names = self.templates()
        self.assertGreaterEqual(len(names), 77)  # 77: chat/channel.html is batch d's
        for name in names:
            text = (self.BASE / 'templates' / name).read_text(encoding='utf-8')
            self.assertEqual(text.count('{% if ui_refresh %}'), 1)
            stripped = re.sub(r'\{% if ui_refresh %\}( [\w -]+)\{% endif %\}', '', text)
            self.assertNotIn('ui_refresh', stripped)
            self.assertNotRegex(stripped, r'\brf-')

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
        for rule in re.findall(r'(?m)^([^{}@\n][^{}]*)\{', css):
            for selector in self.top_level_split(rule):
                selector = selector.strip()
                if not selector or selector.startswith(':root.ui-refresh'):
                    continue
                self.assertRegex(selector, r'\.rf-')

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (self.BASE / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
