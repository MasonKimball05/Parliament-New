"""
11-10-26: clicking an applicant's name opens one page about them, not just
the application: every interview in full (time, place, panel, strengths,
concerns, notes, recommendation, positions), "Recommended" / "Not
recommended" on the positions they asked for, their slate spots and the
period's history for the application. Same access as the notes themselves.
"""
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.models import SlatingActivity, SlatingInterview, SlatingPosition
from src.tests.slating.test_slating_review_fixes import _Base, member


class ApplicantProfileTests(_Base):

    def setUp(self):
        super().setUp()
        self.treas = SlatingPosition.objects.create(period=self.period, title='Treasurer', code='T')
        self.app.position_preferences = {'first_choice': [self.pres.id], 'second_choice': [self.vp.id],
                                         'third_choice': [], 'do_not_want': []}
        self.app.save()
        self.url = reverse('slating_app_detail', args=[self.period.id, self.app.id])

    def get(self, user=None):
        self.client.force_login(user or self.committee_member)
        return self.client.get(self.url)

    def interview(self, **kw):
        panelist = kw.pop('panel', None)
        positions = kw.pop('positions', [])
        i = SlatingInterview.objects.create(application=self.app, **kw)
        if panelist:
            i.interviewers.add(panelist)
        i.recommended_positions.set(positions)
        return i

    def test_shows_the_whole_interview(self):
        panelist = member('sr-panel')
        self.interview(scheduled_at=timezone.now() - timedelta(days=1), completed_at=timezone.now(),
                       location='Room 204', panel=panelist, notes='Line one\nLine two of the notes ' + 'word ' * 80 + 'THE END',
                       strengths='Organised', concerns='Busy semester', recommendation='recommend',
                       positions=[self.pres, self.treas])
        r = self.get()
        self.assertContains(r, 'Room 204')
        self.assertContains(r, 'Member sr-panel')
        self.assertContains(r, 'Organised')
        self.assertContains(r, 'Busy semester')
        self.assertContains(r, 'Line two of the notes')
        self.assertContains(r, 'THE END')  # the 50-word truncation is gone
        self.assertContains(r, 'Recommend')
        self.assertContains(r, 'Edit notes')
        ctx = r.context
        self.assertEqual(ctx['recommended_ids'], {self.pres.id, self.treas.id})
        self.assertEqual(ctx['extra_recommended'], [self.treas])  # didn't apply for it
        html = r.content.decode()
        self.assertEqual(html.count('>Recommended</span>'), 1)       # President
        self.assertEqual(html.count('>Not recommended</span>'), 1)   # VP

    def test_no_badges_until_positions_are_marked(self):
        self.interview(scheduled_at=timezone.now())
        r = self.get()
        self.assertNotContains(r, 'Not recommended</span>')
        self.assertContains(r, 'Record notes')
        self.assertContains(r, 'Scheduled')

    def test_destroyed_notes_stay_hidden(self):
        i = self.interview(completed_at=timezone.now(), notes='SECRET', strengths='SECRET2')
        i.destroy_notes(self.manager)
        r = self.get()
        self.assertNotContains(r, 'SECRET')
        self.assertContains(r, 'Notes destroyed per bylaws')
        self.assertNotContains(r, 'Edit notes')

    def test_slate_spot_and_history(self):
        SlatingActivity.objects.create(period=self.period, user=self.manager, action='application_reviewed',
                                       details='Status changed to under review',
                                       metadata={'application_id': self.app.id})
        SlatingActivity.objects.create(period=self.period, action='application_reviewed',
                                       details='SOMEONE ELSE', metadata={'application_id': self.app.id + 999})
        r = self.get()
        self.assertEqual(r.context['slate_spots'], [self.c_pres])
        self.assertContains(r, 'Status changed to under review')
        self.assertNotContains(r, 'SOMEONE ELSE')

    def test_no_interview(self):
        r = self.get()
        self.assertContains(r, 'No interview yet.')
        self.assertIsNone(r.context['latest_interview'])

    def test_site_admin_on_locked_period_is_still_refused(self):
        self.interview(completed_at=timezone.now(), notes='CONFIDENTIAL')
        r = self.get(self.admin)
        self.assertNotContains(r, 'CONFIDENTIAL', status_code=302)

    def test_queries_do_not_grow_with_interviews(self):
        def count():
            self.client.force_login(self.committee_member)
            self.client.get(self.url)  # warm per-request caches (saving models clears some)
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(self.url)
            return len(ctx.captured_queries)
        self.interview(completed_at=timezone.now(), panel=self.manager, positions=[self.pres])
        one = count()
        for _ in range(3):
            self.interview(completed_at=timezone.now(), panel=self.manager, positions=[self.vp])
        n = count()
        self.assertEqual(n, one)
