"""
The website refresh (src/ui_refresh.py) on a committee's own chair and
officer tools (attendance, votes, members and permissions, chat settings,
uploads, Manage Committees), the education pages (dashboard, meetings, a
pledge, quiz questions / submissions / breakdown, study hours, task form),
the recruitment pages (dashboard, an event, the event and candidate forms)
and the pledge quiz pages (take, breakdown, not available).

What must hold:
* off: no page stylesheet and no `rf-ct` hooks;
* on: the stylesheet and the page's `rf-ct rf-ct-<page>` class, and the
  same links, forms, field names, ids and script hooks as off (the refresh
  only adds classes);
* marking committee attendance and adding a member still work with it on;
* the templates' switches only add classes (strip them and no `ui_refresh`
  or `rf-` is left), and the stylesheet stays plain (no !important,
  imports or remote URLs).
"""
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Attendance, Committee, CommitteeLegislation, Event, ParliamentUser, RecruitmentCandidate, RecruitmentEvent,
)
from src.models.chat import ChatChannel
from src.models.education import (
    EducationMeeting, EducationMeetingAttendance, PledgeQuizAnswer, PledgeTask, PledgeTaskCompletion,
    PledgeTaskQuestion,
)
from src.models_feature_flags import FeatureFlag

PAGE_CSS = 'css/refresh-pages/committee-tools.css'
PASSWORD = 'refresh-pages-i-12345!'

#: Attributes that carry a page's behaviour: where it links and posts, what
#: its fields are called, and what its scripts look up.
BEHAVIOUR_ATTR = re.compile(r'\s(href|action|method|name|id|for|type|data-[\w-]+|x-[\w.:-]+|@[\w.:-]+|:class|aria-label)="([^"]*)"')
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,}')
JS_CLASS = re.compile(r'class="([^"]*)"')


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}'.lower(), member_type='Member',
                    member_status='Active', email=f'{uid}@example.test'.lower())
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password(PASSWORD)
    user.save()
    return user


def behaviour(html):
    """Every behaviour attribute in <body>, plus the js-/tab-/color-preset
    style classes scripts select on, as a sorted list."""
    body = html[html.index('<body'):]
    attrs = sorted((k, TOKEN.sub('TOKEN', v)) for k, v in BEHAVIOUR_ATTR.findall(body))
    hooks = sorted(c for cls in JS_CLASS.findall(body) for c in cls.split()
                   if c.startswith(('js-', 'tab-', 'color-preset', 'add-note', 'notif-')))
    return attrs, hooks


class RefreshPagesIBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.chair = make_user('rpi-1', name='Avery Chairman', member_type='Officer', is_admin=True)
        cls.members = [make_user(f'rpi-{i}', name=n) for i, n in
                       enumerate(['Jordan Treasure', 'Riley Booker', 'Casey Minute'], start=2)]
        cls.pledge = make_user('P-RPI1AX', name='Parker Newman', member_type='Pledge')
        cls.pledge2 = make_user('P-RPI2BX', name='Sam Freshley', member_type='Pledge')

        cls.fin = Committee.objects.create(name='Finance Committee', code='RPIFIN', is_active=True)
        cls.fin.chairs.add(cls.chair)
        cls.fin.members.add(cls.chair, *cls.members)
        cls.fin.voting_members.add(cls.chair, *cls.members)
        ChatChannel.objects.create(committee=cls.fin, channel_type='committee', name='Finance Committee',
                                   created_by=cls.chair)
        cls.leg = CommitteeLegislation.objects.create(
            committee=cls.fin, title='Spring Budget 2027', description='Adopt the spring budget.',
            posted_by=cls.chair, available_at=now - timedelta(days=5), status='passed', passed=True,
            voting_closed=True, voting_ended_at=now - timedelta(days=1))
        Attendance.objects.create(committee=cls.fin, user=cls.members[0], date=timezone.localdate(),
                                  attendance_type='committee', status='present', marked_by=cls.chair,
                                  marked_at=now)

        cls.edu = Committee.objects.create(name='Education Committee', code='RPIEDU', is_active=True,
                                           is_education_committee=True)
        cls.edu.chairs.add(cls.chair)
        cls.edu.members.add(cls.chair)
        cls.quiz = PledgeTask.objects.create(
            title='Chapter History Quiz', task_type='quiz', phase='1', is_required=True, points=10,
            is_published=True, is_active=True, created_by=cls.chair, show_analysis_to_pledges=True)
        cls.empty_quiz = PledgeTask.objects.create(
            title='Officer Roles Quiz', task_type='quiz', phase='1', points=5, is_published=True,
            is_active=True, created_by=cls.chair)
        questions = [PledgeTaskQuestion.objects.create(task=cls.quiz, question_text=q, answer_hint='1839')
                     for q in ('In what year was the chapter founded?', 'Name the founding principles.')]
        for question in questions:
            PledgeQuizAnswer.objects.create(question=question, pledge=cls.pledge, answer_text='1839')
        PledgeTaskCompletion.objects.create(task=cls.quiz, pledge=cls.pledge, status='submitted')
        event = Event.objects.create(title='Pledge Meeting: Chapter History', date_time=now - timedelta(days=1),
                                     location='Chapter Room', created_by=cls.chair, is_active=True)
        cls.meeting = EducationMeeting.objects.create(event=event, committee=cls.edu, points=5)
        EducationMeetingAttendance.objects.create(meeting=cls.meeting, pledge=cls.pledge, status='present')

        cls.kai = Committee.objects.create(name='Kai Committee', code='RPIKAI', is_active=True,
                                           is_kai_committee=True)
        cls.kai.chairs.add(cls.chair)
        cls.kai.members.add(cls.chair, cls.members[1])

        cls.rec = Committee.objects.create(name='Recruitment Committee', code='RPIREC', is_active=True,
                                           is_recruitment_committee=True)
        cls.rec.chairs.add(cls.chair)
        cls.rec.members.add(cls.chair, cls.members[2])
        rush = Event.objects.create(title='Fall Info Session', date_time=now + timedelta(days=3),
                                    location='Student Union 210', created_by=cls.chair, is_active=True)
        cls.rec_event = RecruitmentEvent.objects.create(event=rush, committee=cls.rec, event_type='info_session')
        cls.candidate = RecruitmentCandidate.objects.create(committee=cls.rec, name='Alex Prospect',
                                                            status='prospect', source_event=cls.rec_event)

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.force_login(self.chair)

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

    def off_and_on(self, url, page_class, must_have=()):
        """Off: no stylesheet or hooks. On: both. Either way: the same
        behaviour attributes, and the page's own landmarks."""
        off = self.get(url)
        self.assertNotIn(PAGE_CSS, off)
        self.assertNotIn('rf-ct', off)
        self.set_flag(True)
        try:
            on = self.get(url)
        finally:
            self.set_flag(False)
        self.assertIn(PAGE_CSS, on)
        self.assertIn(f'rf-ct {page_class}"', on)
        self.assertEqual(behaviour(off), behaviour(on), url)
        for text in must_have:
            self.assertIn(text, off)
            self.assertIn(text, on)
        return on


class CommitteeToolPagesTests(RefreshPagesIBase):
    def test_attendance_and_history(self):
        code = self.fin.code
        on = self.off_and_on(reverse('committee_attendance', args=[code]), 'rf-ct-att',
                             ['name="present"', 'value="mark_attendance"', 'Jordan Treasure',
                              reverse('committee_attendance_history', args=[code])])
        self.assertIn('space-y-2 rf-ct-list"', on)
        self.off_and_on(reverse('committee_attendance_history', args=[code]), 'rf-ct-atthist',
                        ['Jordan Treasure', reverse('committee_attendance', args=[code])])

    def test_votes_and_push(self):
        code = self.fin.code
        self.off_and_on(reverse('create_committee_vote', args=[code]), 'rf-ct-newvote',
                        ['name="title"', 'name="description"', 'enctype="multipart/form-data"'])
        self.off_and_on(reverse('create_chapter_vote', args=[code, self.leg.id]), 'rf-ct-chvote',
                        ['Spring Budget 2027'])
        self.off_and_on(reverse('push_to_chapter', args=[code]), 'rf-ct-push',
                        ['name="legislation_id"', 'js-confirm-submit', 'Spring Budget 2027'])

    def test_members_permissions_and_upload(self):
        code = self.fin.code
        on = self.off_and_on(reverse('manage_members', args=[code]), 'rf-ct-members',
                             ['value="remove_member"', 'value="add_member"', 'name="user_id"'])
        self.assertIn('space-y-3 rf-ct-list"', on)
        self.off_and_on(reverse('committee_member_permissions', args=[code]), 'rf-ct-perms',
                        ['Member Permissions', 'type="checkbox"'])
        self.off_and_on(reverse('upload_document', args=[code]), 'rf-ct-upload',
                        ['name="file"', 'name="document_type"', 'id="publish_to_chapter"',
                         reverse('committee_documents', args=[code])])

    def test_chat_settings_and_guests(self):
        code = self.fin.code
        self.off_and_on(reverse('edit_committee_chat_settings', args=[code]), 'rf-ct-chatset',
                        ['name="icon"', 'name="color"', 'color-preset'])
        self.off_and_on(reverse('manage_chat_permissions', args=[code]), 'rf-ct-chatperm',
                        ['Chat Guest Permissions'])

    def test_manage_and_create_committees(self):
        self.off_and_on(reverse('manage_committees'), 'rf-ct-cmtes',
                        ['Finance Committee', 'id="add-committee-modal"'])
        self.off_and_on(reverse('create_committee'), 'rf-ct-newcmte',
                        ['name="name"', 'name="code"', 'name="initial_chairs"', 'id="create-form"'])

    def test_permission_pages(self):
        self.off_and_on(reverse('manage_kai_permissions', args=[self.kai.code]), 'rf-ct-kaiperm',
                        ['Kai Member Permissions'])
        self.off_and_on(reverse('manage_education_permissions', args=[self.edu.code]), 'rf-ct-eduperm',
                        ['Education Member Permissions'])
        self.off_and_on(reverse('manage_recruitment_permissions', args=[self.rec.code]), 'rf-ct-recperm',
                        ['Recruitment Permissions'])

    def test_marking_attendance_with_the_refresh_on_still_works(self):
        self.set_flag(True)
        url = reverse('committee_attendance', args=[self.fin.code])
        self.assertIn('rf-ct-att', self.get(url))
        response = self.client.post(url, {'action': 'mark_attendance',
                                          'present': [self.members[1].user_id, self.members[2].user_id]})
        self.assertEqual(response.status_code, 302)
        present = set(Attendance.objects.filter(committee=self.fin, attendance_type='committee',
                                                date=timezone.localdate()).values_list('user_id', flat=True))
        self.assertEqual(present, {self.members[1].user_id, self.members[2].user_id})

    def test_saving_chat_settings_with_the_refresh_on_still_works(self):
        self.set_flag(True)
        url = reverse('edit_committee_chat_settings', args=[self.fin.code])
        self.assertIn('rf-ct-chatset', self.get(url))
        response = self.client.post(url, {'icon': '\U0001F4B0', 'color': '#10B981'})
        self.assertEqual(response.status_code, 302)
        channel = ChatChannel.objects.get(committee=self.fin, channel_type='committee')
        self.assertEqual((channel.icon, channel.color.upper()), ('\U0001F4B0', '#10B981'))


class EducationPagesTests(RefreshPagesIBase):
    def test_dashboard_and_study_hours(self):
        code = self.edu.code
        on = self.off_and_on(reverse('education_home', args=[code]), 'rf-ct-edu',
                             ['Pledge Tracker', 'Parker Newman', 'Chapter History Quiz'])
        self.assertIn('shadow-lg rf-ct-band"', on)
        self.off_and_on(reverse('education_study_hours', args=[code]), 'rf-ct-edu-study', ['Study Hours'])

    def test_meeting_pages(self):
        code = self.edu.code
        self.off_and_on(reverse('education_meeting_attendance', args=[code, self.meeting.pk]), 'rf-ct-edu-att',
                        ['Pledge Meeting: Chapter History', 'Parker Newman'])
        self.off_and_on(reverse('education_edit_meeting', args=[code, self.meeting.pk]), 'rf-ct-edu-meeting',
                        ['Edit meeting'])

    def test_pledge_and_task_pages(self):
        code = self.edu.code
        self.off_and_on(reverse('education_pledge_detail', args=[code, self.pledge.pk]), 'rf-ct-edu-pledge',
                        ['Parker Newman', 'Chapter History Quiz'])
        self.off_and_on(reverse('education_edit_task', args=[code, self.quiz.pk]), 'rf-ct-edu-task',
                        ['name="title"', 'Edit task'])

    def test_quiz_pages(self):
        code = self.edu.code
        self.off_and_on(reverse('education_manage_quiz_questions', args=[code, self.quiz.pk]),
                        'rf-ct-edu-questions', ['name="question_text"', 'In what year was the chapter founded?'])
        self.off_and_on(reverse('education_quiz_submissions', args=[code, self.quiz.pk]), 'rf-ct-edu-subs',
                        ['Parker Newman', reverse('education_quiz_analysis', args=[code, self.quiz.pk])])
        self.off_and_on(reverse('education_quiz_analysis', args=[code, self.quiz.pk]), 'rf-ct-edu-analysis',
                        ['Question breakdown'])


class RecruitmentPagesTests(RefreshPagesIBase):
    def test_dashboard_and_event(self):
        code = self.rec.code
        on = self.off_and_on(reverse('recruitment_dashboard', args=[code]), 'rf-ct-rec',
                             ['data-tab="events"', 'data-tab="candidates"', 'Fall Info Session', 'Alex Prospect',
                              reverse('create_recruitment_event', args=[code])])
        self.assertIn('flex gap-6 rf-ct-tabs" aria-label="Tabs"', on)
        self.off_and_on(reverse('recruitment_event_detail', args=[code, self.rec_event.id]), 'rf-ct-rec-event',
                        ['Fall Info Session', reverse('edit_recruitment_event', args=[code, self.rec_event.id])])

    def test_forms(self):
        code = self.rec.code
        on = self.off_and_on(reverse('create_recruitment_event', args=[code]), 'rf-ct-rec-eventform',
                             ['name="title"', 'name="event_type"'])
        self.assertIn('space-y-5 rf-ct-formcard"', on)
        self.off_and_on(reverse('edit_recruitment_event', args=[code, self.rec_event.id]), 'rf-ct-rec-eventform',
                        ['Fall Info Session'])
        self.off_and_on(reverse('create_candidate', args=[code]), 'rf-ct-rec-cand', ['name="name"'])
        self.off_and_on(reverse('edit_candidate', args=[code, self.candidate.id]), 'rf-ct-rec-cand',
                        ['Alex Prospect'])


class PledgeQuizPagesTests(RefreshPagesIBase):
    def test_take_quiz(self):
        self.client.force_login(self.pledge2)
        self.off_and_on(reverse('pledge_take_quiz', args=[self.quiz.pk]), 'rf-ct-quiz',
                        ['In what year was the chapter founded?', reverse('my_pledge_tasks')])

    def test_breakdown_and_not_available(self):
        self.client.force_login(self.pledge)
        self.off_and_on(reverse('pledge_quiz_analysis', args=[self.quiz.pk]), 'rf-ct-quiz-analysis',
                        ['Chapter History Quiz'])
        self.off_and_on(reverse('pledge_take_quiz', args=[self.empty_quiz.pk]), 'rf-ct-quiz-na',
                        ['Quiz Not Available', reverse('my_pledge_tasks')])


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    adds classes (`{% if ui_refresh %} rf-…{% endif %}`) to a class
    attribute the page already had."""

    TEMPLATES = ['committee/' + name + '.html' for name in (
        'attendance', 'attendance_history', 'create_chapter_vote', 'create_committee', 'create_vote', 'detail',
        'edit_chat_settings', 'manage_chat_permissions', 'manage_committees', 'manage_members',
        'member_permissions', 'manage_education_permissions', 'manage_kai_permissions',
        'manage_recruitment_permissions', 'push_to_chapter', 'upload_document', 'education_dashboard',
        'education_meeting_attendance', 'education_meeting_form', 'education_pledge_detail',
        'education_quiz_analysis', 'education_quiz_questions', 'education_quiz_submissions',
        'education_study_hours', 'education_task_form', 'recruitment_candidate_form',
        'recruitment_candidate_list', 'recruitment_dashboard', 'recruitment_event_detail',
        'recruitment_event_form')] + ['pledge/take_quiz.html', 'pledge/quiz_analysis.html',
                                      'pledge/quiz_not_available.html']

    def test_every_switch_only_adds_classes(self):
        base = Path(settings.BASE_DIR) / 'templates'
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                text = (base / name).read_text(encoding='utf-8')
                self.assertIn('{% if ui_refresh %} rf-ct rf-ct-', text)
                # every switch sits inside a class attribute
                for switch in re.findall(r'[^\s"]*\{% if ui_refresh %\}[^"]*"', text):
                    self.assertRegex(switch, r'^[^"]*\{% if ui_refresh %\}( [\w-]+)+\{% endif %\}"$')
                stripped = re.sub(r'\{% if ui_refresh %\}( [\w -]+)\{% endif %\}', '', text)
                self.assertNotIn('ui_refresh', stripped)
                self.assertNotRegex(stripped, r'\brf-')

    def test_stylesheet_is_plain(self):
        css = (Path(settings.BASE_DIR) / 'static' / PAGE_CSS).read_text(encoding='utf-8')
        body = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        self.assertNotRegex(body, r'!\s*important')
        self.assertNotIn('@import', body)
        self.assertNotIn('http', body)
        self.assertEqual(body.count('{'), body.count('}'))

    def test_base_links_the_stylesheet_only_with_the_switch(self):
        text = (Path(settings.BASE_DIR) / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
