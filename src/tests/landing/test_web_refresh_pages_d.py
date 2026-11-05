"""
The website refresh (src/ui_refresh.py) on Chats, Service Hours, Preferences
and Songbook.

What must hold:
* off: the old markup (no page stylesheet, switch classes or app rows);
* on: the app-style rows and the page stylesheet, with every link, form,
  field and hook the pages had;
* Preferences: the same fields under the same names and ids, and saving with
  the refresh on changes exactly what saving with it off does (the top bar's
  show_*_menu links among them);
* the templates' switches only wrap unchanged lines, and the new includes
  carry no inline script or style (CSP).
"""
import re
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser
from src.models.chat import ChatChannel, ChatMessage
from src.models.service import ServiceHoursSubmission, ServicePeriod
from src.models.songs import Song, SongCategory
from src.models_feature_flags import FeatureFlag
from src.view.preferences import preference_values, preferences_for

PAGE_CSS = 'css/refresh-pages/chats-service-prefs-songbook.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-d-12345!')
    user.save()
    return user


def set_flag(name, on):
    FeatureFlag.objects.update_or_create(
        name=name,
        defaults={'display_name': name, 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


class FormFields(HTMLParser):
    """Every control inside <form id=...>: (tag, name, type, id) plus the value
    a browser would post for it as rendered."""

    def __init__(self, form_id):
        super().__init__()
        self.form_id, self.depth, self.select = form_id, 0, None
        self.controls, self.posted = [], {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form' and a.get('id') == self.form_id:
            self.depth = 1
            return
        if not self.depth:
            return
        if tag == 'form':
            self.depth += 1
        if tag in ('input', 'select', 'textarea'):
            self.controls.append((tag, a.get('name'), a.get('type'), a.get('id')))
            if tag == 'input' and a.get('name'):
                if a.get('type') == 'checkbox':
                    if 'checked' in a:
                        self.posted[a['name']] = 'on'
                elif a.get('type') not in ('submit', 'button'):
                    self.posted[a['name']] = a.get('value', '')
            if tag == 'select':
                self.select = a.get('name')
        if tag == 'option' and self.select and 'selected' in a:
            self.posted[self.select] = a.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'select':
            self.select = None
        if tag == 'form' and self.depth:
            self.depth -= 1


def form_fields(html, form_id):
    parser = FormFields(form_id)
    parser.feed(html)
    return parser


class RefreshPagesDTests(TestCase):
    def setUp(self):
        cache.clear()
        set_flag('chats', True)
        self.user = make_user('rpd-1', name='Avery Testwell', is_admin=True)
        self.other = make_user('rpd-2', name='Jonah Officerly', member_type='Officer')
        self.channel = ChatChannel.objects.create(
            name='Refresh Test Channel', description='Planning the formal', icon='🎉',
            channel_type='custom', access_type='open', created_by=self.user)
        ChatMessage.objects.create(channel=self.channel, sender=self.other, message='See you Friday.')
        self.period = ServicePeriod.objects.create(
            name='Refresh Period', start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=30), default_hours_required=Decimal('10'))
        self.pending = ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=self.user, hours=Decimal('2.5'), service_date=date.today(),
            organization='Refresh Food Bank', description='Packed boxes for families.', status='pending')
        self.approved = ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=self.user, hours=Decimal('4'), service_date=date.today(),
            organization='Refresh Habitat', description='Framed walls.', status='approved')
        self.category = SongCategory.objects.create(name='Refresh Hymns', color='yellow')
        self.song = Song.objects.create(title='Refresh Alma Mater', lyrics='Hail to thee our brotherhood',
                                        category=self.category, created_by=self.user)
        SongCategory.invalidate_tabs_cache()
        self.client = Client()
        self.client.force_login(self.user)

    def page(self, name, *args, query=''):
        response = self.client.get(reverse(name, args=args) + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # Chats ------------------------------------------------------------------

    def test_chats_off_is_the_old_page(self):
        html = self.page('chat_index')
        self.assertNotIn(PAGE_CSS, html)
        for marker in ('rf-chats', 'rf-chat-row', 'rf-row'):
            self.assertNotIn(marker, html)
        self.assertIn('<div class="grid gap-4">', html)

    def test_chats_on_uses_the_app_rows_and_keeps_links_and_admin_controls(self):
        set_flag('web_refresh', True)
        html = self.page('chat_index')
        self.assertIn(PAGE_CSS, html)
        self.assertIn(' rf-chats"', html)
        self.assertNotIn('<div class="grid gap-4">', html)
        self.assertIn(f'href="{reverse("channel_chat", args=[self.channel.id])}" class="rf-row rf-chat-row is-unread"', html)
        for text in ('Refresh Test Channel', 'Planning the formal', '🎉', 'Custom', '• Open to all',
                     '>1</span>', 'Edit Settings', 'Delete Channel',
                     reverse('edit_channel', args=[self.channel.id]), reverse('delete_channel', args=[self.channel.id]),
                     reverse('chat_muted_members'), reverse('create_channel'), '?view_all=true'):
            self.assertIn(text, html)

    def test_chats_on_view_all_marks_admin_only_channels(self):
        set_flag('web_refresh', True)
        ChatChannel.objects.create(name='Hidden Room', channel_type='custom', access_type='restricted',
                                   created_by=self.other)
        html = self.page('chat_index', query='?view_all=true')
        self.assertIn('Hidden Room', html)
        self.assertIn('<span class="rf-chip is-danger">Admin View</span>', html)
        self.assertIn('• Restricted', html)
        self.assertIn('View My Chats', html)

    def test_chats_on_empty_state(self):
        set_flag('web_refresh', True)
        ChatChannel.objects.all().delete()
        html = self.page('chat_index')
        self.assertIn('No chats available', html)
        self.assertIn("You don't have access to any chat channels yet.", html)

    def test_conversation_is_not_edited(self):
        """Its look changes through the page stylesheet only."""
        set_flag('web_refresh', True)
        on = self.page('channel_chat', self.channel.id)
        self.assertIn(PAGE_CSS, on)
        self.assertNotIn('rf-', re.sub(r'<link[^>]*>', '', on.split('<main', 1)[-1]).split('</main>')[0])
        for hook in ('id="chat-container"', 'id="messages-list"', 'id="message-form"', 'id="message-input"',
                     'id="notif-pref-btn"', 'id="reportModal"'):
            self.assertIn(hook, on)

    # Service Hours ----------------------------------------------------------

    def test_service_off_is_the_old_page(self):
        html = self.page('user_service_dashboard')
        self.assertNotIn(PAGE_CSS, html)
        for marker in ('rf-svc', 'rf-row'):
            self.assertNotIn(marker, html)
        self.assertIn('<table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">', html)

    def test_service_on_lists_submissions_as_rows_with_view_and_edit(self):
        set_flag('web_refresh', True)
        html = self.page('user_service_dashboard')
        self.assertIn(PAGE_CSS, html)
        for cls in (' rf-svc"', ' rf-svc-progress"', ' rf-svc-stats"'):
            self.assertIn(cls, html)
        self.assertNotIn('<table class="min-w-full', html)
        self.assertEqual(html.count('class="rf-row rf-row-group rf-svc-row"'), 2)
        for text in ('Refresh Food Bank', 'Packed boxes for families.', '2.50 hrs', 'Pending Approval', 'Approved',
                     'Refresh Period', reverse('user_view_service_submission', args=[self.pending.id]),
                     reverse('user_view_service_submission', args=[self.approved.id]),
                     reverse('submit_service_hours')):
            self.assertIn(text, html)
        # Edit only where the old page offered it.
        self.assertEqual(self.pending.can_edit(), reverse('edit_service_submission', args=[self.pending.id]) in html)
        self.assertEqual(self.approved.can_edit(), reverse('edit_service_submission', args=[self.approved.id]) in html)

    def test_service_on_empty_state(self):
        set_flag('web_refresh', True)
        ServiceHoursSubmission.objects.all().delete()
        html = self.page('user_service_dashboard')
        self.assertIn('No submissions yet', html)
        self.assertIn('Get started by submitting your first service hours.', html)

    def test_submit_form_keeps_every_field(self):
        off = form_fields(self.page('submit_service_hours'), 'submit-form').controls
        set_flag('web_refresh', True)
        html = self.page('submit_service_hours')
        self.assertIn(' rf-svc-form"', html)
        self.assertEqual(form_fields(html, 'submit-form').controls, off)
        self.assertIn('enctype="multipart/form-data"', html)
        for hook in ('id="add-date-btn"', 'id="extra-dates-container"', 'id="submit-btn"', "name = 'extra_dates'"):
            self.assertIn(hook, html)

    # Preferences ------------------------------------------------------------

    def test_preferences_on_keeps_every_field(self):
        off_html = self.page('preferences')
        self.assertNotIn('rf-prefs', off_html)
        off = form_fields(off_html, 'preferences-form')
        set_flag('web_refresh', True)
        on_html = self.page('preferences')
        on = form_fields(on_html, 'preferences-form')
        self.assertIn(' rf-prefs"', on_html)
        self.assertEqual(on.controls, off.controls)
        for parsed in (on, off):
            parsed.posted.pop('csrfmiddlewaretoken', None)
        self.assertEqual(on.posted, off.posted)
        names = {c[1] for c in on.controls}
        for menu in ('show_vote_menu', 'show_committees_menu', 'show_chats_menu', 'show_calendar_menu',
                     'show_search_menu', 'show_roberts_rules_menu'):
            self.assertIn(menu, names)
            self.assertIn(f'id="id_{menu}"', on_html)
        self.assertIn('menu-checkbox', on_html)
        self.assertIn('id="menu-limit-warning"', on_html)

    def _save(self, flag_on, changes):
        set_flag('web_refresh', flag_on)
        payload = form_fields(self.page('preferences'), 'preferences-form').posted
        for key, value in changes.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        response = self.client.post(reverse('preferences'), payload)
        self.assertIn(response.status_code, (302, 303))
        return preference_values(preferences_for(self.user))

    def test_saving_with_the_refresh_on_changes_the_same_values(self):
        changes = {'show_chats_menu': 'on', 'show_vote_menu': None, 'email_events': None, 'landing_page': 'calendar'}
        start = preferences_for(self.user)
        before = dict(start.prefs)
        off_result = self._save(False, changes)
        start.refresh_from_db()
        start.prefs = before
        start.save()
        self.assertNotEqual(preference_values(start), off_result)
        on_result = self._save(True, changes)
        self.assertEqual(on_result, off_result)
        self.assertTrue(on_result.get('show_chats_menu'))
        self.assertFalse(on_result.get('show_vote_menu'))
        self.assertFalse(on_result.get('email_events'))
        self.assertEqual(on_result.get('landing_page'), 'calendar')
        # …and the top bar follows: Chats is in it now.
        html = self.page('home')
        nav = html.split('<nav', 1)[1].split('</nav>', 1)[0]
        self.assertIn(reverse('chat_index'), nav)

    # Songbook ---------------------------------------------------------------

    def test_songbook_off_is_the_old_page(self):
        html = self.page('songbook')
        self.assertNotIn(PAGE_CSS, html)
        for marker in ('rf-songbook', 'rf-song-row'):
            self.assertNotIn(marker, html)
        self.assertIn('<div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">', html)

    def test_songbook_on_lists_songs_as_rows(self):
        set_flag('web_refresh', True)
        html = self.page('songbook')
        self.assertIn(PAGE_CSS, html)
        for cls in (' rf-songbook"', ' rf-songbook-cats"', ' rf-songbook-search"'):
            self.assertIn(cls, html)
        self.assertNotIn('md:grid-cols-2 lg:grid-cols-3', html)
        self.assertIn(f'href="{reverse("song_detail", args=[self.song.pk])}" class="rf-row rf-song-row"', html)
        self.assertIn('class="rf-tile rf-song-tile is-yellow"', html)
        for text in ('Refresh Alma Mater', 'Refresh Hymns', 'Hail to thee our brotherhood', 'Click to view full lyrics',
                     f'?category={self.category.id}', 'name="q"', reverse('song_create'),
                     reverse('manage_song_categories')):
            self.assertIn(text, html)

    def test_songbook_on_empty_search(self):
        set_flag('web_refresh', True)
        html = self.page('songbook', query='?q=zzzznothing')
        self.assertIn('No Songs Found', html)
        self.assertIn('Clear search', html)

    def test_song_page_keeps_its_controls(self):
        off = self.page('song_detail', self.song.pk)
        set_flag('web_refresh', True)
        html = self.page('song_detail', self.song.pk)
        self.assertIn(' rf-song"', html)
        self.assertIn(' rf-song-modal"', html)
        self.assertNotIn('rf-song', off)
        for hook in ('id="js-confirm-delete"', 'id="deleteModal"', 'js-close-delete-modal',
                     reverse('song_edit', args=[self.song.pk]), reverse('song_delete', args=[self.song.pk]),
                     'Hail to thee our brotherhood'):
            self.assertIn(hook, html)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and HEAD's file comes back."""

    TEMPLATES = ['templates/chat/index.html', 'templates/service_hours/user_dashboard.html',
                 'templates/service_hours/submit_hours.html', 'templates/preferences.html',
                 'templates/songbook.html', 'templates/songbook_detail.html']
    NEW = ['templates/chat_refresh/_channels.html', 'templates/service_hours_refresh/_submissions.html',
           'templates/songbook_refresh/_songs.html', 'static/css/refresh-pages/chats-service-prefs-songbook.css']

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% if ui_refresh %\}\{% include "[^"]+" %\}\{% else %\}', '', text)
        text = re.sub(r'\{% if ui_refresh %\}( rf-[\w-]+| class="rf-[\w-]+")\{% endif %\}', '', text)
        text = re.sub(r'\{% endif %\}\{% endif %\}$', '{% endif %}', text, flags=re.M)
        return re.sub(r'</div>\{% endif %\}', '</div>', text)

    def test_every_switch_wraps_unchanged_lines(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                stripped = self.strip_switches((base / name).read_text(encoding='utf-8'))
                self.assertNotIn('ui_refresh', stripped)

    def test_new_files_carry_no_inline_script_or_style(self):
        base = Path(settings.BASE_DIR)
        for name in self.NEW:
            with self.subTest(file=name):
                text = (base / name).read_text(encoding='utf-8')
                if name.endswith('.html'):
                    self.assertNotIn('<script', text)
                    self.assertNotIn('<style', text)
                    self.assertNotIn('style="', text)
                else:
                    self.assertNotIn('@import', text)
                    self.assertNotIn('http', text)

    def test_base_links_the_page_stylesheet_only_with_the_refresh(self):
        base = (Path(settings.BASE_DIR) / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}", base)
