"""
The website refresh (src/ui_refresh.py) on Calendar and Directory.

What must hold:
* off: the old markup (no refresh stylesheet, switch classes or rows);
* on: the app-style list rows and the page stylesheet, with every hook the
  pages' own scripts use (event openers, month navigation, search, sort, type
  filters, profile card, export form) still in the page;
* the templates' switches only wrap unchanged lines, and the new includes
  carry no inline script or style (CSP).
"""
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Event, ParliamentUser
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/calendar-directory.css'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pages-b-12345!')
    user.save()
    return user


class RefreshPagesBTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user('rpb-1', name='Avery Testwell', role_number='301',
                              email='avery@example.com', phone_number='2055550101')
        make_user('rpb-2', name='Jonah Officerly', member_type='Officer', role_number='302')
        make_user('rpb-3', name='Dmitri Pledgeson', member_type='Pledge')
        self.event = Event.objects.create(
            title='Refresh Test Meeting', description='Bring updates.', location='Chapter Room',
            date_time=timezone.now() + timedelta(hours=2), created_by=self.user, is_active=True)
        self.client = Client()
        self.client.force_login(self.user)

    def set_flag(self, on):
        FeatureFlag.objects.update_or_create(
            name='web_refresh',
            defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
        )
        cache.clear()

    def page(self, name, query=''):
        response = self.client.get(reverse(name) + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # Calendar ---------------------------------------------------------------

    def test_calendar_off_is_the_old_page(self):
        html = self.page('calendar')
        self.assertNotIn(PAGE_CSS, html)
        for marker in ('rf-cal', 'rf-row'):
            self.assertNotIn(marker, html)
        self.assertIn('<h3 class="text-xl font-semibold mb-4 text-gray-800 dark:text-gray-100">Upcoming Events</h3>', html)
        self.assertIn('<div id="dynamicModals"></div>', html)

    def test_calendar_on_uses_the_app_rows_and_keeps_its_hooks(self):
        self.set_flag(True)
        html = self.page('calendar')
        self.assertIn(PAGE_CSS, html)
        self.assertIn('rf-cal-page', html)
        self.assertIn(' rf-cal"', html)
        self.assertIn('class="rf-cal-modals"', html)
        self.assertIn('rf-cal-upcoming', html)
        self.assertNotIn('<h3 class="text-xl font-semibold mb-4 text-gray-800 dark:text-gray-100">Upcoming Events</h3>', html)
        # The upcoming row opens the same event details as before.
        self.assertIn(f'class="rf-row rf-cal-row js-static-event-open" data-event-id="{self.event.id}"', html)
        self.assertIn(f'id="eventModal{self.event.id}"', html)
        self.assertIn('Refresh Test Meeting', html)
        # Month navigation, the picker and the script-drawn grid are untouched.
        for hook in ('id="prevMonthBtn"', 'id="nextMonthBtn"', 'id="js-toggle-month"', 'id="monthDropdown"',
                     'id="calendarGrid"', 'id="monthYearTitle"', 'js-static-event-close',
                     reverse('export_calendar_ical'),
                     reverse('export_event_ical', args=[self.event.id]), 'function renderCalendar(',
                     'function showEventModal(', '/api/calendar-data/'):
            self.assertIn(hook, html)

    # Directory --------------------------------------------------------------

    def test_directory_off_is_the_old_page(self):
        html = self.page('member_directory')
        self.assertNotIn(PAGE_CSS, html)
        for marker in ('rf-dir', 'rf-profile-modal', 'rf-row'):
            self.assertNotIn(marker, html)
        self.assertIn('<div class="mb-8 directory-section">', html)
        self.assertIn('class="member-card bg-white dark:bg-gray-800 rounded-xl shadow-sm p-4 border-l-4 border-green-500"', html)

    def test_directory_on_uses_the_app_rows_and_keeps_search_filters_and_card(self):
        self.set_flag(True)
        html = self.page('member_directory')
        self.assertIn(PAGE_CSS, html)
        self.assertIn('rf-dir"', html)
        self.assertIn('rf-profile-modal', html)
        self.assertNotIn('<div class="mb-8 directory-section">', html)
        self.assertIn('class="directory-section rf-dir-section"', html)
        self.assertEqual(html.count('class="member-card rf-row rf-dir-row"'), 3)
        # The search and filters read these attributes off every row.
        self.assertIn('data-name="avery testwell"', html)
        self.assertIn('data-email="avery@example.com"', html)
        self.assertIn('data-role="301"', html)
        for kind in ('officer', 'member', 'pledge'):
            self.assertIn(f'data-type="{kind}"', html)
            self.assertIn(f'data-filter="{kind}"', html)
        self.assertIn('data-profile-user="rpb-1"', html)
        self.assertIn('mailto:avery@example.com', html)
        # Search box, sort menu, filter pills, alumni toggle, profile card and
        # export form keep their ids and field names.
        for hook in ('id="directorySearch"', 'id="directorySort"', 'value="name_desc"', 'value="roll"',
                     'id="type-filters"', 'class="filter-pill active', '?show_alumni=1',
                     'id="profileModal"', 'id="profileModalContent"', 'id="pm-contact-list"',
                     'id="noResults"', 'id="exportModal"', 'name="format"', 'name="include_alumni"',
                     'name="include_email"', 'name="include_roles"', reverse('export_directory'),
                     reverse('house_map'), reverse('role_knowledge_base_index')):
            self.assertIn(hook, html)

    def test_directory_on_keeps_alumni_and_founders_switches(self):
        self.set_flag(True)
        make_user('rpb-4', name='Wendell Alumnus', member_status='Alumni', role_number='99')
        html = self.page('member_directory', '?show_alumni=1')
        self.assertIn('data-type="alumni"', html)
        self.assertIn('#99 &middot; Alumni', html)
        self.assertIn('?show_alumni=1&show_founders=1', html)

    def test_directory_on_hides_contact_like_the_old_cards(self):
        self.set_flag(True)
        from unittest import mock
        with mock.patch('src.view.directory.hides_contact_for', return_value=True):
            html = self.page('member_directory')
        self.assertNotIn('mailto:avery@example.com', html)
        self.assertNotIn('data-email="avery@example.com"', html)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and HEAD's file comes back (the same check as
    test_web_refresh for Home and Committees)."""

    TEMPLATES = ['templates/calendar.html', 'templates/directory.html']
    NEW = ['templates/calendar_refresh/_upcoming.html', 'templates/directory_refresh/_sections.html',
           'templates/directory_refresh/_member_row.html', 'static/css/refresh-pages/calendar-directory.css']

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% if ui_refresh %\}\{% include "[^"]+" %\}\{% else %\}', '', text)
        text = re.sub(r'\{% if ui_refresh %\}( rf-[\w-]+| class="rf-[\w-]+")\{% endif %\}', '', text)
        return re.sub(r'\{% endif %\}\{% endif %\}$', '{% endif %}', text, flags=re.M)

    def test_every_switch_wraps_unchanged_lines(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                stripped = self.strip_switches((base / name).read_text(encoding='utf-8'))
                self.assertNotIn('ui_refresh', stripped)

    def test_new_files_exist_and_carry_no_inline_script_or_style(self):
        base = Path(settings.BASE_DIR)
        for name in self.NEW:
            with self.subTest(file=name):
                text = (base / name).read_text(encoding='utf-8')
                if name.endswith('.html'):
                    self.assertNotIn('<script', text)
                    self.assertNotIn('<style', text)
                    self.assertNotIn('style="', text)
                    self.assertNotIn('.exists', text)
