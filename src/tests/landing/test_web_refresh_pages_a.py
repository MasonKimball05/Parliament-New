"""
The website refresh (src/ui_refresh.py) on Vote, Announcements and Profile.

With the refresh off the pages carry none of its hooks or its stylesheet
(static/css/refresh-pages/vote-announcements-profile.css); with it on they
do. Either way they render the same forms, fields, ids, links and script
hooks: only the presentation changes. And a vote cast from the refreshed
ballot still counts.
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Announcement, AnnouncementPoll, Attendance, Legislation, ParliamentUser, Vote
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/vote-announcements-profile.css'


def hooks(html):
    """What a page's behaviour hangs on: form actions, field names, ids,
    links, `js-` classes and data- attributes (values of csrf tokens and
    nonces aside)."""
    body = html[html.index('<main'):] if '<main' in html else html
    found = set()
    for attr in ('name', 'id', 'action', 'href', 'for', 'type', 'value'):
        for v in re.findall(r'\s%s="([^"]*)"' % attr, body):
            if attr == 'value' and len(v) > 40:  # csrf tokens
                continue
            found.add((attr, v))
    for cls in re.findall(r'class="([^"]*)"', body):
        found.update(('js', c) for c in cls.split() if c.startswith('js-'))
    found.update(('data', d) for d in re.findall(r'\s(data-[\w-]+)=', body))
    return found


class RefreshPagesATests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = ParliamentUser.objects.create_user(
            user_id='rfa1', name='Refresh Voter', username='rfa1', member_type='Officer', member_status='Active')
        self.user.set_password('rfa-pass-12345!')
        self.user.onboarding_data = {'pages_visited': ['vote', 'announcements', 'profile']}
        self.user.save()
        self.author = ParliamentUser.objects.create_user(
            user_id='rfa2', name='Bill Author', username='rfa2', member_type='Officer', member_status='Active')
        Attendance.objects.create(user=self.user, status='present')
        now = timezone.now()
        self.leg = Legislation.objects.create(
            title='Quiet hours bill', description='Moves quiet hours.', posted_by=self.author,
            available_at=now, vote_mode='percentage', required_percentage='51', document='test.pdf')
        self.plurality = Legislation.objects.create(
            title='Venue pick', description='Pick a venue for the formal.', posted_by=self.author,
            available_at=now, vote_mode='plurality', plurality_options=['Hall', 'Lake'], allow_abstain=False)
        self.own = Legislation.objects.create(
            title='My own bill', description='Tallied for its author.', posted_by=self.user,
            available_at=now, vote_mode='percentage', required_percentage='51', document='test.pdf')
        ann = Announcement.objects.create(
            title='Formal tickets', content='On sale Monday https://example.com', posted_by=self.author,
            is_active=True, event_date=now)
        AnnouncementPoll.objects.create(announcement=ann, title='Formal RSVP', created_by=self.author)
        Announcement.objects.create(title='House cleaning', content='Saturday', posted_by=self.author, is_active=True)
        self.client = Client()
        self.client.force_login(self.user)

    def set_flag(self, on):
        FeatureFlag.objects.update_or_create(
            name='web_refresh',
            defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
        )
        cache.clear()

    def page(self, name):
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_off_has_no_refresh_markup(self):
        for name, old in (('vote', 'border-l-green-500'), ('announcements', 'border-l-4 border-primary-500'),
                          ('profile', 'ring-4 ring-white ring-opacity-50')):
            with self.subTest(page=name):
                html = self.page(name)
                self.assertNotIn(PAGE_CSS, html)
                self.assertNotIn('rf-vap', html)
                self.assertNotIn('{% if', html)
                self.assertIn(old, html)

    def test_on_uses_the_app_markup(self):
        self.set_flag(True)
        for name, new in (('vote', 'rf-choices'), ('announcements', 'rf-ann-card'), ('profile', 'rf-profile-hero')):
            with self.subTest(page=name):
                html = self.page(name)
                self.assertIn(PAGE_CSS, html)
                self.assertIn('rf-vap', html)
                self.assertIn(new, html)
        announcements = self.page('announcements')
        self.assertNotIn('border-l-4 border-primary-500', announcements)  # the old card is replaced
        self.assertIn('Take Poll: Formal RSVP', announcements)
        self.assertIn('Event Date:', announcements)
        profile = self.page('profile')
        self.assertNotIn('ring-4 ring-white ring-opacity-50', profile)  # the old blue header is replaced
        self.assertIn('Preferences &amp; Settings', profile)

    def test_same_forms_fields_ids_links_and_hooks_either_way(self):
        for name in ('vote', 'announcements', 'profile'):
            with self.subTest(page=name):
                self.set_flag(False)
                off = hooks(self.page(name))
                self.set_flag(True)
                on = hooks(self.page(name))
                self.assertGreater(len(off), 15)  # the comparison has something to compare
                self.assertEqual(off - on, set(), 'dropped under the refresh')
                self.assertEqual(on - off, set(), 'added under the refresh')

    def test_ballot_keeps_its_fields(self):
        self.set_flag(True)
        html = self.page('vote')
        form = html[html.index('class="js-vote-form'):]
        form = form[:form.index('</form>')]
        for field in ('name="csrfmiddlewaretoken"', 'name="legislation_id"', 'name="action" value="cast_vote"',
                      'name="vote_choice" value="yes"', 'name="vote_choice" value="no"',
                      'name="vote_choice" value="abstain"', 'name="password"'):
            self.assertIn(field, form)
        self.assertIn('action="%s"' % reverse('cast_vote'), html)
        self.assertIn('.js-vote-passkey', html)  # the shared confirm script (vote_passkey_confirm.html) is still there
        self.assertIn('data-tally-id="%d"' % self.own.id, html)

    def test_a_vote_cast_from_the_refreshed_page_counts(self):
        self.set_flag(True)
        self.page('vote')
        response = self.client.post(reverse('cast_vote'), {
            'action': 'cast_vote', 'legislation_id': self.leg.id,
            'vote_choice': 'no', 'password': 'rfa-pass-12345!'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Vote.objects.filter(user=self.user, legislation=self.leg, vote_choice='no').exists())
        html = response.content.decode()
        self.assertIn('id="fresh-receipt-token"', html)
        self.assertIn('rf-receipt', html)
        self.assertIn('Your vote has been recorded.', html)
        self.client.post(reverse('cast_vote'), {
            'action': 'cast_vote', 'legislation_id': self.plurality.id,
            'vote_choice': 'Lake', 'password': 'rfa-pass-12345!'})
        self.assertTrue(Vote.objects.filter(user=self.user, legislation=self.plurality, vote_choice='Lake').exists())


class SwitchesOnlyWrapTests(TestCase):
    """Each change in these templates is a `{% if ui_refresh %}` switch: take
    the switches out and no trace of the refresh is left."""

    TEMPLATES = ['templates/vote.html', 'templates/announcements.html', 'templates/profile.html']

    @staticmethod
    def strip(text):
        text = re.sub(r'\{% if ui_refresh %\}\{% include "[^"]+" %\}\{% else %\}', '', text)
        text = re.sub(r'\{% if ui_refresh %\}( rf-[\w -]+|<div class="rf-section-title"><h2>[^<]+</h2></div>)\{% endif %\}', '', text)
        return text

    def test_no_refresh_left_after_stripping(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                stripped = self.strip((base / name).read_text(encoding='utf-8'))
                self.assertNotIn('ui_refresh', stripped)
                self.assertNotIn('rf-', stripped)

    def test_page_stylesheet_exists(self):
        self.assertTrue((Path(settings.BASE_DIR) / 'static' / PAGE_CSS).is_file())
