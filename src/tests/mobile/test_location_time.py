"""
Timed location check-in (10-30-26, src/location_time.py): a minimum stay before
a member is marked present, and study hours counted from time at a study place,
with manual adjustments.

    python manage.py test src.tests.mobile.test_location_time
"""
from datetime import timedelta

from django.utils import timezone

from src import location_time as lt
from src.models import (
    Attendance, Committee, EducationMemberPermission, Event, LocationTimeAdjustment, LocationVisit,
    StudyHoursRequirement,
)
from src.tests.mobile.sess_helpers import make_user
from src.tests.mobile.test_location_checkin import API, FAR, NEAR, SPOT, _Base


class MinimumStayTests(_Base):

    def setUp(self):
        super().setUp()
        self.event.checkin_min_minutes = 60
        self.event.save()
        self.key_ = self.key()

    def leave(self, event=None):
        return self.api.post(API + f'events/{(event or self.event).id}/checkin/location/leave/',
                             content_type='application/json', HTTP_X_CHECKIN_KEY=self.key_)

    def test_arriving_starts_a_visit_but_does_not_check_in(self):
        r = self.checkin(key=self.key_)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['timed'] and body['inside'])
        self.assertEqual((body['minutes'], body['min_minutes'], body['checked_in']), (0, 60, False))
        self.assertFalse(Attendance.objects.filter(event=self.event, user=self.user).exists())
        self.checkin(key=self.key_)                                     # a second "enter" opens no second visit
        self.assertEqual(LocationVisit.objects.filter(event=self.event, user=self.user).count(), 1)

    def test_far_away_is_refused(self):
        self.assertEqual(self.checkin(point=FAR, key=self.key_).status_code, 403)
        self.assertFalse(LocationVisit.objects.exists())

    def test_time_adds_up_across_visits_and_credits_at_the_minimum(self):
        start = self.event.date_time
        LocationVisit.objects.create(event=self.event, user=self.user, entered_at=start,
                                     left_at=start + timedelta(minutes=40))
        visit = LocationVisit.objects.create(event=self.event, user=self.user,
                                             entered_at=start + timedelta(minutes=50))
        self.assertFalse(lt.credit(None, self.user, self.event, now=start + timedelta(minutes=55)))   # 45 min
        credited, closed = lt.tick(now=start + timedelta(minutes=71))                                # 40 + 21
        self.assertEqual((credited, closed), (1, 0))
        a = Attendance.objects.get(event=self.event, user=self.user)
        self.assertEqual(a.status, 'present')
        self.assertIn('61 min', a.notes)
        visit.refresh_from_db()
        self.assertIsNone(visit.left_at)                                # still inside

    def test_leaving_closes_the_visit(self):
        self.checkin(key=self.key_)
        r = self.leave()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['inside'])
        self.assertIsNotNone(LocationVisit.objects.get().left_at)

    def test_an_officers_marking_stays(self):
        Attendance.objects.create(event=self.event, user=self.user, attendance_type='event', status='absent')
        LocationVisit.objects.create(event=self.event, user=self.user, entered_at=self.event.date_time,
                                     left_at=self.event.date_time + timedelta(hours=2))
        self.assertFalse(lt.credit(None, self.user, self.event))
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.user).status, 'absent')

    def test_unconfirmed_visit_closes_at_the_window(self):
        LocationVisit.objects.create(event=self.event, user=self.user, entered_at=self.event.date_time)
        _, closes = lt.window(self.event)
        lt.tick(now=closes + timedelta(minutes=5))
        v = LocationVisit.objects.get()
        self.assertEqual(v.left_at, closes)
        self.assertTrue(v.left_unconfirmed)

    def test_adjustment_credits_and_needs_a_reason(self):
        with self.assertRaises(ValueError):
            lt.adjust(None, self.user, self.event, 60, '', self.officer)
        total = lt.adjust(None, self.user, self.event, 60, 'Phone died; was there', self.officer)
        self.assertEqual(total, 60)
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.user).status, 'present')
        self.assertEqual(LocationTimeAdjustment.objects.get().adjusted_by, self.officer)

    def test_areas_list_says_timed(self):
        from django.test import Client
        from src.tests.mobile.sess_helpers import bearer
        rows = Client().get(API + 'events/checkin-areas/', **bearer(self.access)).json()['events']
        row = next(r for r in rows if r['id'] == self.event.id)
        self.assertTrue(row['timed'])
        self.assertEqual(row['min_minutes'], 60)


class StudyHoursTests(_Base):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.study = Event.objects.create(
            title='Library hours', description='d', created_by=self.officer, is_study_hours=True,
            date_time=now - timedelta(hours=1), duration_minutes=180,
            checkin_latitude=SPOT[0], checkin_longitude=SPOT[1], checkin_radius_m=100)

    def test_study_hours_count_inside_the_window_only(self):
        start = self.study.date_time
        LocationVisit.objects.create(event=self.study, user=self.user, entered_at=start - timedelta(minutes=30),
                                     left_at=start + timedelta(minutes=45))
        self.assertEqual(lt.minutes(self.user, self.study, now=start + timedelta(hours=1)), 45)
        week = lt.study_week(self.user)
        self.assertEqual(week['minutes'], 45)
        self.assertIsNone(week['required_minutes'])
        StudyHoursRequirement.objects.create(user=self.user, weekly_minutes=300)
        self.assertEqual(lt.study_week(self.user)['required_minutes'], 300)

    def test_study_hours_never_mark_attendance(self):
        r = self.checkin(event=self.study)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['study_hours'])
        lt.tick(now=self.study.date_time + timedelta(hours=3, minutes=1))
        self.assertFalse(Attendance.objects.filter(event=self.study).exists())


class AdjustPermissionTests(_Base):

    def test_who_can_adjust(self):
        member = make_user('adj-mem')
        self.assertTrue(lt.can_adjust(self.officer))
        self.assertFalse(lt.can_adjust(member))
        edu = Committee.objects.create(name='Education', code='EDU', is_education_committee=True)
        EducationMemberPermission.objects.create(committee=edu, user=member, can_adjust_time=True)
        self.assertTrue(lt.can_adjust(member))
        vp = make_user('adj-vp')
        edu.chairs.add(vp)
        self.assertTrue(lt.can_adjust(vp))


class WebPagesTests(_Base):

    def setUp(self):
        super().setUp()
        self.event.checkin_min_minutes = 30
        self.event.save()
        now = timezone.now()
        LocationVisit.objects.create(event=self.event, user=self.user, entered_at=now - timedelta(minutes=20),
                                     left_at=now - timedelta(minutes=10))
        self.edu = Committee.objects.create(name='Education', code='EDU', is_education_committee=True)

    def test_event_time_page_adjusts(self):
        from django.urls import reverse
        url = reverse('event_time', args=[self.event.id])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 404)          # a member can't
        self.client.force_login(self.officer)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['rows'][0]['minutes'], 10)
        self.client.post(url, {'user_id': self.user.pk, 'minutes': 25, 'direction': 'add', 'reason': 'Phone died'})
        self.assertEqual(lt.minutes(self.user, self.event), 35)
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.user).status, 'present')

    def test_study_hours_page_and_requirements(self):
        from django.urls import reverse
        url = reverse('education_study_hours', args=['EDU'])
        helper = make_user('sh-helper')
        self.client.force_login(helper)
        self.assertEqual(self.client.get(url).status_code, 404)
        EducationMemberPermission.objects.create(committee=self.edu, user=helper, can_adjust_time=True)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'user_id': self.user.pk, 'hours': '5'})
        self.assertEqual(StudyHoursRequirement.objects.get(user=self.user).weekly_minutes, 300)
        self.client.post(url, {'user_id': self.user.pk, 'hours': ''})
        self.assertFalse(StudyHoursRequirement.objects.filter(user=self.user).exists())
