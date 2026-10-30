"""
Mobile plan, Phase 3f — the Recruitment dashboard.

The rules are src/view/committee/recruitment.py's: access for members, chairs,
advisors and the VP; private events and candidates need can_view_private;
creating, editing and deleting events needs can_manage; check-in needs
can_take_attendance; a note is deleted by its author or a chair.

    python manage.py test src.tests.mobile.test_recruitment
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    Committee, Event, RecruitmentCandidate, RecruitmentCandidateNote, RecruitmentEvent, RecruitmentEventRSVP,
    RecruitmentMemberPermission,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/committees/RCT/recruitment/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class RecruitmentTests(TestCase):

    def setUp(self):
        cache.clear()
        self.member = make_user('rc-mem')
        self.chair = make_user('rc-chair')
        _, self.member_access, _ = make_device(self.member)
        _, self.chair_access, _ = make_device(self.chair)
        self.c = Committee.objects.create(name='Recruitment', code='RCT', is_recruitment_committee=True)
        self.c.members.add(self.member)
        self.c.chairs.add(self.chair)
        self.api = Client()

    def get(self, path='', access=None):
        return self.api.get(API + path, **bearer(access or self.member_access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, json.dumps(body or {}), content_type='application/json',
                             **bearer(access or self.member_access))

    def rec_event(self, title, days=3, **extra):
        e = Event.objects.create(title=title, description='d', created_by=self.chair,
                                 date_time=timezone.now() + timedelta(days=days))
        return RecruitmentEvent.objects.create(event=e, committee=self.c, created_by=self.chair, **extra)

    def grant(self, **perms):
        RecruitmentMemberPermission.objects.create(committee=self.c, user=self.member, **perms)

    def test_member_sees_public_events_only(self):
        self.rec_event('Open house')
        self.rec_event('Interviews', visibility='committee_only')
        self.rec_event('Last week', days=-7)
        body = self.get().json()
        self.assertEqual([e['title'] for e in body['upcoming']], ['Open house'])
        self.assertEqual([e['title'] for e in body['past']], ['Last week'])
        self.assertIsNone(body['candidates'])
        self.assertFalse(body['can_manage'])
        private = RecruitmentEvent.objects.get(event__title='Interviews')
        self.assertEqual(self.get(f'events/{private.id}/').status_code, 404)

    def test_chair_sees_everything(self):
        self.rec_event('Interviews', visibility='committee_only')
        RecruitmentCandidate.objects.create(committee=self.c, name='Alex')
        body = self.get(access=self.chair_access).json()
        self.assertEqual([e['title'] for e in body['upcoming']], ['Interviews'])
        self.assertEqual([c['name'] for c in body['candidates']], ['Alex'])
        self.assertTrue(body['can_manage'])

    def test_outsider_and_non_recruitment(self):
        out = make_user('rc-out')
        _, access, _ = make_device(out)
        self.assertEqual(self.get(access=access).json()['error'], 'no_access')
        Committee.objects.create(name='Finance', code='FIN')
        r = self.api.get('/api/mobile/v1/committees/FIN/recruitment/', **bearer(self.chair_access))
        self.assertEqual(r.status_code, 404)

    def test_create_edit_delete_event(self):
        body = {'title': 'Rush BBQ', 'date_time': '2026-11-05T18:00', 'event_type': 'rush_event',
                'attendance_type': 'signup', 'max_signups': 40}
        self.assertEqual(self.post('events/', body).json()['error'], 'forbidden')
        created = self.post('events/', body, self.chair_access)
        self.assertEqual(created.status_code, 201)
        re = RecruitmentEvent.objects.get(id=created.json()['id'])
        self.assertTrue(re.event.requires_signup)
        self.assertEqual(re.event.max_signups, 40)
        re.rsvp_reminder_enabled = True
        re.save()
        edited = self.post(f'events/{re.id}/', {**body, 'title': 'Rush cookout', 'event_type': 'bogus'},
                           self.chair_access)
        self.assertEqual(edited.json()['error'], 'invalid_choice')
        edited = self.post(f'events/{re.id}/', {**body, 'title': 'Rush cookout'}, self.chair_access)
        self.assertEqual(edited.json()['title'], 'Rush cookout')
        re.refresh_from_db()
        self.assertTrue(re.rsvp_reminder_enabled)          # the app doesn't switch the reminder off
        self.assertTrue(self.post(f'events/{re.id}/delete/', access=self.chair_access).json()['ok'])
        self.assertFalse(Event.objects.filter(id=re.event_id).exists())

    def test_rsvp_and_check_in(self):
        re = self.rec_event('Info session')
        self.assertEqual(self.post(f'events/{re.id}/rsvp/', {'status': 'maybe'}).json(), {'status': 'maybe'})
        self.assertEqual(self.get(f'events/{re.id}/').json()['my_rsvp'], 'maybe')
        self.assertEqual(self.post(f'events/{re.id}/check-in/', {'user_id': 'rc-mem', 'checked_in': True})
                         .json()['error'], 'forbidden')
        self.post(f'events/{re.id}/check-in/', {'user_id': 'rc-mem', 'checked_in': True}, self.chair_access)
        self.assertTrue(RecruitmentEventRSVP.objects.get(user=self.member).checked_in)

    def test_notes_visibility(self):
        re = self.rec_event('Info session', notes='Bring forms', notes_visibility='committee_only')
        self.assertIsNone(self.get(f'events/{re.id}/').json()['notes'])
        body = self.get(f'events/{re.id}/', self.chair_access).json()
        self.assertEqual((body['notes'], body['notes_private']), ('Bring forms', True))

    def test_candidates(self):
        self.assertEqual(self.post('candidates/', {'name': 'Sam'}).json()['error'], 'forbidden')
        self.grant(can_view_private=True)
        self.assertEqual(self.post('candidates/', {'name': ' '}).json()['error'], 'name_required')
        c = self.post('candidates/', {'name': 'Sam', 'status': 'contacted', 'assigned_to': 'rc-chair'}).json()
        self.assertEqual((c['status'], c['assigned_to']['user_id']), ('contacted', 'rc-chair'))
        self.assertEqual(self.post(f"candidates/{c['id']}/status/", {'status': 'bid'}).json()['error'], 'forbidden')
        r = self.post(f"candidates/{c['id']}/status/", {'status': 'bid'}, self.chair_access).json()
        self.assertEqual(r['status_label'], 'Bid Offered')
        note = self.post(f"candidates/{c['id']}/notes/", {'body': 'Met at BBQ'}).json()
        self.assertTrue(note['can_delete'])
        theirs = RecruitmentCandidateNote.objects.create(candidate_id=c['id'], author=self.chair, body='x')
        self.assertEqual(self.post(f"candidates/{c['id']}/notes/{theirs.id}/delete/").json()['error'], 'forbidden')
        self.assertTrue(self.post(f"candidates/{c['id']}/notes/{note['id']}/delete/").json()['ok'])
        self.assertEqual(len(self.get(f"candidates/{c['id']}/").json()['note_entries']), 1)
        self.assertTrue(self.post(f"candidates/{c['id']}/delete/", access=self.chair_access).json()['ok'])
