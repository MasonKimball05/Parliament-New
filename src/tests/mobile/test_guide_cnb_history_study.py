"""
The app's user guide, C&B section history / "as of", study hours and event
time (src/mobile/views_guide.py, views_cnb.py, views_study_hours.py): the same
pages, permissions and side effects as the website's.

    python manage.py test src.tests.mobile.test_guide_cnb_history_study
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src import location_time as lt
from src.markdown_blocks import blocks_plain_text
from src.models import (
    ActivityLog, Article, Attendance, Committee, EducationMemberPermission, Event, GoverningDocument, GuideArticle,
    LocationTimeAdjustment, LocationVisit, PledgePageRestriction, Resolution, Section, SectionRevision,
    StudyHoursRequirement,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag
from src.tests.mobile.test_location_checkin import SPOT
from src.view.cnb_history import word_diff, word_diff_segments
from src.view.guide import GUIDE_PAGES

API = '/api/mobile/v1/'


class _Api(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()

    def signed_in(self, user):
        _, access, _ = make_device(user)
        return bearer(access)

    def get(self, path, auth):
        return self.api.get(API + path, **auth)

    def post(self, path, body, auth):
        return self.api.post(API + path, body, content_type='application/json', **auth)


# ── The user guide ──────────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class GuideTests(_Api):

    def setUp(self):
        super().setUp()
        self.member = make_user('g-mem')
        self.officer = make_user('g-off', member_type='Officer')
        self.m = self.signed_in(self.member)
        self.o = self.signed_in(self.officer)

    def test_index_lists_the_web_cards_and_hides_the_developer_guide_from_members(self):
        r = self.get('guide/', self.m)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([s['key'] for s in body['sections']], ['officer', 'member'])
        keys = [p['key'] for s in body['sections'] for p in s['pages']]
        self.assertIn('guide_events', keys)
        self.assertIn('guide_chat', keys)
        self.assertNotIn('guide_developer_handoff', keys)
        chat = next(p for s in body['sections'] for p in s['pages'] if p['key'] == 'guide_chat')
        self.assertEqual(chat['web_path'], reverse('guide_chat'))
        self.assertIn('message', chat['keywords'])
        officer_keys = [p['key'] for s in self.get('guide/', self.o).json()['sections'] for p in s['pages']]
        self.assertIn('guide_developer_handoff', officer_keys)
        self.assertEqual(len(officer_keys), len(GUIDE_PAGES))

    def test_every_registry_page_matches_its_web_view(self):
        # The registry drives the web views: each still renders its template.
        web = Client()
        web.force_login(self.officer)
        for key, page in GUIDE_PAGES.items():
            r = web.get(reverse(key))
            self.assertEqual(r.status_code, 200, key)
            if page['template']:
                self.assertEqual(r.templates[0].name, page['template'], key)
                self.assertEqual(r.context['page_title'], page['page_title'], key)

    def test_a_static_page_comes_as_blocks_without_the_site_chrome(self):
        r = self.get('guide/pages/guide_chat/', self.m)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['heading'], 'Chat Channels')
        self.assertTrue(body['subtitle'])
        text = blocks_plain_text(body['blocks'])
        self.assertIn('1. What are Chat Channels?', text)
        self.assertNotIn('<', text)
        self.assertNotIn('Breadcrumb', text)
        # No breadcrumb list ("Guide" > "Chat Channels") at the top.
        self.assertNotEqual(body['blocks'][0]['type'], 'list')
        # Site links survive for the app to open natively.
        hrefs = [run.get('href') for b in body['blocks'] if b['type'] == 'paragraph' for run in b['text']]
        self.assertIn('/chats/', hrefs)

    def test_handoff_docs_render_and_the_developer_one_is_for_officers(self):
        r = self.get('guide/pages/guide_officer_handoff/', self.m)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(any(b['type'] == 'heading' for b in r.json()['blocks']))
        self.assertEqual(self.get('guide/pages/guide_developer_handoff/', self.m).status_code, 403)
        self.assertEqual(self.get('guide/pages/guide_developer_handoff/', self.o).status_code, 200)

    def test_unknown_pages_404(self):
        self.assertEqual(self.get('guide/pages/guide_nope/', self.m).status_code, 404)
        self.assertEqual(self.get('guide/pages/home/', self.m).status_code, 404)   # a url name, not a guide page

    def test_pledge_restrictions_follow_each_page(self):
        pledge = make_user('g-pl', member_type='Pledge', pledge_phase='1')
        p = self.signed_in(pledge)
        PledgePageRestriction.objects.create(url_name='guide_kai', allowed_phases=[])
        self.assertEqual(self.get('guide/pages/guide_kai/', p).status_code, 403)
        self.assertEqual(self.get('guide/pages/guide_chat/', p).status_code, 200)

    def test_articles_are_published_only_and_sanitized(self):
        a = GuideArticle.objects.create(title='Rush week', slug='rush-week', category='member', summary='How',
                                        content='<p>Hello <script>x()</script><b>there</b></p>')
        GuideArticle.objects.create(title='Draft', slug='draft', category='member', content='x', is_published=False)
        GuideArticle.objects.create(title='Also', slug='also', category='member', content='<p>y</p>')
        index = self.get('guide/', self.m).json()
        self.assertEqual(sorted(x['slug'] for x in index['articles']), ['also', 'rush-week'])
        r = self.get(f'guide/articles/{a.slug}/', self.m)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(blocks_plain_text(body['blocks']), 'Hello x()there')   # the web's bleach strips the tag
        self.assertEqual([x['slug'] for x in body['related']], ['also'])
        self.assertEqual(self.get('guide/articles/draft/', self.m).status_code, 404)


# ── C&B history and "as of" ─────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class CnbHistoryTests(_Api):

    def setUp(self):
        super().setUp()
        flag('cnb_constitution', True)
        flag('cnb_bylaws', False)
        self.member = make_user('h-mem')
        self.chair = make_user('h-chair', member_type='Officer', is_admin=True)
        self.m = self.signed_in(self.member)
        self.con = GoverningDocument.objects.create(doc_type='constitution', title='Constitution', display_order=1)
        art = Article.objects.create(document=self.con, number='I', title='Name', display_order=1)
        self.sec = Section.objects.create(article=art, number='1', title='Name', display_order=1,
                                          content='The chapter is called Beta.')
        Section.objects.filter(pk=self.sec.pk).update(created_at=None)
        self.res = Resolution.objects.create(title='Rename', created_by=self.chair)
        now = timezone.now()
        self.older = SectionRevision.objects.create(
            section=self.sec, content='The chapter is Alpha.', replaced_at=now - timedelta(days=30),
            source='direct_edit', replaced_by=self.chair, note='typo')
        self.newer = SectionRevision.objects.create(
            section=self.sec, content='The chapter is called Alpha.', replaced_at=now - timedelta(days=5),
            source='resolution', resolution=self.res)

    def test_segments_match_the_web_diff(self):
        segs = word_diff_segments('a b c', 'a x c')
        self.assertEqual(segs, [('equal', 'a '), ('delete', 'b'), ('insert', 'x'), ('equal', ' c')])
        self.assertEqual(word_diff('a <b>', 'a <i>').count('&lt;'), 2)
        self.assertEqual(word_diff_segments(None, 'new'), [('insert', 'new')])

    def test_history_newest_first_with_what_replaced_each(self):
        r = self.get(f'cnb/sections/{self.sec.pk}/history/', self.m)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['section']['content'], 'The chapter is called Beta.')
        self.assertEqual(body['anchor'], 'constitution-art-I-sec-1')
        v = body['versions']
        self.assertEqual([x['id'] for x in v], [self.newer.pk, self.older.pk])
        self.assertEqual(v[0]['resolution'], {'id': self.res.pk, 'title': 'Rename'})
        self.assertEqual(v[1]['replaced_by'], self.chair.name)
        self.assertEqual((v[1]['source'], v[1]['note']), ('direct_edit', 'typo'))
        # Each diff runs from that version to the text that replaced it.
        self.assertEqual([s for s in v[0]['diff'] if s['op'] != 'equal'],
                         [{'op': 'delete', 'text': 'Alpha.'}, {'op': 'insert', 'text': 'Beta.'}])
        self.assertEqual([s for s in v[1]['diff'] if s['op'] != 'equal'], [{'op': 'insert', 'text': 'called '}])
        # Parity with the page's versions.
        web = Client()
        web.force_login(self.member)
        page = web.get(reverse('cnb_section_history', args=[self.sec.pk]))
        self.assertEqual([x['rev'].pk for x in page.context['versions']], [x['id'] for x in v])

    def test_history_of_a_disabled_document_is_for_cnb_holders_only(self):
        bylaws = GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws', display_order=2)
        art = Article.objects.create(document=bylaws, number='I', title='Dues', display_order=1)
        sec = Section.objects.create(article=art, number='1', content='Pay.', display_order=1)
        self.assertEqual(self.get(f'cnb/sections/{sec.pk}/history/', self.m).status_code, 404)
        self.assertEqual(self.get(f'cnb/sections/{sec.pk}/history/', self.signed_in(self.chair)).status_code, 200)
        self.assertEqual(self.get('cnb/sections/999999/history/', self.m).status_code, 404)

    def test_as_of_shows_the_text_then_and_saves_nothing(self):
        day = (timezone.localdate() - timedelta(days=10)).isoformat()
        r = self.get(f'cnb/?as_of={day}', self.m)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['as_of'], day)
        sec = body['documents'][0]['articles'][0]['sections'][0]
        self.assertEqual(sec['content'], 'The chapter is called Alpha.')
        self.assertTrue(sec['changed_since'])
        self.sec.refresh_from_db()
        self.assertEqual(self.sec.content, 'The chapter is called Beta.')
        current = self.get('cnb/', self.m).json()
        self.assertIsNone(current['as_of'])
        self.assertFalse(current['documents'][0]['articles'][0]['sections'][0]['changed_since'])

    def test_as_of_hides_sections_added_later(self):
        new = Section.objects.create(article=self.sec.article, number='2', content='New.', display_order=2)
        day = (timezone.localdate() - timedelta(days=3)).isoformat()
        Section.objects.filter(pk=new.pk).update(created_at=timezone.now() - timedelta(days=1))
        numbers = [s['number'] for s in self.get(f'cnb/?as_of={day}', self.m).json()
                   ['documents'][0]['articles'][0]['sections']]
        self.assertEqual(numbers, ['1'])

    def test_bad_as_of_is_invalid_input(self):
        for bad in ('yesterday', '2026-02-31', (timezone.localdate() + timedelta(days=2)).isoformat()):
            r = self.get(f'cnb/?as_of={bad}', self.m)
            self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(r.json()['error'], 'invalid_input')


# ── Study hours and event time ──────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class StudyHoursTests(_Api):

    def setUp(self):
        super().setUp()
        self.officer = make_user('sh-off', member_type='Officer')
        self.member = make_user('sh-mem')
        self.pledge = make_user('sh-pl', member_type='Pledge')
        self.edu = Committee.objects.create(name='Education', code='EDU', is_education_committee=True,
                                            is_active=True)
        self.o = self.signed_in(self.officer)
        self.m = self.signed_in(self.member)
        self.now = timezone.now()
        self.study = Event.objects.create(
            title='Library hours', description='d', created_by=self.officer, is_study_hours=True,
            date_time=self.now - timedelta(hours=1), duration_minutes=180,
            checkin_latitude=SPOT[0], checkin_longitude=SPOT[1], checkin_radius_m=100)
        LocationVisit.objects.create(event=self.study, user=self.pledge,
                                     entered_at=self.study.date_time, left_at=self.study.date_time + timedelta(minutes=45))
        self.week = timezone.localtime(self.study.date_time).date().isoformat()

    def path(self):
        return f'committees/EDU/education/study-hours/?week={self.week}'

    def test_week_needs_view_or_adjust_permission(self):
        self.assertEqual(self.get(self.path(), self.m).status_code, 404)
        EducationMemberPermission.objects.create(committee=self.edu, user=self.member, can_view_submissions=True)
        r = self.get(self.path(), self.m)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertFalse(body['can_set_requirements'])
        self.assertEqual(body['without_requirement'], [])
        self.assertIsNone(body['events'][0]['time_path'])                 # can't adjust time

    def test_week_matches_the_web_page(self):
        EducationMemberPermission.objects.create(committee=self.edu, user=self.member, can_adjust_time=True)
        StudyHoursRequirement.objects.create(user=self.pledge, weekly_minutes=90, set_by=self.officer)
        body = self.get(self.path(), self.m).json()
        web = Client()
        web.force_login(self.member)
        ctx = web.get(reverse('education_study_hours', args=['EDU']), {'week': self.week}).context
        self.assertEqual([(r['user_id'], r['minutes'], r['required_minutes'], r['percent']) for r in body['rows']],
                         [(r['member'].pk, r['minutes'], r['required'], r['percent']) for r in ctx['rows']])
        self.assertEqual(body['rows'][0]['minutes'], 45)
        self.assertEqual(body['rows'][0]['percent'], 50)
        self.assertEqual(body['week_start'], ctx['week_start'].date().isoformat())
        self.assertEqual([e['id'] for e in body['events']], [e.id for e in ctx['events']])
        self.assertEqual(body['events'][0]['time_path'], reverse('event_time', args=[self.study.id]))
        self.assertEqual(sorted(m['user_id'] for m in body['without_requirement']),
                         sorted(m.pk for m in ctx['without_requirement']))

    def test_bad_week_is_invalid_input(self):
        EducationMemberPermission.objects.create(committee=self.edu, user=self.member, can_view_submissions=True)
        for bad in ('soon', '2026-02-31'):
            r = self.get(f'committees/EDU/education/study-hours/?week={bad}', self.m)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), bad)

    def test_setting_requirements(self):
        url = 'committees/EDU/education/study-hours/requirements/'
        EducationMemberPermission.objects.create(committee=self.edu, user=self.member, can_view_submissions=True)
        self.assertEqual(self.post(url, {'user_id': self.pledge.pk, 'hours': 5}, self.m).status_code, 403)
        self.assertFalse(StudyHoursRequirement.objects.exists())
        EducationMemberPermission.objects.filter(user=self.member).update(can_adjust_time=True)
        r = self.post(url, {'user_id': self.pledge.pk, 'hours': '2.5'}, self.m)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['required_minutes'], 150)
        req = StudyHoursRequirement.objects.get(user=self.pledge)
        self.assertEqual((req.weekly_minutes, req.set_by), (150, self.member))
        for bad in (0, 81, 'lots'):
            r = self.post(url, {'user_id': self.pledge.pk, 'hours': bad}, self.m)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), bad)
        self.assertEqual(StudyHoursRequirement.objects.get(user=self.pledge).weekly_minutes, 150)
        r = self.post(url, {'user_id': self.pledge.pk, 'hours': None}, self.m)
        self.assertEqual(r.json()['required_minutes'], None)
        self.assertFalse(StudyHoursRequirement.objects.exists())
        self.assertEqual(self.post(url, {'user_id': 'nobody', 'hours': 2}, self.m).status_code, 404)

    def test_event_time_is_for_those_who_can_adjust_on_timed_events(self):
        url = f'officer/events/{self.study.id}/time/'
        self.assertEqual(self.get(url, self.m).status_code, 404)
        plain = Event.objects.create(title='Social', description='d', created_by=self.officer,
                                     date_time=self.now)
        self.assertEqual(self.get(f'officer/events/{plain.id}/time/', self.o).status_code, 404)
        r = self.get(url, self.o)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['event']['is_study_hours'])
        row = body['rows'][0]
        self.assertEqual((row['user_id'], row['minutes'], row['inside'], row['unconfirmed']),
                         (self.pledge.pk, 45, False, False))
        self.assertEqual(len(row['visits']), 1)
        self.assertNotIn(self.pledge.pk, [m['user_id'] for m in body['others']])
        self.assertIn(self.member.pk, [m['user_id'] for m in body['others']])
        # Parity with the page.
        web = Client()
        web.force_login(self.officer)
        ctx = web.get(reverse('event_time', args=[self.study.id])).context
        self.assertEqual([(r['member'].pk, r['minutes']) for r in ctx['rows']],
                         [(r['user_id'], r['minutes']) for r in body['rows']])
        self.assertEqual([m.pk for m in ctx['others']], [m['user_id'] for m in body['others']])

    def test_adjusting_time_logs_and_needs_a_reason(self):
        url = f'officer/events/{self.study.id}/time/'
        r = self.post(url, {'user_id': self.pledge.pk, 'minutes': 15, 'direction': 'add', 'reason': 'Phone died'},
                      self.o)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['adjusted'], {'user_id': self.pledge.pk, 'name': self.pledge.get_display_name(),
                                            'minutes': 60})
        self.assertEqual(body['rows'][0]['adjustments'][0]['minutes'], 15)
        self.assertEqual(body['rows'][0]['adjustments'][0]['adjusted_by'], self.officer.get_display_name())
        self.assertTrue(ActivityLog.objects.filter(description__contains='Added 15 min').exists())
        r = self.post(url, {'user_id': self.pledge.pk, 'minutes': '20', 'direction': 'remove', 'reason': 'Left early'},
                      self.o)
        self.assertEqual(r.json()['adjusted']['minutes'], 40)
        self.assertEqual(LocationTimeAdjustment.objects.get(minutes=-20).reason, 'Left early')
        before = LocationTimeAdjustment.objects.count()
        for bad in ({'user_id': self.pledge.pk, 'minutes': 5, 'reason': ''},
                    {'user_id': self.pledge.pk, 'minutes': 0, 'reason': 'x'},
                    {'user_id': self.pledge.pk, 'minutes': 721, 'reason': 'x'},
                    {'user_id': self.pledge.pk, 'minutes': -5, 'reason': 'x'},
                    {'user_id': self.pledge.pk, 'minutes': 'ten', 'reason': 'x'},
                    {'user_id': self.pledge.pk, 'minutes': 5, 'reason': 'x', 'direction': 'sideways'},
                    {'user_id': '', 'minutes': 5, 'reason': 'x'},
                    {'user_id': 'nobody', 'minutes': 5, 'reason': 'x'}):
            r = self.post(url, bad, self.o)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), bad)
        self.assertEqual(LocationTimeAdjustment.objects.count(), before)
        self.assertEqual(self.post(url, {'user_id': self.pledge.pk, 'minutes': 5, 'reason': 'x'}, self.m).status_code,
                         404)

    def test_adding_time_to_a_minimum_stay_event_marks_present(self):
        meeting = Event.objects.create(
            title='Chapter', description='d', created_by=self.officer, requires_attendance=True,
            date_time=self.now, checkin_min_minutes=30,
            checkin_latitude=SPOT[0], checkin_longitude=SPOT[1], checkin_radius_m=100)
        r = self.post(f'officer/events/{meeting.id}/time/',
                      {'user_id': self.member.pk, 'minutes': 30, 'reason': 'Was there'}, self.o)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['rows'][0]['met'])
        self.assertEqual(Attendance.objects.get(event=meeting, user=self.member).status, 'present')
        self.assertEqual(lt.minutes(self.member, meeting), 30)

    def test_officer_attendance_links_the_time_screen(self):
        flag('attendance_tracking', True)
        flag('event_attendance', True)
        meeting = Event.objects.create(
            title='Chapter', description='d', created_by=self.officer, requires_attendance=True,
            date_time=self.now, checkin_min_minutes=30)
        r = self.get(f'officer/events/{meeting.id}/attendance/', self.o)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['event']['time_path'], reverse('event_time', args=[meeting.id]))
        meeting.checkin_min_minutes = None
        meeting.save()
        self.assertIsNone(self.get(f'officer/events/{meeting.id}/attendance/', self.o).json()['event']['time_path'])
