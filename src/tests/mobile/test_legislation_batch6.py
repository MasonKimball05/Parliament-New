"""
v4.4.2 — chapter legislation for authors and officers in the app
(src/mobile/views_legislation_manage.py), through the website's own code
(src/chapter_votes.py).

What must hold: each endpoint lets in exactly whom the web view lets in (and
in its order); creating, ending, reopening, running off and opening a vote
write what the website writes — status, ``passed``, the activity log, the
voters' notifications — and receipts cast before the close still verify; the
author's tally and the officers' turnout match the vote page's; the edit,
new-version, tracker and appointment tools refuse and save as the web does.

    python manage.py test src.tests.mobile.test_legislation_batch6
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src import ballots
from src.models import ActivityLog, Attendance, Legislation, Notification, Role, Vote
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'
LONG = 'A detailed description of the bill, well over twenty characters.'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.chair = make_user('b6-chair', member_type='Chair')
        self.officer = make_user('b6-officer', member_type='Officer')
        self.member = make_user('b6-member')
        self.admin = make_user('b6-admin', is_admin=True)
        self.tokens = {u.pk: make_device(u)[1] for u in (self.chair, self.officer, self.member, self.admin)}
        self.api = Client()
        self.web = Client()

    def bill(self, title='Bill', posted_by=None, **kw):
        now = timezone.now()
        fields = dict(title=title, description=LONG, posted_by=posted_by or self.chair,
                      available_at=now - timedelta(hours=1), voting_starts_at=now - timedelta(hours=1))
        fields.update(kw)
        return Legislation.objects.create(**fields)

    def get(self, path, user=None):
        return self.api.get(API + path, **bearer(self.tokens[(user or self.chair).pk]))

    def post(self, path, body=None, user=None):
        return self.api.post(API + path, body or {}, content_type='application/json',
                             **bearer(self.tokens[(user or self.chair).pk]))

    def vote(self, user, leg, choices):
        Attendance.objects.create(user=user, status='present')
        return ballots.cast(user, leg, choices)


class ManageListTests(_Base):

    def test_author_sees_own_bill_with_tally_and_actions(self):
        leg = self.bill('Mine')
        self.vote(self.member, leg, ['yes'])
        self.bill('Someone else', posted_by=self.officer)
        body = self.get('votes/manage/').json()
        self.assertTrue(body['can_upload'])
        mine = next(b for b in body['bills'] if b['title'] == 'Mine')
        self.assertEqual({r['option']: r['count'] for r in mine['tally']}, {'yes': 1, 'no': 0, 'abstain': 0})
        self.assertTrue(mine['can_end'])
        self.assertFalse(mine['can_open_now'])
        other = next(b for b in body['bills'] if b['title'] == 'Someone else')
        # Officers and chairs see everyone's turnout, but not the tally or the buttons.
        self.assertIsNone(other['tally'])
        self.assertFalse(other['can_end'])
        self.assertEqual(other['turnout']['voted'], 0)

    def test_turnout_lists_present_members_who_have_not_voted(self):
        leg = self.bill('Turnout')
        Attendance.objects.create(user=self.officer, status='present')
        self.vote(self.member, leg, ['no'])
        row = next(b for b in self.get('votes/manage/').json()['bills'] if b['id'] == leg.id)
        self.assertEqual(row['turnout']['voted'], 1)
        self.assertEqual(row['turnout']['present'], 2)
        self.assertEqual(row['turnout']['not_voted'], [self.officer.get_display_name()])

    def test_a_member_sees_only_their_own(self):
        self.bill('Chair bill')
        body = self.get('votes/manage/', user=self.member).json()
        self.assertEqual(body['bills'], [])
        self.assertFalse(body['can_upload'])

    def test_admin_runs_any_bill(self):
        self.bill('Chair bill')
        row = self.get('votes/manage/', user=self.admin).json()['bills'][0]
        self.assertTrue(row['can_end'])
        self.assertIsNone(row['tally'])   # the tally is the author's, as on the page

    def test_scheduled_bill_offers_now_buttons_and_edit_to_its_author(self):
        later = timezone.now() + timedelta(days=1)
        leg = self.bill('Later', available_at=later, voting_starts_at=later)
        row = next(b for b in self.get('votes/manage/').json()['bills'] if b['id'] == leg.id)
        self.assertEqual((row['can_open_now'], row['can_show_now'], row['can_edit'], row['can_end']),
                         (True, True, True, False))

    def test_page_toggle_applies(self):
        toggle('vote', False)
        self.assertEqual(self.get('votes/manage/').json()['error'], 'page_disabled')


class CreateTests(_Base):

    def body(self, **kw):
        body = {'title': 'New bill', 'description': LONG, 'available_now': True, 'vote_mode': 'percentage',
                'required_percentage': '67'}
        body.update(kw)
        return body

    def test_chair_creates_a_vote_like_the_web_form(self):
        r = self.post('votes/new/', self.body())
        self.assertEqual(r.status_code, 201, r.content)
        app = Legislation.objects.get(title='New bill')
        self.web.force_login(self.chair)
        self.web.post('/vote/upload/', {'title': 'Web bill', 'description': LONG, 'available_at_is_now': '1',
                                        'available_at': '2026-01-01T10:00', 'required_percentage': '67',
                                        'vote_mode': 'percentage'})
        web = Legislation.objects.get(title='Web bill')
        for field in ('posted_by_id', 'required_percentage', 'vote_mode', 'anonymous_vote', 'allow_abstain',
                      'voting_manual_open', 'status', 'document'):
            self.assertEqual(getattr(app, field), getattr(web, field), field)
        self.assertTrue(r.json()['message'].startswith('Legislation uploaded — visible now'))
        self.assertTrue(ActivityLog.objects.filter(action_type='legislation_created', object_id=app.id).exists())

    def test_plurality_options_and_limits(self):
        r = self.post('votes/new/', self.body(vote_mode='plurality', description='short',
                                              plurality_options=['A', 'B', 'C'], plurality_votes_allowed=9,
                                              plurality_runoff_enabled=True, plurality_runoff_count=5))
        self.assertEqual(r.status_code, 201, r.content)
        leg = Legislation.objects.get(title='New bill')
        self.assertEqual((leg.plurality_options, leg.plurality_votes_allowed, leg.plurality_runoff_count),
                         (['A', 'B', 'C'], 3, 3))

    def test_multipart_takes_the_same_fields_and_checks_the_document(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        auth = bearer(self.tokens[self.chair.pk])
        r = self.api.post(API + 'votes/new/', {'title': 'Multi', 'description': 'short', 'available_now': 'true',
                                               'vote_mode': 'plurality', 'plurality_options': ['X', 'Y']}, **auth)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Legislation.objects.get(title='Multi').plurality_options, ['X', 'Y'])
        fake = SimpleUploadedFile('bill.pdf', b'not really a pdf', content_type='application/pdf')
        r = self.api.post(API + 'votes/new/', {'title': 'Fake', 'description': LONG, 'available_now': 'true',
                                               'document': fake}, **auth)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertFalse(Legislation.objects.filter(title='Fake').exists())

    def test_separate_voting_waits_for_open_now(self):
        self.post('votes/new/', self.body(separate_voting=True))
        leg = Legislation.objects.get(title='New bill')
        self.assertTrue(leg.voting_manual_open)
        self.assertFalse(leg.voting_has_started())

    def test_validation(self):
        r = self.post('votes/new/', self.body(description='too short'))
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('NOT saved', r.json()['detail'])
        r = self.post('votes/new/', self.body(vote_mode='plurality', plurality_options=['Only one']))
        self.assertIn('at least two options', r.json()['detail'])
        r = self.post('votes/new/', self.body(vote_mode='piecewise', required_number='0'))
        self.assertIn('Piecewise', r.json()['detail'])
        r = self.post('votes/new/', self.body(available_now=False, available_at='not a date'))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Legislation.objects.exists())

    def test_members_flag_and_pledge_restriction(self):
        r = self.post('votes/new/', self.body(), user=self.member)
        self.assertEqual(r.status_code, 403)
        # The form's own route carries the pledge restriction.
        restrict('upload_chapter_legislation')
        pledge = make_user('b6-pledge', member_type='Pledge')
        self.tokens[pledge.pk] = make_device(pledge)[1]
        self.assertEqual(self.post('votes/new/', self.body(), user=pledge).json()['error'], 'pledge_restricted')
        flag('legislation_voting', False)
        self.assertEqual(self.post('votes/new/', self.body()).json()['error'], 'feature_disabled')

    def test_appointment(self):
        role = Role.objects.create(name='Social Chair', code='SOC')
        r = self.post('votes/appointments/new/', {'role_id': str(role.id), 'member_id': self.member.pk,
                                                  'available_now': True}, user=self.officer)
        self.assertEqual(r.status_code, 201, r.content)
        leg = Legislation.objects.get(legislation_type='appointment')
        self.assertEqual(leg.title, f'Chair Appointment: {self.member.name} as Social Chair')
        self.assertEqual(leg.appointment_member, self.member)
        # Announced at once, as it is available now.
        self.assertIsNotNone(leg.availability_notified_at)
        r = self.post('votes/appointments/new/', {'member_id': self.member.pk, 'available_now': True},
                      user=self.officer)
        self.assertEqual(r.json()['detail'], 'Please select a role for the appointment.')
        self.assertEqual(self.get('votes/appointments/new/', user=self.member).status_code, 403)


class EndRunoffReopenTests(_Base):

    def test_end_vote_writes_the_outcome_notifies_voters_and_keeps_receipts(self):
        from src.utils.vote_receipts import verify_receipt
        leg = self.bill('Close me')
        receipt = self.vote(self.member, leg, ['yes'])['receipt']
        self.vote(self.officer, leg, ['no'])
        self.assertEqual(self.post(f'votes/{leg.id}/end/', user=self.member).status_code, 403)
        r = self.post(f'votes/{leg.id}/end/')
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()['result']
        self.assertEqual((result['yes'], result['no'], result['passed'], result['yes_percentage']),
                         (1, 1, False, 50))
        leg.refresh_from_db()
        self.assertEqual((leg.voting_closed, leg.status, leg.passed), (True, 'failed', False))
        self.assertEqual(set(Notification.objects.filter(notification_type='vote_ended')
                             .values_list('recipient_id', flat=True)), {self.member.pk, self.officer.pk})
        self.assertTrue(ActivityLog.objects.filter(action_type='vote_ended', object_id=leg.id).exists())
        check = verify_receipt(receipt)
        self.assertTrue(check['valid'] and check['intact'])
        # The page's End Vote is gone once it's closed: no second close.
        self.assertEqual(self.post(f'votes/{leg.id}/end/').json()['error'], 'not_open')
        self.assertEqual(Notification.objects.filter(notification_type='vote_ended').count(), 2)

    def test_end_matches_the_web_view(self):
        app_leg, web_leg = self.bill('App'), self.bill('Web')
        for leg in (app_leg, web_leg):
            Vote.objects.create(user=self.member, legislation=leg, vote_choice='yes')
            Vote.objects.create(user=self.officer, legislation=leg, vote_choice='yes')
            Vote.objects.create(user=self.admin, legislation=leg, vote_choice='no')
        self.post(f'votes/{app_leg.id}/end/')
        self.web.force_login(self.chair)
        self.assertEqual(self.web.post(f'/vote/end/{web_leg.id}/').status_code, 200)
        app_leg.refresh_from_db()
        web_leg.refresh_from_db()
        self.assertEqual((app_leg.status, app_leg.passed, app_leg.voting_closed),
                         (web_leg.status, web_leg.passed, web_leg.voting_closed))
        self.assertEqual(app_leg.status, 'passed')

    def test_plurality_tie_offers_a_runoff(self):
        leg = self.bill('Pick', vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                        plurality_runoff_enabled=True, plurality_runoff_count=2)
        Vote.objects.create(user=self.member, legislation=leg, vote_choice='A')
        Vote.objects.create(user=self.officer, legislation=leg, vote_choice='B')
        body = self.post(f'votes/{leg.id}/end/').json()
        self.assertFalse(body['result']['passed'])
        self.assertTrue(body['result']['has_tie'])
        self.assertTrue(body['can_runoff'])
        self.assertTrue(self.get(f'legislation/passed/{leg.id}/').json()['actions']['runoff'])
        self.assertEqual(self.post(f'votes/{leg.id}/runoff/', user=self.member).status_code, 403)
        r = self.post(f'votes/{leg.id}/runoff/')
        self.assertEqual(r.status_code, 201, r.content)
        runoff = Legislation.objects.get(pk=r.json()['runoff']['id'])
        self.assertEqual((runoff.plurality_options, runoff.plurality_parent_id, runoff.plurality_is_runoff),
                         (['A', 'B'], leg.id, True))
        again = self.post(f'votes/{leg.id}/runoff/')
        self.assertEqual(again.json()['detail'], 'A runoff vote has already been created for this legislation.')
        self.assertFalse(self.get(f'legislation/passed/{leg.id}/').json()['actions']['runoff'])

    def test_runoff_refused_while_open(self):
        leg = self.bill('Open', vote_mode='plurality', plurality_options=['A', 'B'], plurality_runoff_enabled=True)
        r = self.post(f'votes/{leg.id}/runoff/')
        self.assertEqual(r.json()['detail'], 'The original vote must be closed before creating a runoff.')

    def test_open_now(self):
        leg = self.bill('Manual', voting_starts_at=None, voting_manual_open=True)
        self.assertEqual(self.post(f'votes/{leg.id}/open-now/', {'mode': 'open'}, user=self.member).status_code,
                         403)
        r = self.post(f'votes/{leg.id}/open-now/', {'mode': 'open'})
        self.assertTrue(r.json()['changed'])
        leg.refresh_from_db()
        self.assertTrue(leg.voting_has_started())
        self.assertFalse(self.post(f'votes/{leg.id}/open-now/', {'mode': 'open'}).json()['changed'])
        Legislation.objects.filter(pk=leg.pk).update(voting_closed=True)
        self.assertEqual(self.post(f'votes/{leg.id}/open-now/').json()['error'], 'not_open')

    def test_reopen(self):
        leg = self.bill('Failed', voting_closed=True, status='failed')
        self.assertEqual(self.post(f'legislation/{leg.id}/reopen/', user=self.member).status_code, 403)
        self.assertEqual(self.post(f'legislation/{leg.id}/reopen/', user=self.officer).status_code, 403)
        r = self.post(f'legislation/{leg.id}/reopen/')
        self.assertEqual(r.status_code, 200, r.content)
        leg.refresh_from_db()
        self.assertEqual((leg.voting_closed, leg.status), (False, 'draft'))
        passed = self.bill('Passed', voting_closed=True, status='passed', passed=True)
        r = self.post(f'legislation/{passed.id}/reopen/')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))


class AttendanceAndReceiptTests(_Base):

    def test_quick_attendance(self):
        self.assertEqual(self.get('votes/attendance/', user=self.member).status_code, 403)
        r = self.post('votes/attendance/', {'user_id': self.member.pk, 'status': 'present'})
        self.assertEqual(r.json(), {'ok': True, 'status': 'present'})
        row = Attendance.objects.get(user=self.member)
        self.assertEqual((row.status, row.marked_by_id, row.attendance_type), ('present', self.chair.pk, 'committee'))
        self.assertTrue(ballots.attendance_ok(self.member))
        self.post('votes/attendance/', {'user_id': self.member.pk, 'status': 'late'})
        self.assertEqual(Attendance.objects.get(user=self.member).status, 'late')
        status_of = {m['id']: m['status'] for m in self.get('votes/attendance/').json()['members']}
        self.assertEqual(status_of[self.member.pk], 'late')
        self.assertEqual(self.post('votes/attendance/', {'user_id': self.member.pk, 'status': 'gone'}).status_code,
                         400)
        self.assertEqual(self.post('votes/attendance/', {'user_id': 'nobody', 'status': 'late'}).status_code, 404)

    def test_receipt_verification(self):
        leg = self.bill('Receipt')
        receipt = self.vote(self.member, leg, ['yes'])['receipt']
        mine = self.post('votes/receipts/verify/', {'receipt': receipt}, user=self.member).json()
        self.assertEqual((mine['valid'], mine['intact'], mine['is_yours'], mine['legislation']['id']),
                         (True, True, True, leg.id))
        theirs = self.post('votes/receipts/verify/', {'receipt': receipt}, user=self.chair).json()
        self.assertFalse(theirs['is_yours'])
        Vote.objects.filter(legislation=leg).update(vote_choice='no')
        changed = self.post('votes/receipts/verify/', {'receipt': receipt}).json()
        self.assertEqual((changed['valid'], changed['intact']), (True, False))
        self.assertFalse(self.post('votes/receipts/verify/', {'receipt': 'junk'}).json()['valid'])


class EditAndVersionTests(_Base):

    def form(self, **kw):
        later = (timezone.now() + timedelta(days=2)).isoformat()
        body = {'title': 'Edited', 'description': LONG, 'available_at': later, 'required_percentage': '60'}
        body.update(kw)
        return body

    def test_author_edits_a_scheduled_bill(self):
        later = timezone.now() + timedelta(days=1)
        leg = self.bill('Draft', posted_by=self.member, available_at=later, voting_starts_at=later)
        r = self.post(f'legislation/{leg.id}/edit/', self.form(), user=self.member)
        self.assertEqual(r.status_code, 200, r.content)
        leg.refresh_from_db()
        self.assertEqual((leg.title, leg.required_percentage), ('Edited', '60'))
        self.assertEqual(self.post(f'legislation/{leg.id}/edit/', self.form(description='short'),
                                   user=self.member).json()['error'], 'invalid_input')

    def test_refusals_follow_the_web_order(self):
        leg = self.bill('Open', posted_by=self.member)
        self.assertEqual(self.get(f'legislation/{leg.id}/edit/', user=self.officer).json()['error'],
                         'not_editable')   # voting has started
        self.assertEqual(self.get(f'legislation/{leg.id}/edit/', user=self.admin).status_code, 200)
        stranger = make_user('b6-stranger')
        self.tokens[stranger.pk] = make_device(stranger)[1]
        self.assertEqual(self.get(f'legislation/{leg.id}/edit/', user=stranger).status_code, 403)
        passed = self.bill('Passed', status='passed')
        self.assertIn('submit a new version', self.get(f'legislation/{passed.id}/edit/').json()['detail'])

    def test_tabled_bill_status_and_co_authors(self):
        leg = self.bill('Tabled', status='tabled', voting_closed=True)
        r = self.post(f'legislation/{leg.id}/edit/',
                      self.form(status='active', co_author_ids=[self.member.pk]), user=self.officer)
        self.assertEqual(r.json()['message'], 'Legislation has been updated and reintroduced to voting.')
        leg.refresh_from_db()
        self.assertEqual((leg.status, leg.voting_closed), ('active', False))
        self.assertEqual(list(leg.co_authors.all()), [self.member])

    def test_mark_voted_and_transfer(self):
        leg = self.bill('Pending', status='pending', posted_by=self.member)
        r = self.post(f'legislation/{leg.id}/edit/', {'action': 'mark_voted', 'outcome': 'passed', 'yes': 9},
                      user=self.member)
        self.assertEqual(r.status_code, 403)
        leg2 = self.bill('Pending 2', status='pending', posted_by=self.member)
        r = self.post(f'legislation/{leg2.id}/edit/', {'action': 'transfer_authorship',
                                                       'transfer_to_id': self.officer.pk}, user=self.member)
        self.assertEqual(r.status_code, 200, r.content)
        leg2.refresh_from_db()
        self.assertEqual(leg2.posted_by, self.officer)
        self.assertEqual(list(leg2.co_authors.all()), [self.member])
        r = self.post(f'legislation/{leg.id}/edit/', {'action': 'mark_voted', 'outcome': 'passed', 'yes': 9},
                      user=self.officer)
        leg.refresh_from_db()
        self.assertEqual((leg.status, leg.passed, leg.voting_closed, leg.historical_yes_votes),
                         ('passed', True, True, 9))

    def test_new_version(self):
        leg = self.bill('Old', status='passed', passed=True, voting_closed=True)
        self.assertEqual(self.post(f'legislation/{leg.id}/new-version/', self.form(), user=self.officer)
                         .status_code, 403)
        member_bill = self.bill('Member', posted_by=self.member, status='passed')
        self.assertEqual(self.get(f'legislation/{member_bill.id}/new-version/', user=self.member).status_code, 403)
        r = self.post(f'legislation/{leg.id}/new-version/', self.form(title='Old v2'))
        self.assertEqual(r.status_code, 201, r.content)
        new = Legislation.objects.get(title='Old v2')
        self.assertEqual((new.posted_by, new.status, bool(new.document)), (self.chair, 'draft', False))
        self.assertTrue(self.get(f'legislation/passed/{leg.id}/').json()['actions']['new_version'])
        self.assertFalse(self.get(f'legislation/passed/{leg.id}/', user=self.officer).json()['actions']['new_version'])


class TrackerAndAssignTests(_Base):

    def test_add_and_note(self):
        body = {'title': 'Old bill', 'status': 'passed', 'description': LONG, 'include_votes': True,
                'yes_votes': 10, 'no_votes': 2}
        self.assertEqual(self.post('legislation/passed/add/', body, user=self.chair).status_code, 403)
        r = self.post('legislation/passed/add/', body, user=self.officer)
        self.assertEqual(r.status_code, 201, r.content)
        leg = Legislation.objects.get(title='Old bill')
        self.assertEqual((leg.status, leg.passed, leg.voting_closed, leg.historical_yes_votes),
                         ('passed', True, True, 10))
        r = self.post('legislation/passed/add/', {'title': 'x', 'description': 'short'}, user=self.officer)
        self.assertEqual(r.json()['error'], 'invalid_input')
        self.assertEqual(self.post(f'legislation/{leg.id}/note/', {'note': 'n'}, user=self.chair).status_code, 403)
        r = self.post(f'legislation/{leg.id}/note/', {'note': '  Amended later. '}, user=self.officer)
        self.assertEqual(r.json(), {'ok': True, 'note': 'Amended later.'})
        leg.refresh_from_db()
        self.assertEqual((leg.admin_note, leg.status), ('Amended later.', 'passed'))

    def test_assign_appointment(self):
        role = Role.objects.create(name='Risk Chair', code='RISK')
        leg = self.bill('Appt', legislation_type='appointment', appointment_role=role,
                        appointment_member=self.member)
        self.assertEqual(self.get(f'legislation/{leg.id}/assign/', user=self.officer).json()['error'],
                         'not_assignable')   # still open
        Legislation.objects.filter(pk=leg.pk).update(voting_closed=True, passed=True, status='passed')
        self.assertEqual(self.post(f'legislation/{leg.id}/assign/', user=self.member).status_code, 403)
        # Chairs pass @officer_required but don't hand out roles.
        self.assertEqual(self.post(f'legislation/{leg.id}/assign/', user=self.chair).status_code, 403)
        r = self.post(f'legislation/{leg.id}/assign/', user=self.officer)
        self.assertEqual(r.status_code, 200, r.content)
        self.member.refresh_from_db()
        self.assertTrue(self.member.roles.filter(pk=role.pk).exists())
        self.assertEqual(self.member.member_type, 'Chair')
        self.assertTrue(ActivityLog.objects.filter(metadata__action='assign_appointment').exists())
        self.assertEqual(self.post(f'legislation/{leg.id}/assign/', user=self.officer).json()['error'],
                         'not_assignable')


class DetailActionsTests(_Base):
    """The results screen's tools: each the web view's own rule."""

    def actions(self, leg, user):
        return self.get(f'legislation/passed/{leg.id}/', user=user).json()['actions']

    def test_open_bill(self):
        leg = self.bill('Open')
        self.assertEqual(self.actions(leg, self.chair), {
            'edit': False, 'note': False, 'end': True, 'reopen': False, 'runoff': False,
            'new_version': False, 'assign': False})
        self.assertTrue(self.actions(leg, self.admin)['edit'])   # admins edit after voting starts
        self.assertEqual(set(k for k, v in self.actions(leg, self.member).items() if v), set())
        self.assertEqual(set(k for k, v in self.actions(leg, self.officer).items() if v), {'note'})

    def test_failed_and_passed(self):
        failed = self.bill('Failed', voting_closed=True, status='failed')
        self.assertTrue(self.actions(failed, self.chair)['reopen'])
        self.assertFalse(self.actions(failed, self.chair)['edit'])
        role = Role.objects.create(name='House Chair', code='HOUSE')
        appt = self.bill('Appt', legislation_type='appointment', appointment_role=role, appointment_member=self.member,
                         voting_closed=True, status='passed', passed=True)
        self.assertTrue(self.actions(appt, self.officer)['assign'])
        self.assertFalse(self.actions(appt, self.member)['assign'])
