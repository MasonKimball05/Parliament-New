"""
Committee minutes in the app (v4.3.0, src/mobile/views_committee_minutes.py):
the committee editor's create, write-up, roll call, publish and delete, with
the website's permissions (members start minutes and edit their own drafts;
chairs and minute-takers edit any, publish and delete; admins delete
published ones).

    python manage.py test src.tests.mobile.test_committee_minutes
"""
import io
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import Attendance, ChapterMinutes, Committee, CommitteePermissions, MinutesMotion
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class CommitteeMinutesTests(TestCase):

    def setUp(self):
        cache.clear()
        self.member = make_user('cmm-1')
        _, self.member_access, _ = make_device(self.member)
        self.chair = make_user('cmm-chair')
        _, self.chair_access, _ = make_device(self.chair)
        self.outsider = make_user('cmm-out')
        _, self.outsider_access, _ = make_device(self.outsider)
        self.c = Committee.objects.create(name='Finance', code='FIN')
        self.c.members.add(self.member)
        self.c.chairs.add(self.chair)
        self.api = Client()
        r = self.call('post', 'committees/FIN/minutes/',
                      {'title': 'Week 9', 'date': '2026-10-30', 'start_time': '19:00'}, self.member_access)
        self.assertEqual(r.status_code, 201, r.content)
        self.id = r.json()['minutes']['id']
        self.base = f'committees/FIN/minutes/{self.id}/'

    def call(self, method, path, body=None, access=None):
        fn = getattr(self.api, method)
        kw = bearer(access or self.chair_access)
        if body is None:
            return fn(API + path, **kw)
        return fn(API + path, json.dumps(body), content_type='application/json', **kw)

    def test_list_offers_the_editor(self):
        body = self.call('get', 'committees/FIN/minutes/', access=self.member_access).json()
        self.assertTrue(body['can_create'])
        self.assertTrue(body['minutes'][0]['can_edit'])           # the member's own draft
        self.assertEqual(self.call('post', 'committees/FIN/minutes/', {'title': 'x'}).status_code, 400)
        self.assertEqual(self.call('post', 'committees/FIN/minutes/', {'title': 'x', 'date': '2026-10-30',
                                                                       'start_time': '19:00'},
                                   self.outsider_access).status_code, 403)

    def test_detail_is_the_committees_roll_call(self):
        body = self.call('get', self.base).json()
        self.assertEqual({r['user_id'] for r in body['roll_call']}, {'cmm-1', 'cmm-chair'})
        self.assertEqual(body['events'], [])
        self.assertTrue(body['can_publish'])
        self.assertNotIn('custom', [c['value'] for c in body['choices']['visibility']])
        # The member may edit their own draft but not publish or delete it.
        mine = self.call('get', self.base, access=self.member_access).json()
        self.assertFalse(mine['can_publish'])
        self.assertFalse(mine['can_delete'])
        self.assertEqual(self.call('get', self.base, access=self.outsider_access).status_code, 403)

    def test_write_up_and_roll_call(self):
        r = self.call('put', self.base + 'sections/', {'end_time': '20:15', 'sections': [
            {'type': 'header', 'title': 'Budget'},
            {'type': 'motion', 'motion': {'motion_type': 'adjourn', 'motion_text': 'Adjourn',
                                          'author_id': 'cmm-1', 'vote_method': 'nonsense'}},
        ]}, self.member_access)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([s['type'] for s in r.json()['sections']], ['header', 'motion'])
        self.assertEqual(MinutesMotion.objects.get().vote_method, 'voice')
        r = self.call('put', self.base + 'roll-call/', {'attendance': [
            {'user_id': 'cmm-1', 'status': 'present'},
            {'user_id': 'cmm-out', 'status': 'present'},       # not on the committee
            {'user_id': 'cmm-chair', 'status': 'asleep'},
        ]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Attendance.objects.get(user=self.member, committee=self.c,
                                                date=timezone.localdate()).status, 'present')
        self.assertFalse(Attendance.objects.filter(user=self.outsider).exists())
        self.assertTrue(r.json()['minutes']['attendance_taken'])

    def test_another_members_draft_is_not_theirs(self):
        other = make_user('cmm-2')
        self.c.members.add(other)
        _, access, _ = make_device(other)
        self.assertEqual(self.call('put', self.base + 'sections/', {'sections': []}, access).status_code, 403)
        # A designated minute-taker may edit any.
        CommitteePermissions.objects.create(committee=self.c, user=other, can_take_minutes=True)
        self.assertEqual(self.call('put', self.base + 'sections/', {'sections': []}, access).status_code, 200)

    def test_publish_and_delete(self):
        self.assertEqual(self.call('post', self.base + 'publish/', {}, self.member_access).status_code, 403)
        with patch('src.view.committee.committee_minutes_editor.generate_minutes_pdf_buffer') as pdf:
            pdf.return_value = io.BytesIO(b'%PDF-1')
            self.assertEqual(self.call('post', self.base + 'publish/', {'visibility': 'custom'}).status_code, 400)
            r = self.call('post', self.base + 'publish/', {'visibility': 'chairs_only', 'publish_to_chapter': True})
            self.assertEqual(r.json()['minutes']['status'], 'published')
        m = ChapterMinutes.objects.get(pk=self.id)
        self.assertEqual((m.published_document.visibility, m.published_document.published_to_chapter),
                         ('chairs_only', True))
        m.published_document.document.delete(save=False)
        # Chairs can't delete published minutes; admins can.
        self.assertEqual(self.call('delete', self.base).status_code, 403)
        self.chair.is_admin = True
        self.chair.save(update_fields=['is_admin'])
        self.assertEqual(self.call('delete', self.base).status_code, 200)
        self.assertFalse(ChapterMinutes.objects.filter(pk=self.id).exists())

    def test_pdf(self):
        with patch('src.mobile.views_committee_minutes.generate_minutes_pdf_buffer') as pdf:
            pdf.return_value = io.BytesIO(b'%PDF-1')
            r = self.call('get', self.base + 'pdf/', access=self.member_access)
        self.assertEqual(r['Content-Type'], 'application/pdf')
