"""
Batch 6: the C&B manager and resolution notes (src/mobile/views_cnb_manage.py),
changing a password and browser sessions (views_account_security.py) and the
officer sign-up list (views_calendar.EventSignupListView): the same
permissions, validation and side effects as the website's pages, through the
same helpers.

Minutes needed nothing new: create / edit / publish for chapter and committee
minutes were already in the app (test_minutes, test_committee_minutes).

    python manage.py test src.tests.mobile.test_cnb_minutes_account_batch6
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ActivityLog, Article, Event, EventSignup, GoverningDocument, Resolution, ResolutionCollaborator,
    ResolutionNote, Role, Section, SectionRevision, UserSession,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'


class _Api(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()

    def signed_in(self, user):
        _, access, _ = make_device(user)
        return bearer(access)

    def call(self, method, path, auth, body=None):
        fn = getattr(self.api, method)
        if body is None:
            return fn(API + path, **auth)
        return fn(API + path, body, content_type='application/json', **auth)


def _cnb_holder(uid):
    user = make_user(uid)
    role, _ = Role.objects.get_or_create(code='CNB', defaults={'name': 'C&B Chair'})
    user.roles.add(role)
    return user


# ── The C&B manager ──────────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class CnbManagerTests(_Api):

    def setUp(self):
        super().setUp()
        flag('cnb_constitution', True)
        flag('cnb_foreword', False)
        self.chair = _cnb_holder('b6-chair')
        self.member = make_user('b6-mem')
        self.c = self.signed_in(self.chair)
        self.m = self.signed_in(self.member)
        self.con = GoverningDocument.objects.create(doc_type='constitution', title='Constitution', display_order=10)
        GoverningDocument.objects.create(doc_type='foreword', title='Foreword', preamble='Draft.')
        self.art = Article.objects.create(document=self.con, number='I', title='Name', display_order=1)
        self.sec = Section.objects.create(article=self.art, number='1', title='Name', content='Old text.',
                                          display_order=1)

    def test_members_are_refused_and_chairs_see_every_document(self):
        r = self.call('get', 'cnb/manage/', self.m)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'forbidden')
        r = self.call('get', 'cnb/manage/', self.c)
        self.assertEqual(r.status_code, 200, r.content)
        docs = {d['doc_type']: d for d in r.json()['documents']}
        # The manager edits switched-off documents too (manage_document), marked.
        self.assertEqual(set(docs), {'constitution', 'foreword'})
        self.assertTrue(docs['constitution']['enabled'])
        self.assertFalse(docs['foreword']['enabled'])
        self.assertEqual(docs['constitution']['articles'][0]['sections'][0]['content'], 'Old text.')
        # Admins have it too, as on the website.
        admin = make_user('b6-admin', is_admin=True)
        self.assertEqual(self.call('get', 'cnb/manage/', self.signed_in(admin)).status_code, 200)

    def test_a_pledge_restriction_on_the_web_page_applies(self):
        self.chair.member_type = 'Pledge'
        self.chair.save()
        restrict('cnb_manage_document')
        self.assertEqual(self.call('get', 'cnb/manage/', self.c).json()['error'], 'pledge_restricted')

    def test_editing_a_section_records_history_like_the_website(self):
        r = self.call('patch', f'cnb/manage/sections/{self.sec.id}/', self.c, {'content': ' New text. ', 'title': 'N'})
        self.assertEqual(r.status_code, 200, r.content)
        self.sec.refresh_from_db()
        self.assertEqual((self.sec.content, self.sec.title), ('New text.', 'N'))
        rev = SectionRevision.objects.get(section=self.sec)
        self.assertEqual((rev.content, rev.title, rev.source, rev.replaced_by), ('Old text.', 'Name', 'direct_edit',
                                                                                 self.chair))
        # The app's history screen shows it.
        h = self.call('get', f'cnb/sections/{self.sec.id}/history/', self.c).json()
        self.assertEqual(h['versions'][0]['content'], 'Old text.')
        # No change, no revision; no title keeps the title (the web form's default).
        self.call('patch', f'cnb/manage/sections/{self.sec.id}/', self.c, {'content': 'New text.'})
        self.assertEqual(SectionRevision.objects.filter(section=self.sec).count(), 1)
        self.sec.refresh_from_db()
        self.assertEqual(self.sec.title, 'N')

    def test_empty_content_is_refused(self):
        r = self.call('patch', f'cnb/manage/sections/{self.sec.id}/', self.c, {'content': '  '})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'error': 'invalid_input', 'detail': 'Section content cannot be empty.'})
        self.assertFalse(SectionRevision.objects.exists())

    def test_the_website_edit_goes_through_the_same_helper(self):
        web = Client()
        web.force_login(self.chair)
        r = web.post(reverse('cnb_edit_section', args=[self.sec.id]), {'content': 'Web text.', 'title': 'Name'})
        self.assertEqual(r.status_code, 302)
        self.sec.refresh_from_db()
        self.assertEqual(self.sec.content, 'Web text.')
        self.assertEqual(SectionRevision.objects.get(section=self.sec).content, 'Old text.')

    def test_suspending_a_section_needs_a_reason_and_is_history(self):
        path = f'cnb/manage/sections/{self.sec.id}/active/'
        r = self.call('post', path, self.c, {'active': False})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['detail'], 'A reason is required to deactivate a section.')
        self.assertEqual(self.call('post', path, self.c, {'active': 'no'}).status_code, 400)
        r = self.call('post', path, self.c, {'active': False, 'reason': 'IFC ruling'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['section']['active'])
        self.sec.refresh_from_db()
        self.assertEqual((self.sec.deactivation_reason, self.sec.deactivated_by), ('IFC ruling', self.chair))
        self.assertEqual(SectionRevision.objects.get(section=self.sec).note, 'Deactivated: IFC ruling')
        # Asking again for the state it's in changes nothing (a double tap can't flip it back).
        self.call('post', path, self.c, {'active': False, 'reason': 'again'})
        self.assertEqual(SectionRevision.objects.filter(section=self.sec).count(), 1)
        self.call('post', path, self.c, {'active': True})
        self.sec.refresh_from_db()
        self.assertTrue(self.sec.is_active)
        self.assertEqual(SectionRevision.objects.filter(section=self.sec).first().note, 'Reactivated')

    def test_adding_articles_and_sections(self):
        r = self.call('post', 'cnb/manage/documents/constitution/articles/', self.c, {'number': 'II', 'title': 'Purpose'})
        self.assertEqual(r.status_code, 201, r.content)
        art = Article.objects.get(document=self.con, number='II')
        self.assertEqual(art.display_order, 2)
        r = self.call('post', 'cnb/manage/documents/constitution/articles/', self.c, {'number': 'II', 'title': 'X'})
        self.assertEqual(r.json()['detail'], 'Article II already exists.')
        self.assertEqual(self.call('post', 'cnb/manage/documents/constitution/articles/', self.c,
                                   {'number': 'III'}).status_code, 400)
        self.assertEqual(self.call('post', 'cnb/manage/documents/nope/articles/', self.c,
                                   {'number': 'I', 'title': 'x'}).status_code, 404)

        r = self.call('post', f'cnb/manage/articles/{self.art.id}/sections/', self.c,
                      {'number': '2', 'title': '', 'content': 'Second.'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Section.objects.get(article=self.art, number='2').display_order, 2)
        r = self.call('post', f'cnb/manage/articles/{self.art.id}/sections/', self.c, {'number': '2', 'content': 'x'})
        self.assertEqual(r.json()['detail'], 'Section 2 already exists in Article I.')
        r = self.call('post', f'cnb/manage/articles/{self.art.id}/sections/', self.c, {'number': 'x' * 21, 'content': 'x'})
        self.assertEqual(r.status_code, 400)

    def test_renaming_an_article_is_logged(self):
        path = f'cnb/manage/articles/{self.art.id}/'
        self.assertEqual(self.call('patch', path, self.c, {'title': ' '}).status_code, 400)
        r = self.call('patch', path, self.c, {'title': 'Name'})
        self.assertFalse(r.json()['changed'])
        r = self.call('patch', path, self.c, {'title': 'Name and Purpose'})
        self.assertTrue(r.json()['changed'])
        self.art.refresh_from_db()
        self.assertEqual(self.art.title, 'Name and Purpose')
        log = ActivityLog.objects.get(metadata__action='cnb_rename_article')
        self.assertEqual((log.metadata['old'], log.metadata['new'], log.user), ('Name', 'Name and Purpose', self.chair))

    def test_suspending_an_article(self):
        path = f'cnb/manage/articles/{self.art.id}/active/'
        self.assertEqual(self.call('post', path, self.c, {'active': False}).status_code, 400)
        r = self.call('post', path, self.c, {'active': False, 'reason': 'Ruling'})
        self.assertFalse(r.json()['article']['active'])
        self.art.refresh_from_db()
        self.assertEqual((self.art.deactivation_reason, self.art.deactivated_by), ('Ruling', self.chair))
        self.call('post', path, self.c, {'active': True})
        self.art.refresh_from_db()
        self.assertTrue(self.art.is_active)
        self.assertEqual(self.art.deactivation_reason, '')

    def test_partial_suspensions(self):
        path = f'cnb/manage/sections/{self.sec.id}/partials/'
        self.assertEqual(self.call('post', path, self.c, {'ref': 'a'}).status_code, 400)
        r = self.call('post', path, self.c, {'ref': 'a', 'reason': 'Review'})
        self.assertEqual(r.status_code, 201, r.content)
        p = r.json()['section']['partial_suspensions'][0]
        self.assertEqual((p['index'], p['ref'], p['reason'], p['suspended_by']), (0, 'a', 'Review', self.chair.name))
        self.assertEqual(p['suspended_at'], timezone.localdate().isoformat())
        self.assertEqual(self.call('delete', f'{path}5/', self.c).status_code, 404)
        self.assertEqual(self.call('delete', f'{path}0/', self.c).status_code, 200)
        self.sec.refresh_from_db()
        self.assertEqual(self.sec.partial_suspensions, [])

    def test_a_struck_section_is_not_editable(self):
        self.sec.removed_at = timezone.now()
        self.sec.former_number = '1'
        self.sec.save()
        self.assertEqual(self.call('patch', f'cnb/manage/sections/{self.sec.id}/', self.c,
                                   {'content': 'x'}).status_code, 404)

    def test_members_cannot_write(self):
        for method, path, body in (
            ('patch', f'cnb/manage/sections/{self.sec.id}/', {'content': 'x'}),
            ('post', f'cnb/manage/sections/{self.sec.id}/active/', {'active': False, 'reason': 'x'}),
            ('post', f'cnb/manage/articles/{self.art.id}/sections/', {'number': '9', 'content': 'x'}),
            ('patch', f'cnb/manage/articles/{self.art.id}/', {'title': 'x'}),
        ):
            self.assertEqual(self.call(method, path, self.m, body).status_code, 403, path)
        self.sec.refresh_from_db()
        self.assertEqual(self.sec.content, 'Old text.')


# ── Resolution notes ─────────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class ResolutionNoteTests(_Api):

    def setUp(self):
        super().setUp()
        self.chair = _cnb_holder('b6n-chair')
        self.author = make_user('b6n-author')
        self.collab = make_user('b6n-collab')
        self.outsider = make_user('b6n-out')
        self.res = Resolution.objects.create(title='On Notes', status='draft', created_by=self.author)
        ResolutionCollaborator.objects.create(resolution=self.res, user=self.collab, role='viewer')
        self.base = f'cnb/resolutions/{self.res.id}/notes/'

    def test_outsiders_get_a_404_and_the_detail_says_who_can_use_notes(self):
        out = self.signed_in(self.outsider)
        self.assertEqual(self.call('get', self.base, out).status_code, 404)
        self.assertEqual(self.call('post', self.base, out, {'body': 'hi'}).status_code, 404)
        self.assertFalse(self.call('get', f'cnb/resolutions/{self.res.id}/', out).json()['can_use_notes'])
        self.assertTrue(self.call('get', f'cnb/resolutions/{self.res.id}/',
                                  self.signed_in(self.collab)).json()['can_use_notes'])

    def test_the_working_group_adds_edits_and_ticks_off_notes(self):
        c = self.signed_in(self.collab)
        r = self.call('post', self.base, c, {'body': ' Check the name ', 'color': 'blue', 'anchor': 'preamble'})
        self.assertEqual(r.status_code, 201, r.content)
        note = ResolutionNote.objects.get()
        self.assertEqual((note.body, note.color, note.anchor, note.created_by), ('Check the name', 'blue', 'preamble',
                                                                                self.collab))
        # Unknown colour / anchor fall back, as on the website.
        self.call('post', self.base, c, {'body': 'Two', 'color': 'red', 'anchor': 'nowhere'})
        two = ResolutionNote.objects.get(body='Two')
        self.assertEqual((two.color, two.anchor), ('yellow', ''))

        a = self.signed_in(self.author)
        r = self.call('patch', f'{self.base}{note.id}/', a, {'body': 'Check the title'})
        self.assertEqual(r.json()['note']['edited_by'], self.author.get_display_name())
        note.refresh_from_db()
        self.assertEqual((note.body, note.color, note.anchor), ('Check the title', 'blue', 'preamble'))
        r = self.call('post', f'{self.base}{note.id}/done/', a)
        self.assertTrue(r.json()['note']['is_done'])
        listed = self.call('get', self.base, c).json()
        self.assertEqual([n['body'] for n in listed['notes']], ['Two', 'Check the title'])   # open first
        self.assertEqual(listed['max_length'], ResolutionNote.MAX_LENGTH)
        self.assertEqual(self.call('patch', f'{self.base}{note.id}/', a, {'body': ''}).status_code, 400)
        r = self.call('post', self.base, c, {'body': 'x' * (ResolutionNote.MAX_LENGTH + 1)})
        self.assertEqual(r.json()['detail'], f'Notes are limited to {ResolutionNote.MAX_LENGTH} characters.')

    def test_only_the_writer_the_author_or_a_chair_deletes(self):
        mine = ResolutionNote.objects.create(resolution=self.res, body='a', created_by=self.chair)
        c = self.signed_in(self.collab)
        listed = self.call('get', self.base, c).json()['notes']
        self.assertFalse(listed[0]['can_delete'])
        r = self.call('delete', f'{self.base}{mine.id}/', c)
        self.assertEqual(r.status_code, 403)
        self.assertIn('mark it done', r.json()['detail'])
        self.assertTrue(ResolutionNote.objects.filter(pk=mine.pk).exists())
        self.assertEqual(self.call('delete', f'{self.base}{mine.id}/', self.signed_in(self.author)).status_code, 200)
        self.assertFalse(ResolutionNote.objects.exists())

    def test_a_note_on_another_resolution_is_a_404(self):
        other = Resolution.objects.create(title='Other', status='draft', created_by=self.chair)
        note = ResolutionNote.objects.create(resolution=other, body='a', created_by=self.chair)
        self.assertEqual(self.call('patch', f'{self.base}{note.id}/', self.signed_in(self.collab),
                                   {'body': 'b'}).status_code, 404)


# ── Changing a password ──────────────────────────────────────────────────────

NEW_PW = 'Fresh-Horse-Battery-71'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class ChangePasswordTests(_Api):

    def setUp(self):
        super().setUp()
        self.user = make_user('b6-pw')
        self.device, access, self.refresh = make_device(self.user)
        self.me = bearer(access)
        self.other_device, other_access, _ = make_device(self.user, name='Old phone')
        self.other = bearer(other_access)
        UserSession.objects.create(user=self.user, session_key='b6-browser-1', device_type='desktop')

    def change(self, old, new1, new2=None):
        return self.call('post', 'account/password/', self.me,
                         {'old_password': old, 'new_password1': new1, 'new_password2': new1 if new2 is None else new2})

    def test_a_wrong_current_password_is_refused_with_field_errors(self):
        r = self.change('nope', NEW_PW)
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertEqual(body['error'], 'invalid_input')
        self.assertIn('old_password', body['fields'])
        self.assertNotIn(NEW_PW, r.content.decode())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('pw-b6-pw'))

    def test_django_validators_apply(self):
        r = self.change('pw-b6-pw', NEW_PW, NEW_PW + 'x')
        self.assertIn('new_password2', r.json()['fields'])
        r = self.change('pw-b6-pw', '123')
        self.assertEqual(r.status_code, 400)
        self.assertIn('new_password2', r.json()['fields'])

    def test_success_keeps_this_install_and_signs_out_everything_else(self):
        r = self.change('pw-b6-pw', NEW_PW)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'ok': True})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PW))
        # This install: its fingerprint follows the password, so it stays in.
        self.device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)
        self.assertEqual(self.device.still_allowed(self.user), (True, ''))
        self.assertEqual(self.call('get', 'devices/', self.me).status_code, 200)
        # Its refresh token still works.
        r = self.api.post(API + 'auth/refresh/', {'refresh_token': self.refresh}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        # Every other install and browser session is signed out, as on the website.
        self.other_device.refresh_from_db()
        self.assertIsNotNone(self.other_device.revoked_at)
        self.assertEqual(self.call('get', 'devices/', self.other).status_code, 401)
        self.assertFalse(UserSession.objects.filter(user=self.user).exists())
        log = ActivityLog.objects.get(action_type='password_changed')
        self.assertEqual(log.user, self.user)
        self.assertNotIn(NEW_PW, log.description)

    def test_five_attempts_an_hour_shared_with_the_website(self):
        web = Client()
        web.force_login(self.user)
        for _ in range(3):
            web.post(reverse('change_password'), {'old_password': 'x', 'new_password1': 'y', 'new_password2': 'y'})
        for _ in range(2):
            self.assertEqual(self.change('x', NEW_PW).status_code, 400)
        r = self.change('pw-b6-pw', NEW_PW)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['error'], 'too_many_attempts')
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password(NEW_PW))

    def test_the_website_change_still_keeps_its_session(self):
        web = Client()
        web.force_login(self.user)
        r = web.post(reverse('change_password'),
                     {'old_password': 'pw-b6-pw', 'new_password1': NEW_PW, 'new_password2': NEW_PW})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(web.get(reverse('change_password')).status_code, 200)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.revoked_at)   # app installs signed out, as before
        self.assertEqual(ActivityLog.objects.filter(action_type='password_changed').count(), 1)


# ── Browser sessions ─────────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class SessionTests(_Api):

    def setUp(self):
        super().setUp()
        self.user = make_user('b6-ses')
        self.device, access, _ = make_device(self.user)
        self.me = bearer(access)
        self.other_device, _, _ = make_device(self.user, name='Tablet')
        now = timezone.now()
        # Real sessions: the list sweeps rows whose session has gone.
        self.keys = [self.live_session_key() for _ in range(3)]
        self.old = UserSession.objects.create(user=self.user, session_key=self.keys[0], browser='Safari',
                                              device_type='desktop', ip_address='10.0.0.1')
        self.new = UserSession.objects.create(user=self.user, session_key=self.keys[1], browser='Chrome',
                                              device_type='mobile')
        UserSession.objects.filter(pk=self.old.pk).update(last_activity=now - timedelta(hours=2))
        stranger = make_user('b6-ses-x')
        self.theirs = UserSession.objects.create(user=stranger, session_key=self.keys[2])

    @staticmethod
    def live_session_key():
        from importlib import import_module
        from django.conf import settings
        store = import_module(settings.SESSION_ENGINE).SessionStore()
        store.create()
        return store.session_key

    def test_lists_own_sessions_newest_first_without_keys(self):
        r = self.call('get', 'account/sessions/', self.me)
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()['sessions']
        self.assertEqual([s['id'] for s in rows], [self.new.id, self.old.id])
        self.assertEqual(rows[1]['browser'], 'Safari')
        for key in self.keys:
            self.assertNotIn(key, r.content.decode())

    def test_revoke_one(self):
        self.assertEqual(self.call('post', f'account/sessions/{self.theirs.id}/revoke/', self.me).status_code, 404)
        r = self.call('post', f'account/sessions/{self.old.id}/revoke/', self.me)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(UserSession.objects.filter(pk=self.old.pk).exists())
        self.assertTrue(UserSession.objects.filter(pk=self.theirs.pk).exists())

    def test_revoke_all_keeps_this_install(self):
        r = self.call('post', 'account/sessions/revoke-all/', self.me)
        self.assertEqual(r.json(), {'ok': True, 'revoked_count': 2})
        self.assertFalse(UserSession.objects.filter(user=self.user).exists())
        self.device.refresh_from_db()
        self.other_device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)
        self.assertEqual(self.other_device.revoked_reason, 'signed out everywhere')
        self.assertEqual(self.call('get', 'account/sessions/', self.me).status_code, 200)


# ── Event sign-up list ───────────────────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class EventSignupListTests(_Api):

    def setUp(self):
        super().setUp()
        self.officer = make_user('b6-off', member_type='Officer')
        self.member = make_user('b6-evm')
        self.o = self.signed_in(self.officer)
        self.event = Event.objects.create(title='Formal', description='d', created_by=self.officer,
                                          date_time=timezone.now() + timedelta(days=3), requires_signup=True,
                                          max_signups=1, allow_waitlist=True)
        EventSignup.objects.create(event=self.event, user=self.member)
        self.waiter = make_user('b6-wait')
        EventSignup.objects.create(event=self.event, user=self.waiter, waitlist_position=1)
        gone = make_user('b6-gone')
        EventSignup.objects.create(event=self.event, user=gone, is_cancelled=True, cancelled_at=timezone.now())

    def test_officers_see_the_list_like_the_page(self):
        r = self.call('get', f'events/{self.event.id}/signups/', self.o)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([s['name'] for s in body['signups']], [self.member.get_display_name()])
        self.assertEqual([s['waitlist_position'] for s in body['waitlist']], [1])
        self.assertEqual(len(body['cancelled']), 1)
        self.assertEqual((body['signup_count'], body['waitlist_count']), (1, 1))
        self.assertEqual(body['web_path'], reverse('event_signup_list', args=[self.event.id]))
        # Same rows as the website's context.
        web = Client()
        web.force_login(self.officer)
        ctx = web.get(reverse('event_signup_list', args=[self.event.id])).context
        self.assertEqual([s.id for s in ctx['signups']], [s['id'] for s in body['signups']])
        self.assertEqual([s.id for s in ctx['waitlist']], [s['id'] for s in body['waitlist']])

    def test_members_are_refused_and_the_calendar_toggle_applies(self):
        r = self.call('get', f'events/{self.event.id}/signups/', self.signed_in(self.member))
        self.assertEqual(r.status_code, 403)
        toggle('calendar', False)
        r = self.call('get', f'events/{self.event.id}/signups/', self.o)
        self.assertEqual(r.json()['error'], 'page_disabled')

    def test_an_event_without_sign_ups_is_a_404(self):
        e = Event.objects.create(title='Chapter', description='d', created_by=self.officer,
                                 date_time=timezone.now() + timedelta(days=1))
        self.assertEqual(self.call('get', f'events/{e.id}/signups/', self.o).status_code, 404)
