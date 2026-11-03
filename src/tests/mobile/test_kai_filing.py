"""
Filing with Kai from the app (src/mobile/views_kai_filing.py): submitting a
report or a commendation, and a party's closure / drop / appeal requests on
their own case.

The app and the website share their save paths (``file_kai_report``,
``file_kai_commendation``, ``file_closure_request``, ``file_drop_request``,
``file_kai_appeal``), so several tests here file the same thing both ways and
compare what was recorded and emailed. The confidentiality rules are the
point: who filed a case never reaches the accused, the audit log names no one,
and only the chairs are emailed.

    python manage.py test src.tests.mobile.test_kai_filing
"""
import io
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from src.models import (
    ActivityLog, Committee, KaiAppeal, KaiClosureRequest, KaiCommendation, KaiCommendationFieldResponse,
    KaiFormField, KaiReport, KaiReportActivity, KaiReportFieldResponse, KaiReportTemplate,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'


def _png(name='photo.png'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _mails():
    """The emails sent, without the "Submitted at" line (a clock reading)."""
    return [(m.subject, sorted(m.to),
             '\n'.join(line for line in m.body.splitlines() if not line.startswith('Submitted at:')))
            for m in mail.outbox]


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.reporter = make_user('kf-reporter', email='reporter@example.com')
        self.accused = make_user('kf-accused', email='accused@example.com')
        self.other = make_user('kf-other', email='other@example.com')
        self.chair = make_user('kf-chair', email='chair@example.com')
        self.member = make_user('kf-member', email='member@example.com')
        self.kai = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.kai.members.add(self.chair, self.member)
        self.kai.chairs.add(self.chair)
        self.api = Client()
        self.tokens = {}

    def access(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def get(self, user, p):
        return self.api.get(API + p, **bearer(self.access(user)))

    def post(self, user, p, body=None, multipart=False):
        if multipart:
            return self.api.post(API + p, body or {}, **bearer(self.access(user)))
        return self.api.post(API + p, body or {}, content_type='application/json', **bearer(self.access(user)))

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c

    def case(self, title='The case', by=None, to=None, **kw):
        return KaiReport.objects.create(
            title=title, description=f'{title} body', submitted_by=by or self.reporter,
            targeted_to=to if to is not None else self.accused, **kw)


# ── The forms ────────────────────────────────────────────────────────────────

class FormTests(_Base):

    def test_report_form(self):
        make_user('kf-gone', member_status='Inactive')
        text = KaiFormField.objects.create(field_name='kf_where', label='Where', field_type='text',
                                           is_required=True, section='Details', display_order=1)
        KaiFormField.objects.create(field_name='kf_who', label='Who', field_type='radio',
                                    options=['A', {'value': 'b', 'label': 'Bee'}], section='Details',
                                    display_order=2)
        KaiFormField.objects.create(field_name='kf_praise', label='Praise', field_type='text',
                                    form_type='commendation')
        KaiFormField.objects.create(field_name='kf_off', label='Off', field_type='text', is_active=False)
        KaiReportTemplate.objects.create(name='Noise', description='d', category='social',
                                         title_template='Noise at night', description_template='What happened',
                                         suggested_tags=['urgent', 'Follow Up'])
        r = self.get(self.reporter, 'kai/form/')
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertIn({'value': 'hazing', 'label': 'Hazing Concerns'}, b['categories'])
        self.assertIn({'value': 'follow-up', 'label': 'Follow-Up Needed'}, b['tags'])
        names = [m['name'] for m in b['members']]
        self.assertIn('User kf-accused', names)
        self.assertNotIn('User kf-gone', names)
        self.assertEqual(names, sorted(names))
        self.assertEqual(b['templates'][0]['tags'], ['urgent', 'follow-up'])
        self.assertEqual(b['templates'][0]['category_label'], 'Social Conduct')
        self.assertEqual([f['label'] for f in b['fields']], ['Where', 'Who'])
        self.assertEqual(b['fields'][0]['name'], f'custom_field_{text.id}')
        self.assertTrue(b['fields'][0]['required'])
        self.assertEqual(b['fields'][1]['options'], [{'value': 'A', 'label': 'A'}, {'value': 'b', 'label': 'Bee'}])
        self.assertEqual(b['attachment']['extensions'],   # v4.4.2: KaiReportForm's whole list
                         ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.jpg', '.jpeg', '.png'])

    def test_commendation_form(self):
        KaiFormField.objects.create(field_name='kf_praise', label='Praise', field_type='text',
                                    form_type='commendation')
        KaiFormField.objects.create(field_name='kf_where', label='Where', field_type='text')
        b = self.get(self.reporter, 'kai/commendations/form/').json()
        self.assertEqual([f['label'] for f in b['fields']], ['Praise'])
        self.assertIn('User kf-accused', [m['name'] for m in b['members']])

    def test_feature_flag_gates_the_submit_forms_only(self):
        flag('kai_reports', False)
        for p in ('kai/form/', 'kai/commendations/form/'):
            r = self.get(self.reporter, p)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'), p)
        for p in ('kai/reports/', 'kai/commendations/'):
            r = self.post(self.reporter, p, {'title': 'x'})
            self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'), p)
        self.assertEqual(KaiReport.objects.count(), 0)
        self.assertFalse(self.get(self.reporter, 'kai/').json()['can_file'])
        # The request views have no flag on the website, so none here either.
        case = self.case(deliberation_outcome='heard')
        r = self.post(self.reporter, f'kai/reports/{case.id}/closure/', {'reason': 'Done'})
        self.assertEqual(r.status_code, 201, r.content)

    def test_toggle_is_ignored_and_pledge_restriction_applies(self):
        toggle('submit_kai_report', False)
        self.assertEqual(self.get(self.reporter, 'kai/form/').status_code, 200)
        pledge = make_user('kf-pledge', member_type='Pledge')
        restrict('submit_kai_report', [])
        r = self.post(pledge, 'kai/reports/', {'title': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        restrict('submit_kai_commendation', [])
        r = self.get(pledge, 'kai/commendations/form/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))

    def test_signed_out(self):
        self.assertEqual(self.api.get(API + 'kai/form/').status_code, 401)
        self.assertEqual(self.api.post(API + 'kai/reports/', {}).status_code, 401)
        self.assertEqual(self.api.post(API + 'kai/reports/1/appeal/', {}).status_code, 401)


# ── Submitting a report ──────────────────────────────────────────────────────

class SubmitReportTests(_Base):

    def body(self, **kw):
        b = {'category': 'behavioral', 'title': 'Loud party', 'description': 'It was loud.',
             'targeted_to': str(self.accused.pk), 'tags': 'urgent,follow-up'}
        b.update(kw)
        return b

    def test_files_the_report_like_the_website(self):
        r = self.post(self.reporter, 'kai/reports/', self.body())
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        report = KaiReport.objects.get(id=b['id'])
        self.assertEqual(b['message'], 'Your Kai report has been submitted successfully! '
                                       'The Kai chair(s) have been notified.')
        self.assertEqual(b['notes'], [])
        self.assertEqual((report.submitted_by, report.targeted_to, report.category, report.tags),
                         (self.reporter, self.accused, 'behavioral', ['urgent', 'follow-up']))
        self.assertEqual(list(report.activity_log.values_list('action', 'user')), [('created', self.reporter.pk)])
        log = ActivityLog.objects.get(action_type='kai_action', object_type='KaiReport')
        self.assertEqual(log.description, f'A member submitted Kai case {report.display_number}')
        self.assertNotIn('kf-reporter', log.description)
        app_mail = _mails()

        # The same report through the website: same rows, same email.
        mail.outbox = []
        resp = self.web(self.reporter).post(reverse('submit_kai_report'), self.body())
        self.assertEqual(resp.status_code, 302)
        web_report = KaiReport.objects.exclude(id=report.id).get()
        self.assertEqual(_mails(), app_mail)
        self.assertEqual(list(web_report.activity_log.values_list('action', 'details')),
                         list(report.activity_log.values_list('action', 'details')))

    def test_only_the_chairs_are_emailed(self):
        self.post(self.reporter, 'kai/reports/', self.body())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['chair@example.com'])
        self.assertEqual(mail.outbox[0].subject, 'New Kai Report: Loud party')

    def test_no_one_named(self):
        r = self.post(self.reporter, 'kai/reports/', self.body(targeted_to=''))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(KaiReport.objects.get().targeted_to)

    def test_validation(self):
        r = self.post(self.reporter, 'kai/reports/', self.body(title=''))
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('Report Title', r.json()['detail'])
        r = self.post(self.reporter, 'kai/reports/', self.body(tags='kf-accused'))
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('not in the allowed list: kf-accused', r.json()['detail'])
        r = self.post(self.reporter, 'kai/reports/', self.body(category='gossip'))
        self.assertEqual(r.status_code, 400)
        r = self.post(self.reporter, 'kai/reports/',
                      self.body(attachment=SimpleUploadedFile('x.exe', b'MZ')), multipart=True)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertEqual(KaiReport.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_tags_saved_in_the_vocabulary_spelling(self):
        r = self.post(self.reporter, 'kai/reports/', self.body(tags='Follow Up, URGENT, Repeat Incident'))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(KaiReport.objects.get().tags, ['follow-up', 'urgent', 'repeat-incident'])

    def test_the_accused_must_be_an_active_member(self):
        gone = make_user('kf-alum', member_status='Alumni')
        for client_post in (lambda b: self.post(self.reporter, 'kai/reports/', b),
                            lambda b: self.web(self.reporter).post(reverse('submit_kai_report'), b)):
            r = client_post(self.body(targeted_to=gone.pk))
            self.assertNotIn(r.status_code, (201, 302))
        self.assertEqual(KaiReport.objects.count(), 0)

    def test_web_tag_buttons_are_all_in_the_vocabulary(self):
        import re
        html = self.web(self.reporter).get(reverse('submit_kai_report')).content.decode()
        tags = re.findall(r'js-add-tag[^>]*data-tag="([^"]+)"', html)
        self.assertTrue(tags)
        self.assertEqual([t for t in tags if t not in KaiReport.ALLOWED_TAGS], [])

    def test_multipart_with_attachment_and_custom_fields(self):
        text = KaiFormField.objects.create(field_name='kf_where', label='Where', field_type='text')
        num = KaiFormField.objects.create(field_name='kf_n', label='How many', field_type='number')
        multi = KaiFormField.objects.create(field_name='kf_w', label='Witnesses', field_type='multiselect',
                                            options=['A', 'B', 'C'])
        check = KaiFormField.objects.create(field_name='kf_c', label='Repeat?', field_type='checkbox')
        member = KaiFormField.objects.create(field_name='kf_m', label='Also there', field_type='member_select')
        photo = KaiFormField.objects.create(field_name='kf_p', label='Photo', field_type='file')
        bad = KaiFormField.objects.create(field_name='kf_b', label='Document', field_type='file')
        skipped = KaiFormField.objects.create(field_name='kf_s', label='Skipped', field_type='text')
        praise = KaiFormField.objects.create(field_name='kf_praise', label='Praise', field_type='text',
                                             form_type='commendation')
        body = self.body(attachment=_png('proof.png'), **{
            f'custom_field_{text.id}': 'The house',
            f'custom_field_{num.id}': '3',
            f'custom_field_{multi.id}': ['A', 'C'],
            f'custom_field_{check.id}': 'yes',
            f'custom_field_{member.id}': str(self.other.pk),
            f'custom_field_{photo.id}': _png('scene.png'),
            f'custom_field_{bad.id}': SimpleUploadedFile('evil.exe', b'MZ'),
            f'custom_field_{skipped.id}': '  ',
            f'custom_field_{praise.id}': 'not this form',
        })
        r = self.post(self.reporter, 'kai/reports/', body, multipart=True)
        self.assertEqual(r.status_code, 201, r.content)
        report = KaiReport.objects.get(id=r.json()['id'])
        self.assertTrue(report.attachment)
        # The refused file is dropped and the member told, as on the website.
        self.assertEqual(len(r.json()['notes']), 1)
        self.assertTrue(r.json()['notes'][0].startswith('Document: '))
        answers = {x.field_id: x for x in KaiReportFieldResponse.objects.filter(report=report)}
        self.assertEqual(set(answers), {text.id, num.id, multi.id, check.id, member.id, photo.id})
        self.assertEqual(answers[text.id].text_value, 'The house')
        self.assertEqual(float(answers[num.id].number_value), 3.0)
        self.assertEqual(answers[multi.id].json_value, ['A', 'C'])
        self.assertEqual(answers[check.id].json_value, ['yes'])
        self.assertEqual(answers[member.id].text_value, str(self.other.pk))
        self.assertTrue(answers[photo.id].file_value)

    def test_json_lists_for_multiselect(self):
        multi = KaiFormField.objects.create(field_name='kf_w', label='Witnesses', field_type='multiselect')
        r = self.post(self.reporter, 'kai/reports/', self.body(**{f'custom_field_{multi.id}': ['A', 'B']}))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(KaiReportFieldResponse.objects.get().json_value, ['A', 'B'])

    def test_the_accused_never_learns_who_filed(self):
        r = self.post(self.reporter, 'kai/reports/', self.body())
        report = KaiReport.objects.get(id=r.json()['id'])
        # Hidden from the accused until the committee addresses it…
        self.assertEqual(self.get(self.accused, f'kai/reports/{report.id}/').status_code, 404)
        self.assertEqual(self.get(self.accused, 'kai/').json()['named'], [])
        # …and once it has, still no submitter anywhere.
        report.deliberation_outcome = 'heard'
        report.save()
        for p in (f'kai/reports/{report.id}/', 'kai/'):
            text = self.get(self.accused, p).content.decode()
            self.assertNotIn('kf-reporter', text, p)
            self.assertNotIn('reporter@example.com', text, p)
        self.assertNotIn('accused@example.com', str([m.to for m in mail.outbox]))


# ── Submitting a commendation ────────────────────────────────────────────────

class SubmitCommendationTests(_Base):

    def body(self, **kw):
        b = {'commended_member': str(self.accused.pk), 'title': 'Great rush week',
             'description': 'Ran every event.', 'is_submitter_anonymous': True}
        b.update(kw)
        return b

    def test_files_the_commendation_like_the_website(self):
        field = KaiFormField.objects.create(field_name='kf_praise', label='Praise', field_type='text',
                                            form_type='commendation')
        r = self.post(self.reporter, 'kai/commendations/', self.body(**{f'custom_field_{field.id}': 'Lots'}))
        self.assertEqual(r.status_code, 201, r.content)
        c = KaiCommendation.objects.get(id=r.json()['id'])
        self.assertEqual(r.json()['message'],
                         'Your commendation has been submitted. The Kai chair(s) have been notified.')
        self.assertEqual((c.submitted_by, c.commended_member, c.is_submitter_anonymous),
                         (self.reporter, self.accused, True))
        self.assertEqual(KaiCommendationFieldResponse.objects.get(commendation=c).text_value, 'Lots')
        self.assertEqual(list(c.activity_log.values_list('action', flat=True)), ['created'])
        log = ActivityLog.objects.get(action_type='kai_action', object_type='KaiCommendation')
        self.assertNotIn('kf-', log.description)
        self.assertEqual([m.to for m in mail.outbox], [['chair@example.com']])
        app_mail = _mails()

        mail.outbox = []
        body = self.body(**{f'custom_field_{field.id}': 'Lots'})
        body['is_submitter_anonymous'] = 'on'
        self.assertEqual(self.web(self.reporter).post(reverse('submit_kai_commendation'), body).status_code, 302)
        self.assertEqual(_mails(), app_mail)

    def test_not_anonymous_by_default_and_multipart(self):
        r = self.post(self.reporter, 'kai/commendations/',
                      self.body(is_submitter_anonymous='false', attachment=_png()), multipart=True)
        self.assertEqual(r.status_code, 201, r.content)
        c = KaiCommendation.objects.get()
        self.assertFalse(c.is_submitter_anonymous)
        self.assertTrue(c.attachment)

    def test_validation(self):
        r = self.post(self.reporter, 'kai/commendations/', self.body(commended_member=''))
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.post(self.reporter, 'kai/commendations/',
                      self.body(attachment=SimpleUploadedFile('x.exe', b'MZ')), multipart=True)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(KaiCommendation.objects.count(), 0)
        self.assertEqual(mail.outbox, [])


# ── Closure and drop requests ────────────────────────────────────────────────

class ClosureTests(_Base):

    def setUp(self):
        super().setUp()
        self.report = self.case(deliberation_outcome='heard', status='reviewed')

    def path(self, kind='closure', report=None):
        return f'kai/reports/{(report or self.report).id}/{kind}/'

    def test_submitter_requests_closure_like_the_website(self):
        r = self.post(self.reporter, self.path(), {'reason': 'It is resolved'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'], 'Your closure request has been submitted and is pending review.')
        cr = KaiClosureRequest.objects.get()
        self.assertEqual((cr.request_type, cr.requested_by, cr.reason, cr.status),
                         ('closure', self.reporter, 'It is resolved', 'pending'))
        act = self.report.activity_log.get()
        self.assertEqual((act.action, act.details),
                         ('closure_requested', 'Submitter requested case closure. Reason: It is resolved'))
        log = ActivityLog.objects.get(action_type='kai_action')
        self.assertEqual(log.description, f'A party requested closure of Kai case {self.report.display_number}')
        self.assertEqual([(m.subject, m.to) for m in mail.outbox],
                         [('[Kai] Closure Request: The case', ['chair@example.com'])])
        app = (_mails(), list(self.report.activity_log.values_list('action', 'details')))

        # The website, on an identical case.
        mail.outbox = []
        twin = self.case(deliberation_outcome='heard', status='reviewed')
        resp = self.web(self.reporter).post(reverse('kai_request_closure', args=[twin.id]),
                                            {'reason': 'It is resolved'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual((_mails(), list(twin.activity_log.values_list('action', 'details'))), app)

    def test_accused_requests_closure_and_sees_no_submitter(self):
        r = self.post(self.accused, self.path(), {'reason': 'x' * 150})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn('kf-reporter', r.content.decode())
        act = self.report.activity_log.get()
        self.assertEqual(act.details, f'Accused party requested case closure. Reason: {"x" * 100}...')
        self.assertIn('Requested by: User kf-accused (Accused party)', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ['chair@example.com'])

    def test_refusals(self):
        r = self.post(self.reporter, self.path(), {'reason': '  '})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertEqual(r.json()['detail'], 'Please provide a reason for your closure request.')

        pending = self.case('Pending')
        r = self.post(self.reporter, self.path(report=pending), {'reason': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'not_addressed'))
        # The accused can't see a pending case at all.
        self.assertEqual(self.post(self.accused, self.path(report=pending), {'reason': 'x'}).status_code, 404)

        archived = self.case('Old', deliberation_outcome='dismissed', status='archived')
        r = self.post(self.reporter, self.path(report=archived), {'reason': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'archived'))

        KaiClosureRequest.objects.create(report=self.report, requested_by=self.accused, reason='mine')
        r = self.post(self.reporter, self.path(), {'reason': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'request_pending'))
        self.assertEqual(KaiClosureRequest.objects.count(), 1)

        self.assertEqual(self.post(self.other, self.path(), {'reason': 'x'}).status_code, 404)
        self.assertEqual(self.post(self.reporter, 'kai/reports/999999/closure/', {'reason': 'x'}).status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_drop_by_the_submitter_like_the_website(self):
        r = self.post(self.reporter, self.path('drop'), {'reason': 'Changed my mind'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'],
                         'Your request to drop this case has been submitted and is pending review.')
        cr = KaiClosureRequest.objects.get()
        self.assertEqual((cr.request_type, cr.reason), ('drop', 'Changed my mind'))
        log = ActivityLog.objects.get(action_type='kai_action')
        self.assertEqual(log.description, f'The submitter requested to drop Kai case {self.report.display_number}')
        app = (_mails(), list(self.report.activity_log.values_list('action', 'details')))
        self.assertEqual(app[0][0][:2], ('[Kai] Drop Case Request: The case', ['chair@example.com']))

        mail.outbox = []
        twin = self.case(deliberation_outcome='heard', status='reviewed')
        self.web(self.reporter).post(reverse('kai_request_drop', args=[twin.id]), {'reason': 'Changed my mind'})
        self.assertEqual((_mails(), list(twin.activity_log.values_list('action', 'details'))), app)

    def test_drop_refusals(self):
        # Only the submitter: the accused gets 404, as on the website.
        self.assertEqual(self.post(self.accused, self.path('drop'), {'reason': 'x'}).status_code, 404)
        r = self.post(self.reporter, self.path('drop'), {'reason': ''})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        sanctioned = self.case('Done', deliberation_outcome='sanctions_applied')
        r = self.post(self.reporter, self.path('drop', sanctioned), {'reason': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'sanctions_applied'))
        # A drop is allowed before the case is addressed.
        pending = self.case('Pending')
        self.assertEqual(self.post(self.reporter, self.path('drop', pending), {'reason': 'x'}).status_code, 201)
        r = self.post(self.reporter, self.path('drop', pending), {'reason': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'request_pending'))

    def test_case_response_offers_the_native_actions(self):
        b = self.get(self.reporter, f'kai/reports/{self.report.id}/').json()
        self.assertEqual(b['can'], {'request_closure': True, 'drop_case': True, 'appeal': False})
        # The old keys are kept for the shipped build.
        self.assertEqual(b['actions']['request_closure'], reverse('kai_request_closure', args=[self.report.id]))
        self.post(self.reporter, self.path(), {'reason': 'x'})
        b = self.get(self.reporter, f'kai/reports/{self.report.id}/').json()
        self.assertEqual(b['can'], {'request_closure': False, 'drop_case': False, 'appeal': False})
        self.assertIsNone(b['actions']['request_closure'])

    def test_dashboard_offers_the_native_forms(self):
        b = self.get(self.reporter, 'kai/').json()
        self.assertTrue(b['can_file'])
        self.assertEqual(b['links']['commend'], reverse('submit_kai_commendation'))
        self.assertEqual(b['links']['submit'], reverse('submit_kai_report'))
        self.assertFalse(b['can_manage_commendations'])
        self.assertTrue(self.get(self.chair, 'kai/').json()['can_manage_commendations'])


# ── Appeals ──────────────────────────────────────────────────────────────────

class AppealTests(_Base):

    def setUp(self):
        super().setUp()
        self.report = self.case(deliberation_outcome='sanctions_applied', status='reviewed',
                                accused_notified_at=timezone.now() - timedelta(days=3))

    def path(self, report=None):
        return f'kai/reports/{(report or self.report).id}/appeal/'

    def test_accused_appeals_like_the_website(self):
        r = self.post(self.accused, self.path(), {'level': 'district_chief', 'grounds': 'Unfair hearing'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'],
                         'Your appeal to District Chief has been filed and the committee has been notified.')
        a = KaiAppeal.objects.get()
        self.assertEqual((a.filed_by, a.level, a.grounds, a.report), (self.accused, 'district_chief',
                                                                     'Unfair hearing', self.report))
        act = self.report.activity_log.get()
        self.assertEqual((act.action, act.details), ('appeal_filed', 'Appeal filed to District Chief.'))
        log = ActivityLog.objects.get(action_type='kai_action')
        self.assertEqual(log.description, f'An appeal was filed on Kai case {self.report.display_number}')
        self.assertNotIn('kf-accused', log.description)
        # The website emails no one on an appeal; neither does the app.
        self.assertEqual(mail.outbox, [])
        app_activity = list(self.report.activity_log.values_list('action', 'details'))

        twin = self.case(deliberation_outcome='sanctions_applied', status='reviewed',
                         accused_notified_at=timezone.now() - timedelta(days=3))
        self.web(self.accused).post(reverse('kai_file_appeal', args=[twin.id]),
                                    {'level': 'district_chief', 'grounds': 'Unfair hearing'})
        self.assertEqual(list(twin.activity_log.values_list('action', 'details')), app_activity)
        self.assertEqual(mail.outbox, [])

        # Once filed, the case page says so.
        b = self.get(self.accused, f'kai/reports/{self.report.id}/').json()
        self.assertFalse(b['can']['appeal'])
        self.assertIn('already filed', b['appeal']['blocked_reason'])

    def test_unknown_level_is_the_chapter(self):
        self.post(self.accused, self.path(), {'level': 'supreme_court', 'grounds': 'x'})
        self.assertEqual(KaiAppeal.objects.get().level, 'chapter')
        twin = self.case(deliberation_outcome='heard', accused_notified_at=timezone.now())
        self.post(self.accused, self.path(twin), {'grounds': 'x'})
        self.assertEqual(KaiAppeal.objects.get(report=twin).level, 'chapter')

    def test_window_and_conditions(self):
        r = self.post(self.accused, self.path(), {'grounds': ''})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertEqual(r.json()['detail'], 'Please describe the grounds for your appeal.')

        closed = self.case('Closed', deliberation_outcome='heard',
                           accused_notified_at=timezone.now() - timedelta(days=10, minutes=1))
        r = self.post(self.accused, self.path(closed), {'grounds': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'appeal_unavailable'))
        self.assertEqual(r.json()['detail'], 'The 10-day appeal window for this case has closed.')

        unnotified = self.case('Quiet', deliberation_outcome='heard')
        r = self.post(self.accused, self.path(unnotified), {'grounds': 'x'})
        self.assertEqual(r.json()['detail'], 'No decision has been issued on this case yet.')

        KaiAppeal.objects.create(report=self.report, filed_by=self.accused, grounds='first')
        r = self.post(self.accused, self.path(), {'grounds': 'again'})
        self.assertEqual(r.json()['detail'], 'You have already filed an appeal on this case.')
        # Withdrawn doesn't count, as KaiAppeal.can_file says.
        KaiAppeal.objects.filter(report=self.report).update(status='withdrawn')
        self.assertEqual(self.post(self.accused, self.path(), {'grounds': 'again'}).status_code, 201)

    def test_only_the_accused(self):
        self.assertEqual(self.post(self.reporter, self.path(), {'grounds': 'x'}).status_code, 404)
        self.assertEqual(self.post(self.other, self.path(), {'grounds': 'x'}).status_code, 404)
        pending = self.case('Pending', accused_notified_at=timezone.now())
        self.assertEqual(self.post(self.accused, self.path(pending), {'grounds': 'x'}).status_code, 404)
        self.assertEqual(KaiAppeal.objects.count(), 0)

    def test_case_response_offers_the_appeal_form(self):
        b = self.get(self.accused, f'kai/reports/{self.report.id}/').json()
        self.assertTrue(b['can']['appeal'])
        self.assertEqual(b['appeal']['levels'][0], {'value': 'chapter', 'label': 'The Chapter'})
        self.assertEqual([x['value'] for x in b['appeal']['levels']],
                         ['chapter', 'district_chief', 'board', 'convention'])
        self.assertNotIn('kf-reporter', self.get(self.accused, f'kai/reports/{self.report.id}/').content.decode())


class WebStillWorksTests(_Base):
    """The web views now call the shared helpers; their own messages and redirects are unchanged."""

    def test_web_refusals_redirect_as_before(self):
        pending = self.case('Pending')
        c = self.web(self.reporter)
        resp = c.post(reverse('kai_request_closure', args=[pending.id]), {'reason': 'x'})
        self.assertRedirects(resp, reverse('user_kai_dashboard'), fetch_redirect_response=False)
        heard = self.case('Heard', deliberation_outcome='heard')
        resp = c.post(reverse('kai_request_closure', args=[heard.id]), {'reason': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(KaiClosureRequest.objects.count(), 0)
        sanctioned = self.case('Done', deliberation_outcome='sanctions_applied')
        resp = c.post(reverse('kai_request_drop', args=[sanctioned.id]), {'reason': 'x'})
        self.assertRedirects(resp, reverse('user_view_kai_report', args=[sanctioned.id]),
                             fetch_redirect_response=False)
        self.assertEqual(KaiReportActivity.objects.count(), 0)

    def test_web_forms_render(self):
        c = self.web(self.reporter)
        self.assertEqual(c.get(reverse('submit_kai_report')).status_code, 200)
        self.assertEqual(c.get(reverse('submit_kai_commendation')).status_code, 200)
