"""
v4.4.0 — logging service hours in the app (src/mobile/views_service_hours.py).

The app must save exactly what /service-hours/submit/ and
/service-hours/edit/<id>/ save: the same form checks, extra-date rows,
custom-field answers, activity log and VPP email (one shared helper each,
``create_service_submissions`` / ``update_service_submission``).

Until its routes are in src/mobile/urls.py, this module is its own urlconf.

    python manage.py test src.tests.mobile.test_service_hours_log
"""
import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from django.utils import timezone

from src.mobile import views_service_hours as v
from src.models import (
    ServiceActivity, ServiceFieldResponse, ServiceFormField, ServiceHoursSubmission, ServicePeriod,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict

API = '/api/mobile/v1/'

_routes = [
    path('service-hours/form/', v.ServiceHoursFormView.as_view(), name='mobile_service_hours_form'),
    path('service-hours/submissions/', v.ServiceSubmissionsView.as_view(), name='mobile_service_submissions'),
    path('service-hours/submissions/<int:submission_id>/', v.ServiceSubmissionDetailView.as_view(),
         name='mobile_service_submission'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_svc')))] + root.urlpatterns


urlpatterns = _urlpatterns()


def _png(name='proof.png'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class ServiceHoursLogTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('svc-app')
        _, self.access, _ = make_device(self.user)
        self.api = Client()
        today = timezone.localdate()
        self.today = today
        self.period = ServicePeriod.objects.create(
            name='Fall', start_date=today - timedelta(days=30), end_date=today + timedelta(days=30),
            default_hours_required=Decimal('10'), requires_approval=True)
        self.shirt = ServiceFormField.objects.create(
            field_name='shirt', label='Shirt size', field_type='select', options=['S', 'M', 'L'],
            is_required=True, section='Extra', display_order=1)
        self.tags = ServiceFormField.objects.create(
            field_name='tags', label='Tags', field_type='checkbox', options=['Outdoors', 'Kids'], section='Extra', display_order=2)
        ServiceFormField.objects.create(field_name='builtin_x', label='Builtin', field_type='text', is_builtin=True)
        ServiceFormField.objects.create(field_name='off', label='Off', field_type='text', is_active=False)

    def body(self, **extra):
        data = {'period': self.period.pk, 'hours': '2.5', 'service_date': self.today.isoformat(),
                'organization': 'Food Bank', 'description': 'Sorted cans'}
        data.update(extra)
        return data

    def post(self, data, path='service-hours/submissions/', **kw):
        return self.api.post(API + path, data, **kw, **bearer(self.access))

    def test_form(self):
        r = self.api.get(API + 'service-hours/form/', **bearer(self.access))
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([p['id'] for p in body['periods']], [self.period.pk])
        self.assertEqual(body['current_period_id'], self.period.pk)
        self.assertEqual([f['name'] for f in body['fields']], ['custom_shirt', 'custom_tags'])
        self.assertEqual(body['fields'][0]['options'], ['S', 'M', 'L'])
        self.assertTrue(body['fields'][0]['required'])
        self.assertEqual(body['max_extra_dates'], 59)

    def test_json_submission_with_extra_dates_and_custom_fields(self):
        with patch('src.view.service_user_dashboard.send_email') as mail:
            r = self.post(self.body(
                custom_shirt='M', custom_tags=['Outdoors', 'Kids'],
                extra_dates=[(self.today - timedelta(days=1)).isoformat(), (self.today - timedelta(days=2)).isoformat()],
                extra_hours=['3', '99']), content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(len(body['ids']), 2)   # the 99-hour date is skipped, loudly
        self.assertEqual(body['status'], 'pending')
        self.assertIn('2 dates', body['message'])
        self.assertEqual(len(body['notes']), 1)
        self.assertIn('this date was skipped', body['notes'][0])
        rows = ServiceHoursSubmission.objects.filter(submitted_by=self.user)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(len({r.batch_id for r in rows}), 1)
        for row in rows:
            self.assertEqual(ServiceFieldResponse.objects.get(submission=row, field=self.shirt).text_value, 'M')
            self.assertEqual(ServiceFieldResponse.objects.get(submission=row, field=self.tags).json_value,
                             ['Outdoors', 'Kids'])
        self.assertEqual(ServiceActivity.objects.filter(action='created').count(), 2)
        self.assertLessEqual(mail.delay.call_count, 1)

    def test_multipart_with_attachment(self):
        r = self.post(self.body(attachment=_png()))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(ServiceHoursSubmission.objects.get(pk=r.json()['ids'][0]).attachment)

    def test_auto_approved_period(self):
        self.period.requires_approval = False
        self.period.save()
        r = self.post(self.body(), content_type='application/json')
        self.assertEqual(r.json()['status'], 'approved')
        self.assertIn('Auto-approved', r.json()['message'])

    def test_form_errors_are_invalid_input(self):
        r = self.post(self.body(hours='30'), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        self.assertIn('Hours cannot exceed 24', r.json()['detail'])
        r = self.post(self.body(attachment=SimpleUploadedFile('x.exe', b'MZ')))
        self.assertEqual(r.json()['error'], 'invalid_input')
        self.assertFalse(ServiceHoursSubmission.objects.exists())

    def test_no_period(self):
        ServicePeriod.objects.update(is_active=False)
        r = self.post(self.body(), content_type='application/json')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'no_period'))

    def test_pledge_restriction(self):
        restrict('submit_service_hours', phases=[])
        self.user.member_type = 'Pledge'
        self.user.member_status = 'Pledge'
        self.user.save()
        r = self.api.get(API + 'service-hours/form/', **bearer(self.access))
        self.assertEqual(r.status_code, 403, r.content)

    def _submission(self, status='pending'):
        r = self.post(self.body(custom_shirt='S'), content_type='application/json')
        s = ServiceHoursSubmission.objects.get(pk=r.json()['ids'][0])
        s.status = status
        s.reviewer_notes = 'No proof' if status == 'rejected' else ''
        s.save()
        return s

    def test_detail_and_edit(self):
        s = self._submission()
        r = self.api.get(API + f'service-hours/submissions/{s.pk}/', **bearer(self.access))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['responses'], {'custom_shirt': 'S'})
        self.assertTrue(r.json()['can_edit'])
        r = self.post(self.body(hours='4', custom_shirt='L'), path=f'service-hours/submissions/{s.pk}/',
                      content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Submission updated successfully.')
        s.refresh_from_db()
        self.assertEqual(s.hours, Decimal('4'))
        self.assertEqual(ServiceFieldResponse.objects.get(submission=s, field=self.shirt).text_value, 'L')
        self.assertTrue(ServiceActivity.objects.filter(submission=s, action='updated').exists())

    def test_rejected_is_resubmitted(self):
        s = self._submission('rejected')
        with patch('src.view.service_user_dashboard.send_email'):
            r = self.post(self.body(), path=f'service-hours/submissions/{s.pk}/', content_type='application/json')
        self.assertEqual(r.json()['status'], 'pending')
        s.refresh_from_db()
        self.assertEqual(s.reviewer_notes, '')

    def test_approved_cannot_be_edited_and_others_are_hidden(self):
        s = self._submission('approved')
        r = self.post(self.body(), path=f'service-hours/submissions/{s.pk}/', content_type='application/json')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'not_editable'))
        other = make_user('svc-other')
        _, access, _ = make_device(other)
        r = self.api.get(API + f'service-hours/submissions/{s.pk}/', **bearer(access))
        self.assertEqual(r.status_code, 404)


class WebStillTheSameTests(TestCase):
    """The web views after the move into shared helpers: same messages."""

    def setUp(self):
        self.user = make_user('svc-web')
        self.client.force_login(self.user)
        today = timezone.localdate()
        self.period = ServicePeriod.objects.create(
            name='Fall', start_date=today - timedelta(days=30), end_date=today + timedelta(days=30))
        self.today = today

    def test_submit_and_edit_messages(self):
        from django.contrib.messages import get_messages
        data = {'period': self.period.pk, 'hours': '2', 'service_date': self.today.isoformat(),
                'organization': 'Org', 'description': 'D',
                'extra_dates': [(self.today - timedelta(days=1)).isoformat()], 'extra_hours': ['abc']}
        with patch('src.view.service_user_dashboard.send_email'):
            r = self.client.post('/service-hours/submit/', data)
        self.assertEqual(r.status_code, 302)
        msgs = [str(m) for m in get_messages(r.wsgi_request)]
        self.assertIn('no valid hours entered', msgs[0])
        self.assertRegex(msgs[1], r'^Successfully submitted 2(\.00)? service hours for approval\.$')
        s = ServiceHoursSubmission.objects.get()
        r = self.client.post(f'/service-hours/edit/{s.pk}/', dict(data, hours='3'))
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertEqual(s.hours, Decimal('3'))
