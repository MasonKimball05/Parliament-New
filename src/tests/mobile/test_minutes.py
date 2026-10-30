"""
Chapter minutes in the app (10-30-26, src/mobile/views_minutes.py): the same
create, write-up, roll call, link, publish and delete as the website editor.

    python manage.py test src.tests.mobile.test_minutes
"""
from unittest.mock import patch

from django.utils import timezone

from src.models import Attendance, AttendanceExcuse, ChapterMinutes, Event, MinutesMotion
from src.tests.mobile.sess_helpers import make_user
from src.tests.mobile.test_officer import _Base


class MinutesTests(_Base):

    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(title='Chapter', description='d', created_by=self.officer,
                                          date_time=timezone.now(), requires_attendance=True)
        r = self.call('post', 'officer/minutes/', {'title': 'Week 9', 'date': '2026-10-30',
                                                   'start_time': '19:00', 'event_id': self.event.id})
        self.assertEqual(r.status_code, 201, r.content)
        self.id = r.json()['minutes']['id']

    def test_create_needs_date_and_time(self):
        self.assertEqual(self.call('post', 'officer/minutes/', {'title': 'x', 'date': 'soon'}).status_code, 400)
        listed = self.call('get', 'officer/minutes/').json()
        self.assertEqual(listed['minutes'][0]['event']['id'], self.event.id)

    def test_write_up_with_a_motion(self):
        r = self.call('put', f'officer/minutes/{self.id}/sections/', {'end_time': '20:15', 'sections': [
            {'type': 'header', 'title': 'Old business'},
            {'type': 'text', 'content': 'Dues are due.'},
            {'type': 'motion', 'motion': {'motion_type': 'adjourn', 'motion_text': 'Adjourn',
                                          'author_id': 'mem-1', 'received_second': True,
                                          'vote_method': 'nonsense', 'result': 'passed'}},
            {'type': 'bogus'},
        ]})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([s['type'] for s in body['sections']], ['header', 'text', 'motion'])
        self.assertEqual(body['minutes']['end_time'], '20:15')
        motion = MinutesMotion.objects.get()
        self.assertEqual((motion.author_id, motion.vote_method), ('mem-1', 'voice'))   # bad choice → default

    def test_roll_call_syncs_attendance_and_approves_excuse(self):
        excused = make_user('mem-x')
        AttendanceExcuse.objects.create(event=self.event, user=excused, reason='Exam')
        roll = self.call('get', f'officer/minutes/{self.id}/').json()['roll_call']
        self.assertTrue(any(r['user_id'] == 'mem-1' for r in roll))
        r = self.call('put', f'officer/minutes/{self.id}/roll-call/', {'attendance': [
            {'user_id': 'mem-1', 'status': 'present'},
            {'user_id': 'mem-x', 'status': 'excused'},
            {'user_id': 'nobody', 'status': 'present'},
            {'user_id': 'off-1', 'status': 'asleep'},
        ]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.member).status, 'present')
        self.assertEqual(AttendanceExcuse.objects.get(user=excused).status, 'approved')
        self.assertFalse(Attendance.objects.filter(event=self.event, user=self.officer).exists())
        self.assertTrue(r.json()['minutes']['attendance_taken'])

    def test_link_publish_and_delete(self):
        r = self.call('post', f'officer/minutes/{self.id}/event/', {'event_id': None})
        self.assertIsNone(r.json()['minutes']['event'])
        with patch('src.view.officer.chapter_minutes.generate_minutes_pdf_buffer') as pdf:
            import io
            pdf.return_value = io.BytesIO(b'%PDF-1')
            r = self.call('post', f'officer/minutes/{self.id}/publish/', {'visibility': 'officers_only'})
            self.assertEqual(r.json()['minutes']['status'], 'published')
            self.assertEqual(self.call('post', f'officer/minutes/{self.id}/publish/', {}).status_code, 400)
        m = ChapterMinutes.objects.get(pk=self.id)
        self.assertEqual(m.published_document.visibility, 'officers_only')
        m.published_document.document.delete(save=False)
        # Officers can't delete published minutes; admins can.
        self.assertEqual(self.call('delete', f'officer/minutes/{self.id}/').status_code, 403)
        self.officer.is_admin = True
        self.officer.save(update_fields=['is_admin'])
        self.assertEqual(self.call('delete', f'officer/minutes/{self.id}/').status_code, 200)
        self.assertFalse(ChapterMinutes.objects.filter(pk=self.id).exists())

    def test_members_refused(self):
        self.assertEqual(self.call('get', 'officer/minutes/', access=self.member_access).status_code, 403)
        self.assertEqual(self.call('get', f'officer/minutes/{self.id}/pdf/', access=self.member_access).status_code, 403)

    def test_pdf(self):
        r = self.call('get', f'officer/minutes/{self.id}/pdf/')
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(r.content.startswith(b'%PDF'))
