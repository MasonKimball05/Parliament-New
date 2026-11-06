"""
Slating in the app, batch 5 (11-02-26): applying natively, My applications,
and the committee's tools — reviewing applications, interviews, attendance,
paper ballots, the results summary and publishing.

Each endpoint runs the web view's own helpers, so these check the web's
permissions (member vs committee vs chair, the locked-period rule), its
status gates, that the app sees what the web page's context holds, its
validation, and its side effects (activity log, notifications).

    python manage.py test src.tests.mobile.test_slating_batch5
"""
import io
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from src.models import (
    SlatingActivity, SlatingApplication, SlatingApplicationResponse, SlatingAttendance, SlatingFormField,
    SlatingInterview, SlatingPaperBallots, SlatingPeriod, SlatingPosition,
)
from src.tests.mobile.sess_helpers import bearer, make_device
from src.tests.slating.test_slating_review_fixes import _Base, member

API = '/api/mobile/v1/'


def _png(name='shot.png'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _AppBase(_Base):

    def setUp(self):
        super().setUp()
        self.api = Client()
        self.tokens = {}

    def access(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def call(self, method, path, body=None, user=None, multipart=False):
        kw = bearer(self.access(user or self.voter))
        if body is None:
            return getattr(self.api, method)(API + path, **kw)
        if multipart:
            return getattr(self.api, method)(API + path, body, **kw)
        return getattr(self.api, method)(API + path, json.dumps(body), content_type='application/json', **kw)


# ── Applying ─────────────────────────────────────────────────────────────────

class ApplyTests(_AppBase):

    def setUp(self):
        super().setUp()
        self.open = SlatingPeriod.objects.create(name='Spring', academic_term='Spring 2027',
                                                 status='nominations_open', slating_manager=self.manager)
        self.treas = SlatingPosition.objects.create(period=self.open, title='Treasurer', code='T', display_order=1)
        self.sec = SlatingPosition.objects.create(period=self.open, title='Secretary', code='S', display_order=2)
        SlatingPosition.objects.create(period=self.open, title='Hidden', code='H', is_active=False)
        self.why = SlatingFormField.objects.create(
            period=self.open, field_name='why', label='Why?', field_type='textarea', is_required=True,
            section='About you', display_order=1,
            validation_rules=[{'type': 'min_length', 'value': 5}])
        self.years = SlatingFormField.objects.create(
            period=self.open, field_name='years', label='Years', field_type='number', section='About you',
            display_order=2)
        self.skills = SlatingFormField.objects.create(
            period=self.open, field_name='skills', label='Skills', field_type='multiselect',
            options=['Money', 'Words'], display_order=3)
        self.photo = SlatingFormField.objects.create(
            period=self.open, field_name='photo', label='Photo', field_type='image', display_order=4)
        SlatingFormField.objects.create(period=self.open, field_name='gpa', label='GPA', field_type='gpa',
                                        display_order=5)
        SlatingFormField.objects.create(period=self.open, field_name='old', label='Old', field_type='text',
                                        is_active=False)
        self.applicant = member('sr-applicant')
        self.path = f'slating/periods/{self.open.id}/apply/'

    def body(self, **kw):
        out = {
            'action': 'submit',
            'reported_gpa': '3.4',
            'positions_first_choice': [self.treas.id],
            'positions_do_not_want': [self.sec.id],
            f'field_{self.why.id}': 'Because I care',
            f'field_{self.years.id}': '2',
            f'field_{self.skills.id}': ['Money'],
        }
        out.update(kw)
        return out

    def test_form_matches_the_web_page(self):
        r = self.call('get', self.path, user=self.applicant)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        web = Client()
        web.force_login(self.applicant)
        ctx = web.get(reverse('slating_apply', args=[self.open.id])).context
        self.assertEqual([s['title'] for s in body['sections']], list(ctx['sections'].keys()))
        self.assertEqual([f['id'] for s in body['sections'] for f in s['fields']], [f.id for f in ctx['form_fields']])
        self.assertEqual([p['id'] for p in body['positions']], [p.id for p in ctx['positions']])
        self.assertIsNone(body['application'])
        why = body['sections'][0]['fields'][0]
        self.assertEqual((why['name'], why['type'], why['required']), (f'field_{self.why.id}', 'textarea', True))
        self.assertEqual(why['validation_rules'], [{'type': 'min_length', 'value': 5}])

    def test_gates_in_the_web_order(self):
        advisor = member('sr-advisor', member_type='Advisor')
        r = self.call('get', self.path, user=advisor)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'apply_ineligible'))
        self.assertEqual(self.call('get', self.path).status_code, 200)   # applied in another period only
        SlatingApplication.objects.create(period=self.open, applicant=self.voter, status='submitted')
        r = self.call('post', self.path, self.body())
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_applied'))
        self.open.status = 'deliberation'
        self.open.save()
        r = self.call('get', self.path, user=self.applicant)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'apply_closed'))

    def test_submit_saves_like_the_web_and_logs(self):
        r = self.call('post', self.path, self.body(), user=self.applicant)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'submitted')
        app = SlatingApplication.objects.get(period=self.open, applicant=self.applicant)
        self.assertEqual(app.status, 'submitted')
        self.assertIsNotNone(app.submitted_at)
        self.assertEqual(app.gpa_level, app.calculate_gpa_level())
        self.assertEqual(app.position_preferences, {'first_choice': [self.treas.id], 'second_choice': [],
                                                    'third_choice': [], 'do_not_want': [self.sec.id]})
        answers = {r.field_id: r for r in app.responses.all()}
        self.assertEqual(answers[self.why.id].text_value, 'Because I care')
        self.assertEqual(int(answers[self.years.id].number_value), 2)
        self.assertEqual(answers[self.skills.id].json_value, ['Money'])
        self.assertTrue(SlatingActivity.objects.filter(
            period=self.open, user=self.applicant, action='application_submitted').exists())
        # Then it's past draft: the form is closed to them, as on the web.
        self.assertEqual(self.call('get', self.path, user=self.applicant).json()['error'], 'already_applied')

    def test_submit_with_problems_saves_nothing(self):
        r = self.call('post', self.path, self.body(positions_first_choice=[], positions_do_not_want=[],
                                                   **{f'field_{self.why.id}': 'Hi',
                                                      f'field_{self.years.id}': 'two'}),
                      user=self.applicant)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        detail = r.json()['detail']
        self.assertIn('at least one position', detail)
        self.assertIn('Why?: Must be at least 5 characters', detail)
        self.assertIn('Years: Must be a valid number', detail)
        self.assertFalse(SlatingApplication.objects.filter(applicant=self.applicant).exists())
        self.assertFalse(SlatingActivity.objects.filter(user=self.applicant).exists())

    def test_draft_keeps_what_validated_then_continues(self):
        r = self.call('post', self.path, self.body(action='save_draft', **{f'field_{self.years.id}': 'two'}),
                      user=self.applicant)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'draft')
        self.assertEqual(r.json()['notes'], ['Years: Must be a valid number'])
        app = SlatingApplication.objects.get(applicant=self.applicant)
        self.assertFalse(app.responses.filter(field=self.years).exists())
        self.assertFalse(SlatingActivity.objects.filter(user=self.applicant).exists())
        form = self.call('get', self.path, user=self.applicant).json()
        self.assertEqual(form['application']['status'], 'draft')
        self.assertEqual(form['application']['preferences']['first_choice'], [self.treas.id])
        self.assertEqual(form['answers'][str(self.why.id)], {'text': 'Because I care'})
        self.assertEqual(form['answers'][str(self.skills.id)], {'list': ['Money']})
        self.assertEqual(self.call('post', self.path, self.body(), user=self.applicant).json()['status'], 'submitted')

    def test_multipart_with_files(self):
        body = self.body()
        body['gpa_screenshot'] = _png('gpa.png')
        body[f'field_{self.photo.id}'] = _png('me.png')
        r = self.call('post', self.path, body, user=self.applicant, multipart=True)
        self.assertEqual(r.status_code, 200, r.content)
        app = SlatingApplication.objects.get(applicant=self.applicant)
        self.assertTrue(app.gpa_screenshot)
        self.assertTrue(SlatingApplicationResponse.objects.get(application=app, field=self.photo).file_value)
        form_answer = self.call('get', 'slating/my-applications/', user=self.applicant).json()
        self.assertEqual(form_answer['applications'][0]['status'], 'submitted')

    def test_a_disguised_file_is_refused(self):
        body = self.body()
        body[f'field_{self.photo.id}'] = SimpleUploadedFile('me.png', b'<html>hi</html>', content_type='image/png')
        r = self.call('post', self.path, body, user=self.applicant, multipart=True)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('Photo:', r.json()['detail'])

    def test_bad_action(self):
        r = self.call('post', self.path, self.body(action='publish'), user=self.applicant)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_the_web_form_still_submits(self):
        web = Client()
        web.force_login(self.applicant)
        data = self.body()
        r = web.post(reverse('slating_apply', args=[self.open.id]), data)
        self.assertRedirects(r, reverse('slating_my_applications'), fetch_redirect_response=False)
        self.assertEqual(SlatingApplication.objects.get(applicant=self.applicant).status, 'submitted')


class MyApplicationsTests(_AppBase):

    def test_matches_the_web_list(self):
        open_period = SlatingPeriod.objects.create(name='Spring', status='nominations_open')
        draft = SlatingApplication.objects.create(period=open_period, applicant=self.voter, status='draft',
                                                  position_preferences={'first_choice': [1, 2]})
        SlatingInterview.objects.create(application=self.app, location='Library', notes='CONFIDENTIAL')
        r = self.call('get', 'slating/my-applications/')
        self.assertEqual(r.status_code, 200, r.content)
        rows = {a['id']: a for a in r.json()['applications']}
        web = Client()
        web.force_login(self.voter)
        ctx = web.get(reverse('slating_my_applications')).context
        self.assertEqual(list(rows), [a.id for a in ctx['applications']])
        self.assertEqual(rows[draft.id]['positions_count'], 2)
        self.assertTrue(rows[draft.id]['can_continue'])
        self.assertTrue(rows[draft.id]['can_withdraw'])
        # Voting-open period: the web shows no Withdraw.
        self.assertFalse(rows[self.app.id]['can_withdraw'])
        self.assertEqual(rows[self.app.id]['interview'], {'scheduled_at': None, 'location': 'Library',
                                                         'completed': False})
        self.assertNotIn('CONFIDENTIAL', r.content.decode())


# ── The committee: applications and review ──────────────────────────────────

class ReviewTests(_AppBase):

    def setUp(self):
        super().setUp()
        self.chair = member('sr-chair')
        self.committee.chairs.add(self.chair)
        self.list_path = f'slating/periods/{self.period.id}/applications/'
        self.detail_path = f'{self.list_path}{self.app.id}/'
        self.secret = SlatingFormField.objects.create(period=self.period, field_name='s', label='Secret',
                                                      field_type='text', show_in_review=False)
        self.shown = SlatingFormField.objects.create(period=self.period, field_name='a', label='Answer',
                                                     field_type='text', section='Plans')
        SlatingApplicationResponse.objects.create(application=self.app, field=self.secret, text_value='hidden')
        SlatingApplicationResponse.objects.create(application=self.app, field=self.shown, text_value='visible')
        self.app.review_notes = 'Private review'
        self.app.position_preferences = {'first_choice': [self.pres.id], 'do_not_want': [self.vp.id]}
        self.app.save()

    def test_who_can_see_applications(self):
        self.assertEqual(self.call('get', self.list_path).status_code, 403)                     # a member
        self.assertEqual(self.call('get', self.list_path, user=self.admin).status_code, 403)    # locked period
        self.assertEqual(self.call('get', self.list_path, user=self.committee_member).status_code, 200)
        self.assertEqual(self.call('get', self.list_path, user=self.manager).status_code, 200)
        self.assertEqual(self.call('get', self.detail_path, user=self.admin).status_code, 403)

    def test_list_matches_the_web_and_filters(self):
        other = SlatingApplication.objects.create(period=self.period, applicant=member('sr-other'),
                                                  status='withdrawn', gpa_level=2)
        SlatingApplication.objects.create(period=self.period, applicant=member('sr-draft'), status='draft')
        body = self.call('get', self.list_path, user=self.committee_member).json()
        web = Client()
        web.force_login(self.committee_member)
        ctx = web.get(reverse('slating_applications', args=[self.period.id])).context
        self.assertEqual([a['id'] for a in body['applications']], [a.id for a in ctx['applications']])
        self.assertEqual({c['status']: c['count'] for c in body['status_counts']},
                         {k: v['count'] for k, v in ctx['status_counts'].items()})
        self.assertFalse(body['period']['can_manage'])
        row = next(a for a in body['applications'] if a['id'] == self.app.id)
        self.assertEqual(row['first_choice'], ['President'])
        only = self.call('get', self.list_path + '?status=withdrawn', user=self.committee_member).json()
        self.assertEqual([a['id'] for a in only['applications']], [other.id])
        by_pos = self.call('get', f'{self.list_path}?position={self.pres.id}', user=self.committee_member).json()
        self.assertEqual([a['id'] for a in by_pos['applications']], [self.app.id])
        by_gpa = self.call('get', self.list_path + '?gpa_level=2', user=self.committee_member).json()
        self.assertEqual([a['id'] for a in by_gpa['applications']], [other.id])
        r = self.call('get', self.list_path + '?position=abc', user=self.committee_member)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_detail_hides_what_the_web_hides(self):
        body = self.call('get', self.detail_path, user=self.committee_member).json()
        labels = [a['label'] for s in body['sections'] for a in s['answers']]
        self.assertEqual(labels, ['Answer'])
        self.assertEqual(body['sections'][0]['title'], 'Plans')
        self.assertFalse(body['can_review'])
        self.assertIsNone(body['application']['review_notes'])
        self.assertEqual([p['title'] for p in body['preferences']['first_choice']], ['President'])
        self.assertEqual([p['title'] for p in body['preferences']['do_not_want']], ['VP'])
        chair = self.call('get', self.detail_path, user=self.chair).json()
        self.assertTrue(chair['can_review'])
        self.assertEqual(chair['application']['review_notes'], 'Private review')

    def test_review_is_for_managers_and_logs_status_changes(self):
        path = self.detail_path + 'review/'
        r = self.call('post', path, {'status': 'under_review'}, user=self.committee_member)
        self.assertEqual(r.status_code, 403)
        r = self.call('post', path, {'status': 'nope'}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', path, {'status': 'under_review', 'review_notes': ' Strong ', 'verify_gpa': True},
                      user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.app.refresh_from_db()
        self.assertEqual((self.app.status, self.app.review_notes, self.app.reviewer, self.app.gpa_verified),
                         ('under_review', 'Strong', self.manager, True))
        log = SlatingActivity.objects.get(action='application_reviewed')
        self.assertEqual(log.metadata, {'application_id': self.app.id, 'old_status': 'submitted',
                                        'new_status': 'under_review'})
        # Same status again: no second log entry (the web's rule).
        self.call('post', path, {'status': 'under_review', 'unverify_gpa': True}, user=self.manager)
        self.assertEqual(SlatingActivity.objects.filter(action='application_reviewed').count(), 1)
        self.app.refresh_from_db()
        self.assertFalse(self.app.gpa_verified)
        self.assertEqual(self.app.review_notes, 'Strong')   # not sent: unchanged


# ── Interviews ───────────────────────────────────────────────────────────────

class InterviewTests(_AppBase):

    def setUp(self):
        super().setUp()
        self.committee.members.add(member('sr-cm2'))
        self.committee.members.add(member('sr-cm-pledge', member_type='Pledge'))

    def test_list_matches_the_web(self):
        other = SlatingApplication.objects.create(period=self.period, applicant=member('sr-x'), status='submitted')
        SlatingInterview.objects.create(application=self.app, location='Room 1')
        r = self.call('get', f'slating/periods/{self.period.id}/interviews/', user=self.committee_member)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        web = Client()
        web.force_login(self.committee_member)
        ctx = web.get(reverse('slating_interviews', args=[self.period.id])).context
        self.assertEqual([i['id'] for i in body['interviews']], [i.id for i in ctx['interviews']])
        self.assertEqual([a['id'] for a in body['without_interview']], [other.id])
        self.assertEqual(sorted(m['user_id'] for m in body['interviewers']),
                         sorted(m.user_id for m in ctx['interviewers_list']))
        self.assertNotIn('sr-cm-pledge', [m['user_id'] for m in body['interviewers']])
        self.assertEqual((body['notes_remaining'], body['can_manage']), (1, False))
        self.assertEqual(self.call('get', f'slating/periods/{self.period.id}/interviews/').status_code, 403)
        r = self.call('get', f'slating/periods/{self.period.id}/interviews/?status=bogus', user=self.manager)
        self.assertEqual(r.json()['error'], 'invalid_input')

    def test_schedule_is_for_the_committee(self):
        path = f'slating/applications/{self.app.id}/interview/'
        # 11-06-26: any committee member may schedule, not only the managers.
        self.assertEqual(self.call('get', path, user=self.committee_member).status_code, 200)
        self.assertEqual(self.call('get', path, user=self.admin).status_code, 403)
        r = self.call('post', path, {'scheduled_at': 'next tuesday'}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', path, {'scheduled_at': '2026-11-05T19:00:00Z', 'location': ' Library ',
                                     'interviewers': ['sr-cm', 'sr-cm-pledge']}, user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        interview = SlatingInterview.objects.get(application=self.app)
        self.assertEqual(interview.location, 'Library')
        self.assertEqual([u.user_id for u in interview.interviewers.all()], ['sr-cm'])
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'interview_scheduled')
        self.assertTrue(SlatingActivity.objects.filter(action='interview_scheduled', user=self.manager).exists())

    def test_pledges_are_not_interviewed(self):
        pledge_app = SlatingApplication.objects.create(period=self.period, status='submitted',
                                                       applicant=member('sr-pl', member_type='Pledge'))
        r = self.call('get', f'slating/applications/{pledge_app.id}/interview/', user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'pledge_applicant'))

    def test_complete_is_for_the_committee(self):
        interview = SlatingInterview.objects.create(application=self.app, notes='CONFIDENTIAL')
        self.app.status = 'interview_scheduled'
        self.app.save()
        path = f'slating/interviews/{interview.id}/'
        for outsider in (self.voter, self.admin):
            r = self.call('get', path, user=outsider)
            self.assertEqual(r.status_code, 403)
            self.assertNotIn('CONFIDENTIAL', r.content.decode())
        body = self.call('get', path, user=self.committee_member).json()
        self.assertEqual(body['interview']['notes'], 'CONFIDENTIAL')
        r = self.call('post', path, {'recommendation': 'maybe'}, user=self.committee_member)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', path, {'notes': 'Good', 'strengths': 'Calm', 'concerns': '', 'recommendation':
                                     'recommend', 'recommended_positions': [self.pres.id]}, user=self.committee_member)
        self.assertEqual(r.status_code, 200, r.content)
        interview.refresh_from_db()
        self.assertEqual((interview.notes, interview.recommendation), ('Good', 'recommend'))
        self.assertIsNotNone(interview.completed_at)
        self.assertEqual(list(interview.recommended_positions.all()), [self.pres])
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'interviewed')
        log = SlatingActivity.objects.get(action='interview_completed')
        self.assertEqual(log.metadata['recommendation'], 'recommend')

    def test_destroy_notes(self):
        interview = SlatingInterview.objects.create(application=self.app, notes='CONFIDENTIAL', strengths='x')
        path = f'slating/periods/{self.period.id}/interviews/destroy-notes/'
        self.assertEqual(self.call('post', path, {'confirm': 'DESTROY'}, user=self.committee_member).status_code, 403)
        r = self.call('post', path, {'confirm': 'yes'}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', path, {'confirm': 'DESTROY'}, user=self.manager)
        self.assertEqual(r.json(), {'ok': True, 'destroyed': 1})
        interview.refresh_from_db()
        self.assertTrue(interview.notes_destroyed)
        self.assertEqual((interview.strengths, interview.destroyed_by), ('', self.manager))
        self.assertTrue(SlatingActivity.objects.filter(action='notes_destroyed', metadata={'count': 1}).exists())


# ── Attendance, paper ballots, results ───────────────────────────────────────

class ChairToolsTests(_AppBase):

    def test_attendance(self):
        path = f'slating/periods/{self.period.id}/attendance/'
        self.assertEqual(self.call('get', path, user=self.committee_member).status_code, 403)
        body = self.call('get', path, user=self.manager).json()
        web = Client()
        web.force_login(self.manager)
        ctx = web.get(reverse('slating_attendance', args=[self.period.id])).context
        self.assertEqual([m['user_id'] for m in body['members']],
                         [e['member'].user_id for e in ctx['members_with_status']])
        self.assertEqual((body['present_count'], body['total_eligible']), (ctx['present_count'], ctx['total_eligible']))
        r = self.call('post', path, {'action': 'mark_present', 'member_id': 'sr-cm'}, user=self.manager)
        self.assertEqual(r.json()['present_count'], 2)
        attendance = SlatingAttendance.objects.get(member=self.committee_member)
        self.assertEqual(attendance.marked_by, self.manager)
        r = self.call('post', path, {'action': 'mark_absent', 'member_id': 'sr-voter'}, user=self.manager)
        self.assertEqual(r.json()['present_count'], 1)
        self.assertEqual(self.call('post', path, {'action': 'mark_present', 'member_id': 'nobody'},
                                   user=self.manager).status_code, 404)
        self.assertEqual(self.call('post', path, {'action': 'dance', 'member_id': 'sr-cm'},
                                   user=self.manager).json()['error'], 'invalid_input')
        self.period.status = 'results_published'
        self.period.save()
        r = self.call('get', path, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'attendance_closed'))

    def test_paper_ballots_for_a_slate_round(self):
        path = f'slating/periods/{self.period.id}/paper-ballots/'
        self.assertEqual(self.call('get', path, user=self.committee_member).status_code, 403)
        body = self.call('get', path, user=self.manager).json()
        self.assertEqual((body['attempt'], body['individual'], body['ballots']), (1, False, 0))
        self.assertEqual(body['rows'][0]['key'], 'slate')
        r = self.call('post', path, {'approve': 'x'}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', path, {'approve': 3, 'reject': 1, 'abstain': '', 'note': 'Two phones died'},
                      user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['ballots'], r.json()['rows'][0]['counts']),
                         (4, {'approve': 3, 'reject': 1, 'abstain': 0}))
        row = SlatingPaperBallots.objects.get()
        self.assertEqual((row.ballots, row.note, row.recorded_by), (4, 'Two phones died', self.manager))
        self.assertTrue(SlatingActivity.objects.filter(action='vote_cast', metadata__paper_ballots=4).exists())
        self.period.status = 'results_published'
        self.period.save()
        self.assertEqual(self.call('get', path, user=self.manager).json()['error'], 'paper_closed')

    def test_results_summary_matches_the_web(self):
        path = f'slating/periods/{self.period.id}/results/summary/'
        self.assertEqual(self.call('get', path, user=self.committee_member).status_code, 403)
        self.assertEqual(self.call('get', path, user=self.admin).status_code, 403)
        body = self.call('get', path, user=self.manager).json()
        web = Client()
        web.force_login(self.manager)
        ctx = web.get(reverse('slating_results_summary', args=[self.period.id])).context
        self.assertEqual(body['app_stats'], ctx['app_stats'])
        self.assertEqual((body['eligible_voters'], body['total_unique_voters']),
                         (ctx['eligible_voters'], ctx['total_unique_voters']))
        self.assertEqual([s['id'] for s in body['slates']], [s.id for s in ctx['slates']])
        self.assertEqual(len(body['voting_history']), len(ctx['voting_history']))
        self.assertFalse(body['can_publish'])   # 11-05-26: voting is still open
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_closed')
        self.assertTrue(self.call('get', path, user=self.manager).json()['can_publish'])

    @mock.patch('src.notification_service.notify_all_active_members')
    def test_publish(self, notify):
        path = f'slating/periods/{self.period.id}/results/publish/'
        self.assertEqual(self.call('post', path, {}, user=self.committee_member).status_code, 403)
        self.assertEqual(self.call('post', path, {}, user=self.voter).status_code, 403)
        # 11-05-26: not while voting is open.
        r = self.call('post', path, {}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'not_publishable'))
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_closed')
        r = self.call('post', path, {}, user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'results_published')
        self.assertIsNotNone(self.period.results_publish_at)
        self.assertTrue(SlatingActivity.objects.filter(action='results_published', user=self.manager).exists())
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs['link'], f'/slating/period/{self.period.id}/results/')
        self.assertEqual(notify.call_args.kwargs['exclude_user'], self.manager)
        self.assertFalse(r.json()['can_publish'])
        r = self.call('post', path, {}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'not_publishable'))

    def test_chair_overview_says_what_is_open(self):
        body = self.call('get', f'slating/periods/{self.period.id}/chair/', user=self.manager).json()
        # 11-05-26: publishing waits for voting to close.
        self.assertEqual((body['can_attendance'], body['can_paper'], body['can_publish']), (True, True, False))
        self.assertIn('web', body)   # older builds' keys stay


class UnlockedPeriodTests(_AppBase):
    """No manager or committee admin: a site admin still gets in (the web's rule)."""

    def test_site_admin_in_an_unlocked_period(self):
        period = SlatingPeriod.objects.create(name='Open', status='deliberation')
        for path in (f'slating/periods/{period.id}/applications/', f'slating/periods/{period.id}/attendance/',
                     f'slating/periods/{period.id}/results/summary/'):
            self.assertEqual(self.call('get', path, user=self.admin).status_code, 200, path)
            self.assertEqual(self.call('get', path).status_code, 403, path)
