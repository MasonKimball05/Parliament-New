"""
Service hours review in the app (src/mobile/views_service_review.py).

The app's dashboard, list and review must be the website's VPP pages: the
same numbers (shared ``dashboard_data`` / ``filtered_submissions``), the same
writes and activity rows (``review_submission`` / ``bulk_review_submissions``),
and ``vpp_required``'s rule for who may use them (the VPP role or an admin —
not every officer).

Until its routes are in src/mobile/urls.py, this module is its own urlconf.

    python manage.py test src.tests.mobile.test_service_review
"""
import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from src.mobile import views_service_review as v
from src.mobile.views_officer import OfficerHubView
from src.models import (
    ServiceActivity, ServiceFieldResponse, ServiceFormField, ServiceHoursAdjustment, ServiceHoursSubmission,
    ServiceMemberExpectation, ServicePeriod,
)
from src.models.users import Role
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict

API = '/api/mobile/v1/'

_routes = [
    path('officer/', OfficerHubView.as_view(), name='mobile_officer_hub'),
    path('officer/service-hours/', v.ServiceDashboardView.as_view(), name='mobile_officer_service'),
    path('officer/service-hours/submissions/', v.ServiceSubmissionListView.as_view(),
         name='mobile_officer_service_submissions'),
    path('officer/service-hours/submissions/bulk/', v.ServiceSubmissionBulkView.as_view(),
         name='mobile_officer_service_bulk'),
    path('officer/service-hours/submissions/<int:submission_id>/', v.ServiceSubmissionView.as_view(),
         name='mobile_officer_service_submission'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_svc')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        today = timezone.localdate()
        self.period = ServicePeriod.objects.create(
            name='Fall', start_date=today - timedelta(days=30), end_date=today + timedelta(days=30),
            default_hours_required=Decimal('10'))
        self.old = ServicePeriod.objects.create(
            name='Spring', start_date=today - timedelta(days=200), end_date=today - timedelta(days=100),
            default_hours_required=Decimal('5'))
        vpp = Role.objects.create(name='VP Programming', code='VPP')
        self.vpp = make_user('svc-vpp', name='Vic Vpp', member_type='Officer')
        self.vpp.roles.add(vpp)
        _, self.access, _ = make_device(self.vpp)
        self.officer = make_user('svc-off', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        self.amy = make_user('svc-amy', name='Amy Able')
        self.bob = make_user('svc-bob', name='Bob Baker')
        self.api = Client()

    def sub(self, member, hours, status='pending', period=None, **extra):
        return ServiceHoursSubmission.objects.create(
            period=period or self.period, submitted_by=member, hours=Decimal(hours),
            service_date=timezone.localdate(), organization='Food Bank', description='Sorted cans.',
            status=status, **extra)

    def call(self, method, url, body=None, access=None):
        kw = bearer(access or self.access)
        if body is None:
            return getattr(self.api, method)(API + url, **kw)
        return getattr(self.api, method)(API + url, json.dumps(body), content_type='application/json', **kw)


class AccessTests(_Base):

    def test_only_the_vpp_and_admins(self):
        s = self.sub(self.amy, '2')
        for method, url in (('get', 'officer/service-hours/'), ('get', 'officer/service-hours/submissions/'),
                            ('get', f'officer/service-hours/submissions/{s.id}/'),
                            ('post', f'officer/service-hours/submissions/{s.id}/'),
                            ('post', 'officer/service-hours/submissions/bulk/')):
            for access in (self.officer_access, make_device(self.amy)[1]):
                r = self.call(method, url, {} if method == 'post' else None, access=access)
                self.assertEqual(r.status_code, 403, url)
                self.assertEqual(r.json()['error'], 'forbidden')
        admin = make_user('svc-admin', is_admin=True)
        self.assertEqual(self.call('get', 'officer/service-hours/', access=make_device(admin)[1]).status_code, 200)
        s.refresh_from_db()
        self.assertEqual(s.status, 'pending')

    def test_pledge_restriction_applies(self):
        # A role makes its holder an Officer, so the pledge here is an admin.
        pledge = make_user('svc-pl', member_type='Pledge', member_status='Pledge', is_admin=True)
        restrict('service_dashboard')
        cache.clear()   # is_allowed caches the restriction list
        r = self.call('get', 'officer/service-hours/', access=make_device(pledge)[1])
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))

    def test_hub_says_who_can_review(self):
        self.sub(self.amy, '2')
        body = self.call('get', 'officer/').json()
        self.assertEqual((body['can_review_service'], body['pending_service']), (True, 1))
        body = self.call('get', 'officer/', access=self.officer_access).json()
        self.assertEqual((body['can_review_service'], body['pending_service']), (False, None))


class DashboardTests(_Base):

    def test_totals_and_progress_match_the_web(self):
        self.sub(self.amy, '4', 'approved')
        self.sub(self.amy, '1.5')
        self.sub(self.bob, '3', 'rejected')
        ServiceHoursAdjustment.objects.create(period=self.period, member=self.amy, hours=Decimal('2'),
                                              reason='Drive', adjusted_by=self.vpp)
        ServiceMemberExpectation.objects.create(period=self.period, member=self.bob, expected_hours=Decimal('0'))
        r = self.call('get', 'officer/service-hours/')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['period']['id'], self.period.id)
        self.assertEqual([p['name'] for p in d['periods']], ['Fall', 'Spring'])
        self.assertEqual(d['stats'], {'total_submissions': 3, 'pending_count': 1, 'approved_count': 1,
                                      'rejected_count': 1, 'total_approved_hours': 4.0,
                                      'total_pending_hours': 1.5})
        amy = next(m for m in d['members'] if m['user_id'] == 'svc-amy')
        self.assertEqual((amy['approved_hours'], amy['submitted_hours'], amy['adjusted_hours'],
                          amy['pending_hours'], amy['expected_hours'], amy['progress_percent'],
                          amy['completed']), (6.0, 4.0, 2.0, 1.5, 10.0, 60, False))
        bob = next(m for m in d['members'] if m['user_id'] == 'svc-bob')
        self.assertEqual((bob['expected_hours'], bob['progress_percent']), (0.0, 0))
        percents = [m['progress_percent'] for m in d['members']]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(len(d['recent']), 3)
        self.assertEqual(d['web']['expectations'],
                         reverse('manage_member_expectations', args=[self.period.id]))

        web = Client()
        web.force_login(self.vpp)
        page = web.get(reverse('service_dashboard'))
        self.assertEqual(page.context['stats']['pending_count'], 1)
        self.assertEqual([m['member'].user_id for m in page.context['member_progress']],
                         [m['user_id'] for m in d['members']])

    def test_period_choice_and_junk(self):
        d = self.call('get', f'officer/service-hours/?period={self.old.id}').json()
        self.assertEqual(d['period']['name'], 'Spring')
        r = self.call('get', 'officer/service-hours/?period=abc')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_no_period(self):
        ServicePeriod.objects.all().delete()
        d = self.call('get', 'officer/service-hours/').json()
        self.assertEqual((d['period'], d['stats'], d['members'], d['recent']), (None, None, [], []))


class ListTests(_Base):

    def test_filters_and_batches(self):
        batch = uuid4()
        a1 = self.sub(self.amy, '1', batch_id=batch)
        self.sub(self.amy, '1', batch_id=batch)
        self.sub(self.amy, '1', 'approved', batch_id=batch)
        self.sub(self.bob, '2', 'approved', period=self.old)
        d = self.call('get', 'officer/service-hours/submissions/').json()
        self.assertEqual(d['count'], 4)
        row = next(s for s in d['submissions'] if s['id'] == a1.id)
        self.assertEqual((row['batch_pending_count'], row['batch_total_count']), (2, 3))
        self.assertEqual(row['member']['name'], 'Amy Able')
        self.assertEqual([s['value'] for s in d['statuses']], ['pending', 'approved', 'rejected'])

        d = self.call('get', 'officer/service-hours/submissions/?status=pending').json()
        self.assertEqual(d['count'], 2)
        d = self.call('get', f'officer/service-hours/submissions/?period={self.old.id}').json()
        self.assertEqual([s['member']['user_id'] for s in d['submissions']], ['svc-bob'])
        d = self.call('get', 'officer/service-hours/submissions/?member=svc-amy&status=approved').json()
        self.assertEqual(d['count'], 1)
        self.assertEqual(d['filters'], {'period': None, 'status': 'approved', 'member': 'svc-amy'})

    def test_pages_of_fifty(self):
        for _ in range(51):
            self.sub(self.amy, '1')
        d = self.call('get', 'officer/service-hours/submissions/?page=2').json()
        self.assertEqual((d['page'], d['pages'], len(d['submissions'])), (2, 2, 1))


class ReviewTests(_Base):

    def test_detail(self):
        s = self.sub(self.amy, '2', attachment='service_hours/proof.pdf')
        field = ServiceFormField.objects.create(field_name='kind', label='Kind', field_type='multiselect')
        ServiceFieldResponse.objects.create(submission=s, field=field, json_value=['Food', 'Kids'])
        d = self.call('get', f'officer/service-hours/submissions/{s.id}/').json()
        self.assertTrue(d['can_review'])
        self.assertEqual(d['submission']['attachment'], reverse('service_hours_attachment', args=[s.id]))
        self.assertEqual(d['responses'], [{'label': 'Kind', 'field_type': 'multiselect',
                                           'value': 'Food, Kids', 'file': None}])
        self.assertEqual(self.call('get', 'officer/service-hours/submissions/999999/').status_code, 404)

    def test_approve_records_what_the_web_records(self):
        s = self.sub(self.amy, '2')
        r = self.call('post', f'officer/service-hours/submissions/{s.id}/', {'action': 'approve', 'notes': ' '})
        self.assertEqual(r.status_code, 200, r.content)
        s.refresh_from_db()
        self.assertEqual((s.status, s.reviewed_by, s.reviewer_notes), ('approved', self.vpp, ''))
        act = ServiceActivity.objects.get(submission=s)
        self.assertEqual((act.action, act.user, act.details), ('approved', self.vpp, 'Approved by VPP'))
        self.assertFalse(r.json()['can_review'])
        self.assertEqual(r.json()['activity'][0]['label'], act.get_action_display())

        r = self.call('post', f'officer/service-hours/submissions/{s.id}/', {'action': 'reject', 'notes': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'not_pending'))

    def test_reject_needs_notes(self):
        s = self.sub(self.amy, '2')
        url = f'officer/service-hours/submissions/{s.id}/'
        r = self.call('post', url, {'action': 'reject', 'notes': '  '})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'notes_required'))
        s.refresh_from_db()
        self.assertEqual(s.status, 'pending')
        r = self.call('post', url, {'action': 'reject', 'notes': 'Not service.'})
        self.assertEqual(r.status_code, 200)
        s.refresh_from_db()
        self.assertEqual((s.status, s.reviewer_notes), ('rejected', 'Not service.'))
        self.assertEqual(ServiceActivity.objects.get(submission=s).details, 'Not service.')
        r = self.call('post', url, {'action': 'maybe'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_bulk(self):
        a = self.sub(self.amy, '1')
        b = self.sub(self.bob, '1')
        done = self.sub(self.bob, '1', 'rejected')
        r = self.call('post', 'officer/service-hours/submissions/bulk/',
                      {'action': 'approve', 'ids': [a.id, b.id, done.id]})
        self.assertEqual(r.json(), {'action': 'approve', 'count': 2})
        self.assertEqual(set(ServiceHoursSubmission.objects.filter(status='approved').values_list('id', flat=True)),
                         {a.id, b.id})
        self.assertEqual(ServiceActivity.objects.filter(details='Bulk approved').count(), 2)
        done.refresh_from_db()
        self.assertEqual(done.status, 'rejected')

        c = self.sub(self.amy, '1')
        r = self.call('post', 'officer/service-hours/submissions/bulk/', {'action': 'reject', 'ids': [c.id]})
        c.refresh_from_db()
        self.assertEqual((c.status, c.reviewer_notes), ('rejected', 'Rejected via bulk action'))

        for body in ({'action': 'approve', 'ids': []}, {'action': 'nope', 'ids': [c.id]},
                     {'action': 'approve', 'ids': ['x']}):
            r = self.call('post', 'officer/service-hours/submissions/bulk/', body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), body)

    def test_web_page_still_reviews(self):
        s = self.sub(self.amy, '2')
        web = Client()
        web.force_login(self.vpp)
        r = web.post(reverse('manage_service_submission', args=[s.id]), {'action': 'reject', 'reviewer_notes': ''})
        self.assertRedirects(r, reverse('manage_service_submission', args=[s.id]), fetch_redirect_response=False)
        s.refresh_from_db()
        self.assertEqual(s.status, 'pending')
        r = web.post(reverse('manage_service_submission', args=[s.id]), {'action': 'approve'})
        self.assertRedirects(r, reverse('view_service_submissions'), fetch_redirect_response=False)
        s.refresh_from_db()
        self.assertEqual(s.status, 'approved')
