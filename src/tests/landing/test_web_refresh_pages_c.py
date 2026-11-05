"""
The website refresh (src/ui_refresh.py) on a committee's own pages: the
committee home, Minutes, Documents, Voting and a vote's result page.

What must hold:
* off: the old markup (no page stylesheet, switch classes or app rows);
* on: the app-style rows and the page stylesheet, with every link, form,
  field, chair tool and script hook the old pages had;
* a committee ballot cast with the refresh on still counts;
* the templates' switches only wrap unchanged lines (strip them and no
  `ui_refresh` is left), and the new includes carry no inline script or
  style (CSP).
"""
import re
from datetime import time, timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Attendance, Committee, CommitteeLegislation, CommitteePermissions, CommitteeVote, ParliamentUser,
)
from src.models.documents import ChapterMinutes, CommitteeDocument
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/committee.css'
PASSWORD = 'refresh-pages-c-12345!'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password(PASSWORD)
    user.save()
    return user


class RefreshPagesCTests(TestCase):
    def setUp(self):
        cache.clear()
        self.chair = make_user('rpc-1', name='Avery Chairwell')
        self.member = make_user('rpc-2', name='Jonah Memberly')
        self.committee = Committee.objects.create(name='Refresh Finance', code='RFIN', is_active=True)
        self.committee.members.add(self.chair, self.member)
        self.committee.chairs.add(self.chair)
        self.committee.voting_members.add(self.chair, self.member)
        CommitteePermissions.objects.create(committee=self.committee, user=self.chair, can_view_docs=True,
                                            can_upload_docs=True)
        Attendance.objects.create(user=self.chair, status='present')
        Attendance.objects.create(user=self.member, status='present')
        now = timezone.now()
        self.open_leg = CommitteeLegislation.objects.create(
            committee=self.committee, title='Raise the social budget', description='Moves $300 to social.',
            posted_by=self.chair, available_at=now - timedelta(hours=1), voting_ends_at=now + timedelta(days=1))
        self.plurality = CommitteeLegislation.objects.create(
            committee=self.committee, title='Pick the dues app', description='Pick one.', posted_by=self.chair,
            available_at=now - timedelta(hours=1), vote_mode='plurality',
            plurality_options=['Venmo', 'Zelle'], allow_abstain=False)
        self.closed = CommitteeLegislation.objects.create(
            committee=self.committee, title='Approve the fall audit', description='Books balance.',
            posted_by=self.chair, available_at=now - timedelta(days=3), voting_closed=True,
            voting_ended_at=now - timedelta(days=2))
        CommitteeVote.objects.create(user=self.member, legislation=self.closed, vote_choice='yes')
        self.doc = CommitteeDocument.objects.create(
            committee=self.committee, title='Spring budget sheet', uploaded_by=self.chair, document_type='report',
            description='Line items.', document=ContentFile(b'%PDF-1.4 test', name='budget.pdf'))
        self.draft = ChapterMinutes.objects.create(
            committee=self.committee, title='Weekly finance meeting', date=now.date(), start_time=time(19, 0),
            created_by=self.chair, status='draft', attendance_taken=True)
        self.client = Client()
        self.client.force_login(self.chair)

    def set_flag(self, on):
        FeatureFlag.objects.update_or_create(
            name='web_refresh',
            defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
        )
        cache.clear()

    def page(self, name, *args):
        response = self.client.get(reverse(name, args=[self.committee.code, *args]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def assert_hooks(self, html, hooks):
        for hook in hooks:
            self.assertIn(hook, html)

    # Committee home -----------------------------------------------------------

    def home_hooks(self):
        code = self.committee.code
        return [reverse('committee_remove_member', args=[code]), reverse('committee_add_member', args=[code]),
                'name="user_id"', 'name="role_type"', 'value="chair"', 'value="voter"', 'value="member"',
                'data-role-type="chair"', 'data-role-type="advisor"', 'data-role-type="voter"',
                'data-role-type="member"', 'js-open-manage-modal', 'js-close-manage-modal', 'id="manageModal"',
                'id="userSelect"', 'id="roleType"', 'id="voterNote"', reverse('upload_document', args=[code]),
                reverse('create_committee_vote', args=[code]), reverse('push_to_chapter', args=[code]),
                reverse('committee_minutes_list', args=[code]), reverse('committee_attendance', args=[code]),
                reverse('manage_members', args=[code]), reverse('committee_member_permissions', args=[code]),
                reverse('committee_documents', args=[code]), reverse('vote', args=[code]),
                'Spring budget sheet', 'Avery Chairwell', 'Jonah Memberly', 'can vote', 'function openManageModal(']

    def test_home_off_is_the_old_page(self):
        html = self.page('committee_home')
        self.assertNotIn(PAGE_CSS, html)
        for marker in ('rf-cmte', 'rf-row', 'rf-stat'):
            self.assertNotIn(marker, html)
        self.assertIn('<!-- Status & Role Badges Card -->', html)
        self.assert_hooks(html, self.home_hooks())

    def test_home_on_uses_the_app_rows_and_keeps_chair_tools(self):
        self.set_flag(True)
        html = self.page('committee_home')
        self.assertIn(PAGE_CSS, html)
        self.assertIn('rf-cmte rf-cmte-home', html)
        self.assertNotIn('<!-- Status & Role Badges Card -->', html)
        self.assertIn('<span class="rf-chip is-accent">Chair</span>', html)
        self.assertIn('class="rf-stats rf-cmte-stats"', html)
        self.assertIn('class="rf-card rf-cmte-actions"', html)
        self.assert_hooks(html, self.home_hooks())

    def test_home_on_hides_chair_tools_from_a_member(self):
        self.set_flag(True)
        self.client.force_login(self.member)
        html = self.page('committee_home')
        for tool in (reverse('committee_remove_member', args=[self.committee.code]), 'js-open-manage-modal',
                     reverse('push_to_chapter', args=[self.committee.code])):
            self.assertNotIn(tool, html)

    # Minutes ----------------------------------------------------------------------

    def test_minutes_off_and_on(self):
        code = self.committee.code
        hooks = [reverse('edit_committee_minutes', args=[code, self.draft.id]),
                 reverse('create_committee_minutes', args=[code]), 'id="js-open-create-modal"',
                 'js-show-delete-modal', f'data-minutes-id="{self.draft.id}"', 'data-minutes-status="draft"',
                 'data-minutes-title="Weekly finance meeting"', 'id="deleteModal"', 'id="deleteForm"',
                 'id="createDate"', 'name="title"', 'name="date"', 'name="start_time"', 'Attendance Taken']
        html = self.page('committee_minutes_list')
        self.assertNotIn('rf-cmte', html)
        self.assert_hooks(html, hooks)
        self.set_flag(True)
        html = self.page('committee_minutes_list')
        self.assertIn('rf-cmte rf-cmte-minutes', html)
        self.assertIn('class="rf-card rf-cmte-list"', html)
        self.assertIn('<span class="rf-chip is-warning">Draft</span>', html)
        self.assert_hooks(html, hooks)

    # Documents -------------------------------------------------------------------

    def test_documents_off_and_on(self):
        code = self.committee.code
        hooks = [reverse('view_committee_document', args=[code, self.doc.id]),
                 reverse('toggle_document_publish', args=[code, self.doc.id]),
                 reverse('delete_committee_document', args=[code, self.doc.id]),
                 reverse('upload_document', args=[code]), 'js-confirm-submit', 'data-confirm-msg=',
                 'Publish to Chapter', 'Committee Only', 'Line items.', 'Weekly finance meeting',
                 reverse('edit_committee_minutes', args=[code, self.draft.id])]
        html = self.page('committee_documents')
        self.assertNotIn('rf-cmte', html)
        self.assert_hooks(html, hooks)
        self.set_flag(True)
        html = self.page('committee_documents')
        self.assertIn('rf-cmte rf-cmte-docs', html)
        self.assertIn('rf-row rf-row-group rf-cmte-doc', html)
        self.assert_hooks(html, hooks)

    # Voting -------------------------------------------------------------------------

    def vote_hooks(self):
        code = self.committee.code
        return ['js-vote-form', 'name="legislation_id"', 'name="action" value="cast_vote"', 'name="vote_choice"',
                'value="yes"', 'value="no"', 'value="abstain"', 'value="Venmo"', 'name="password"',
                'submit-vote-btn', 'js-end-vote-btn', f'id="end-vote-form-{self.open_leg.id}"',
                'name="end_vote"', 'name="recalculate_result"',
                reverse('delete_committee_vote', args=[code, self.open_leg.id]),
                reverse('committee_vote_result', args=[code, self.closed.id]),
                reverse('create_chapter_vote', args=[code, self.closed.id]), reverse('push_to_chapter', args=[code]),
                reverse('create_committee_vote', args=[code]), 'function submitVote(']

    def test_vote_off_and_on(self):
        html = self.page('vote')
        self.assertNotIn('rf-cmte', html)
        self.assert_hooks(html, self.vote_hooks())
        self.set_flag(True)
        html = self.page('vote')
        self.assertIn('rf-cmte rf-cmte-vote', html)
        self.assertEqual(html.count(' rf-cmte-choices"'), 2)
        self.assertIn(' rf-cmte-bill is-closed"', html)
        self.assert_hooks(html, self.vote_hooks())

    def test_a_ballot_cast_under_the_refresh_counts(self):
        self.set_flag(True)
        self.client.force_login(self.member)
        self.client.cookies['parliament_ui'] = 'refresh'
        self.assertIn('rf-cmte-vote', self.page('vote'))
        url = reverse('vote', args=[self.committee.code])
        response = self.client.post(url, {'action': 'cast_vote', 'legislation_id': self.open_leg.id,
                                           'vote_choice': 'yes', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CommitteeVote.objects.filter(user=self.member, legislation=self.open_leg,
                                                     vote_choice='yes').exists())
        html = self.page('vote')
        self.assertIn('You have already voted on this legislation.', html)
        self.assertIn('rf-cmte-notice is-success', html)

    def test_result_off_and_on(self):
        hooks = ['id="voteChart"', 'Vote Results for', 'Voter Breakdown', 'Jonah Memberly',
                 reverse('vote', args=[self.committee.code])]
        html = self.page('committee_vote_result', self.closed.id)
        self.assertNotIn('rf-cmte', html)
        self.assert_hooks(html, hooks)
        self.set_flag(True)
        html = self.page('committee_vote_result', self.closed.id)
        self.assertIn('rf-cmte rf-cmte-result', html)
        self.assertIn(' rf-cmte-result-card"', html)
        self.assert_hooks(html, hooks)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    either adds a class (`{% if ui_refresh %} rf-…{% endif %}`) or puts an
    include in front of the unchanged old block, closed by `{# /rf #}`."""

    TEMPLATES = ['templates/committee/committee_home.html', 'templates/committee/minutes_list.html',
                 'templates/committee/documents.html', 'templates/committee/vote.html',
                 'templates/committee/vote_result.html']
    NEW = ['templates/committee_refresh/_home_tags.html', 'templates/committee_refresh/_home_stats.html',
           'templates/committee_refresh/_home_roster.html', 'templates/committee_refresh/_home_actions.html',
           'templates/committee_refresh/_home_recent.html', 'templates/committee_refresh/_minutes_list.html',
           'templates/committee_refresh/_documents_minutes.html',
           'templates/committee_refresh/_documents_list.html', 'static/css/refresh-pages/committee.css']

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% if ui_refresh %\}\{% include "[^"]+" %\}\{% else %\}', '', text)
        text = text.replace('{% endif %}{# /rf #}', '')
        return re.sub(r'\{% if ui_refresh %\}( [\w -]+)\{% endif %\}', '', text)

    def test_every_switch_wraps_unchanged_lines(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                stripped = self.strip_switches((base / name).read_text(encoding='utf-8'))
                self.assertNotIn('ui_refresh', stripped)
                self.assertNotIn('rf-', stripped)

    def test_new_files_exist_and_carry_no_inline_script_or_style(self):
        base = Path(settings.BASE_DIR)
        for name in self.NEW:
            with self.subTest(file=name):
                text = (base / name).read_text(encoding='utf-8')
                if name.endswith('.html'):
                    self.assertNotIn('<script', text)
                    self.assertNotIn('<style', text)
                    self.assertNotIn('style="', text)
                else:
                    self.assertNotRegex(re.sub(r'/\*.*?\*/', '', text, flags=re.S), r'!\s*important')

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (Path(settings.BASE_DIR) / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
