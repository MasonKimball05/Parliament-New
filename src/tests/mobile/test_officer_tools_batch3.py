"""
Officer tools in the app, batch 3 (v4.x): All Activity, houses and bigs,
Kai commendations management and legacy resolutions management, each behind
its website page's own gate and through the web views' own helpers.

    python manage.py test src.tests.mobile.test_officer_tools_batch3
"""
import json
from datetime import date

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    ActivityLog, Committee, CommitteeDocument, KaiCommendation, KaiCommendationActivity, KaiMemberPermission,
    Legislation, PassedResolution, PledgeBigAssignment, ResolutionSectionImpact,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, toggle

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.officer = make_user('b3-off', member_type='Officer')
        self.member = make_user('b3-mem')
        self.advisor = make_user('b3-adv', member_type='Advisor')
        self.chair = make_user('b3-chair', member_type='Chair')
        self.admin = make_user('b3-admin', member_type='Member', is_admin=True)
        self.tokens = {u.user_id: make_device(u)[1]
                       for u in (self.officer, self.member, self.advisor, self.chair, self.admin)}

    def call(self, user, method, path, body=None):
        fn = getattr(self.api, method)
        kw = bearer(self.tokens[user.user_id])
        if body is None:
            return fn(API + path, **kw)
        return fn(API + path, json.dumps(body), content_type='application/json', **kw)


# ── All Activity ─────────────────────────────────────────────────────────────

class ActivityTests(_Base):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        Legislation.objects.create(title='Draft bill', description='Body', posted_by=self.officer,
                                   available_at=now, status='draft')
        Legislation.objects.create(title='Passed bill', description='Body', posted_by=self.officer,
                                   available_at=now, status='passed')

    def test_officers_chairs_and_advisors_only(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.advisor, 200), (self.member, 403)):
            self.assertEqual(self.call(user, 'get', 'officer/activity/').status_code, code, user.member_type)

    def test_rows_and_true_totals(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        CommitteeDocument.objects.create(
            title='Minutes', uploaded_by=self.officer,
            document=SimpleUploadedFile('m.pdf', b'%PDF-1.4 x', content_type='application/pdf'))
        r = self.call(self.advisor, 'get', 'officer/activity/')
        d = r.json()
        self.assertEqual({x['title'] for x in d['chapter_legislation']}, {'Draft bill', 'Passed bill'})
        self.assertEqual(d['totals']['chapter'], {'all': 2, 'draft': 1, 'passed': 1, 'removed': 0})
        self.assertEqual(d['totals']['documents'], 1)
        self.assertFalse(d['truncated'])
        doc = d['committee_documents'][0]
        self.assertEqual(doc['file'], f'officer/reports/{doc["id"]}/file/')

    def test_hub_flag(self):
        self.assertTrue(self.call(self.officer, 'get', 'officer/').json()['can_view_activity'])


# ── Houses and bigs ──────────────────────────────────────────────────────────

class FamilyTests(_Base):

    def setUp(self):
        super().setUp()
        self.big = make_user('b3-big', house='Knox')
        self.little = make_user('b3-lil')
        self.grand = make_user('b3-grand', big_brother=self.little)

    def test_who_may_set_houses(self):
        from src.view.house_map import _get_historian_role
        historian = make_user('b3-hist', member_type='Chair')
        historian.roles.add(_get_historian_role())
        self.tokens[historian.user_id] = make_device(historian)[1]
        for user, code in ((self.officer, 200), (self.admin, 200), (historian, 200),
                           (self.chair, 403), (self.member, 403), (self.advisor, 403)):
            r = self.call(user, 'post', f'officer/members/{self.little.user_id}/house/', {'house': 'Smith'})
            self.assertEqual(r.status_code, code, user.user_id)
            r = self.call(user, 'post', f'officer/members/{self.little.user_id}/big/', {'big': ''})
            self.assertEqual(r.status_code, code, user.user_id)
            self.assertEqual(self.call(user, 'get', 'officer/house-assignments/').status_code, code)

    def test_card_says_who_may(self):
        r = self.call(self.officer, 'get', f'members/{self.little.user_id}/')
        self.assertTrue(r.json()['can_set_house'])
        r = self.call(self.member, 'get', f'members/{self.little.user_id}/')
        self.assertFalse(r.json()['can_set_house'])
        self.assertIn('member', r.json())

    def test_set_house_cascades_and_logs(self):
        r = self.call(self.officer, 'post', f'officer/members/{self.little.user_id}/house/', {'house': 'Smith'})
        self.assertEqual(r.json(), {'house': 'Smith'})
        self.grand.refresh_from_db()
        self.assertEqual(self.grand.house, 'Smith')
        log = ActivityLog.objects.filter(object_type='ParliamentUser', object_id=self.little.pk).latest('pk')
        self.assertEqual(log.metadata['new'], 'Smith')

    def test_invalid_house(self):
        r = self.call(self.officer, 'post', f'officer/members/{self.little.user_id}/house/', {'house': 'Nowhere'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')

    def test_set_big_inherits_house(self):
        r = self.call(self.officer, 'post', f'officer/members/{self.little.user_id}/big/', {'big': self.big.user_id})
        self.assertEqual(r.json(), {'big': self.big.user_id, 'house': 'Knox'})
        self.little.refresh_from_db()
        self.assertEqual(self.little.big_brother, self.big)
        self.grand.refresh_from_db()
        self.assertEqual(self.grand.house, 'Knox')

    def test_refuses_loops_and_unknown_bigs(self):
        r = self.call(self.officer, 'post', f'officer/members/{self.little.user_id}/big/', {'big': self.grand.user_id})
        self.assertEqual(r.status_code, 400)
        r = self.call(self.officer, 'post', f'officer/members/{self.little.user_id}/big/', {'big': 'nobody'})
        self.assertEqual(r.status_code, 400)
        self.little.refresh_from_db()
        self.assertIsNone(self.little.big_brother)

    def test_refuses_an_education_managed_pairing(self):
        pledge = make_user('P-B3', member_type='Pledge')
        committee = Committee.objects.create(name='Education', code='EDU', is_active=True,
                                             is_education_committee=True)
        PledgeBigAssignment.objects.create(committee=committee, pledge=pledge, big=self.big,
                                           created_by=self.officer)
        r = self.call(self.officer, 'post', f'officer/members/{pledge.user_id}/big/', {'big': self.member.user_id})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'big_managed')
        people = {p['user_id']: p for p in self.call(self.officer, 'get', 'officer/house-assignments/').json()['people']}
        self.assertTrue(people[pledge.user_id]['locked'])

    def test_assignments_panel(self):
        d = self.call(self.officer, 'get', 'officer/house-assignments/').json()
        self.assertIn(self.little.user_id, d['unassigned'])
        self.assertNotIn(self.big.user_id, d['unassigned'])
        self.assertEqual(d['houses'][0], {'value': 'Smith', 'label': 'Smith'})
        self.assertTrue(self.call(self.officer, 'get', 'officer/').json()['can_set_house'])

    def test_panel_follows_the_house_map_toggle(self):
        toggle('house_map', False)
        self.assertEqual(self.call(self.officer, 'get', 'officer/house-assignments/').status_code, 403)

    def test_web_endpoints_unchanged(self):
        web = Client()
        web.force_login(self.officer)
        r = web.post(f'/officers/members/{self.little.user_id}/set-house/', {'house': 'Ryan'})
        self.assertEqual(r.json(), {'house': 'Ryan'})
        r = web.post(f'/officers/members/{self.little.user_id}/set-big/', {'big': self.grand.user_id})
        self.assertEqual(r.status_code, 400)
        web.force_login(self.member)
        self.assertEqual(web.post(f'/officers/members/{self.little.user_id}/set-house/', {'house': ''}).status_code,
                         403)


# ── Kai commendations ────────────────────────────────────────────────────────

class KaiCommendationTests(_Base):

    def setUp(self):
        super().setUp()
        flag('kai_reports', True)
        self.kai = Committee.objects.create(name='Kai', code='KAIB3', is_kai_committee=True)
        self.kai_chair = make_user('b3-kai', member_type='Chair')
        self.kai.chairs.add(self.kai_chair)
        self.tokens[self.kai_chair.user_id] = make_device(self.kai_chair)[1]
        self.honoree = make_user('b3-hon', name='Honoree Person')
        self.c = KaiCommendation.objects.create(
            title='Great work', description='Ran the philanthropy event', submitted_by=self.member,
            commended_member=self.honoree, is_submitter_anonymous=True)

    def test_only_the_kai_committee(self):
        # Officers and admins without a Kai grant get nothing, as on the website.
        for user in (self.officer, self.admin, self.member, self.chair):
            self.assertEqual(self.call(user, 'get', 'officer/kai/commendations/').status_code, 403, user.user_id)
            self.assertEqual(self.call(user, 'get', f'officer/kai/commendations/{self.c.id}/').status_code, 403)
        self.assertEqual(self.call(self.kai_chair, 'get', 'officer/kai/commendations/').status_code, 200)
        self.assertFalse(self.call(self.officer, 'get', 'officer/').json()['can_manage_kai_commendations'])

    def test_list(self):
        d = self.call(self.kai_chair, 'get', 'officer/kai/commendations/').json()
        row = d['commendations'][0]
        self.assertEqual(row['commended_member'], {'user_id': 'b3-hon', 'name': 'Honoree Person'})
        self.assertEqual(row['submitted_by']['user_id'], self.member.user_id)
        self.assertTrue(row['is_submitter_anonymous'])
        self.assertEqual(self.call(self.kai_chair, 'get', 'officer/kai/commendations/?status=archived')
                         .json()['commendations'], [])

    def test_list_grant_without_details(self):
        KaiMemberPermission.objects.create(committee=self.kai, user=self.member, can_view_report_list=True)
        self.assertEqual(self.call(self.member, 'get', 'officer/kai/commendations/').status_code, 200)
        self.assertEqual(self.call(self.member, 'get', f'officer/kai/commendations/{self.c.id}/').status_code, 403)

    def test_feature_flag(self):
        flag('kai_reports', False)
        r = self.call(self.kai_chair, 'get', 'officer/kai/commendations/')
        self.assertEqual(r.json()['error'], 'feature_disabled')

    def test_chair_updates_status_assignment_and_notes(self):
        path = f'officer/kai/commendations/{self.c.id}/'
        d = self.call(self.kai_chair, 'get', path).json()
        self.assertEqual(d['can'], {'edit': True, 'notes': True})
        self.assertIn(self.kai_chair.user_id, {m['user_id'] for m in d['members']})

        d = self.call(self.kai_chair, 'post', path, {'action': 'update_status', 'status': 'acknowledged'}).json()
        self.assertEqual(d['commendation']['status'], 'acknowledged')
        self.c.refresh_from_db()
        self.assertEqual(self.c.reviewed_by, self.kai_chair)

        self.call(self.kai_chair, 'post', path, {'action': 'assign', 'assigned_to': self.kai_chair.user_id})
        self.call(self.kai_chair, 'post', path, {'action': 'update_notes', 'committee_notes': 'Relay at chapter'})
        self.c.refresh_from_db()
        self.assertEqual(self.c.assigned_to, self.kai_chair)
        self.assertEqual(self.c.committee_notes, 'Relay at chapter')
        self.assertEqual(set(KaiCommendationActivity.objects.filter(commendation=self.c)
                             .values_list('action', flat=True)), {'status_changed', 'assigned', 'notes_updated'})

    def test_bad_status_and_missing_permission(self):
        path = f'officer/kai/commendations/{self.c.id}/'
        r = self.call(self.kai_chair, 'post', path, {'action': 'update_status', 'status': 'gone'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        KaiMemberPermission.objects.create(committee=self.kai, user=self.member, can_view_report_list=True,
                                           can_view_report_details=True)
        d = self.call(self.member, 'get', path).json()
        self.assertEqual(d['can'], {'edit': False, 'notes': False})
        self.assertEqual(d['members'], [])
        r = self.call(self.member, 'post', path, {'action': 'update_notes', 'committee_notes': 'x'})
        self.assertEqual(r.status_code, 403)
        self.c.refresh_from_db()
        self.assertEqual(self.c.committee_notes, '')
        self.assertFalse(KaiCommendationActivity.objects.filter(commendation=self.c).exists())

    def test_hub_flag_and_count(self):
        h = self.call(self.kai_chair, 'get', 'officer/').json()
        self.assertTrue(h['can_manage_kai_commendations'])
        self.assertEqual(h['pending_kai_commendations'], 1)


# ── Resolutions (legacy) ─────────────────────────────────────────────────────

class ResolutionTests(_Base):

    def setUp(self):
        super().setUp()
        self.bill = Legislation.objects.create(title='Bylaw change', description='Body', posted_by=self.officer,
                                               available_at=timezone.now(), status='passed')

    def test_admins_only(self):
        for user in (self.officer, self.chair, self.member, self.advisor):
            self.assertEqual(self.call(user, 'get', 'officer/resolutions/').status_code, 403, user.user_id)
            self.assertEqual(self.call(user, 'post', 'officer/resolutions/', {}).status_code, 403)
        self.assertEqual(self.call(self.admin, 'get', 'officer/resolutions/').status_code, 200)
        self.assertFalse(self.call(self.officer, 'get', 'officer/').json()['can_manage_resolutions'])

    def test_create_needs_legislation_or_a_document(self):
        body = {'title': 'R1', 'description': 'Does a thing', 'date_passed': '2026-10-01'}
        r = self.call(self.admin, 'post', 'officer/resolutions/', body)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        r = self.call(self.admin, 'post', 'officer/resolutions/', {**body, 'legislation': self.bill.id})
        self.assertEqual(r.status_code, 201, r.content)
        res = PassedResolution.objects.get(title='R1')
        self.assertEqual(res.created_by, self.admin)
        self.assertTrue(res.is_active)
        self.assertEqual(r.json()['resolution']['legislation'], {'id': self.bill.id, 'title': 'Bylaw change'})

    def test_create_with_document_upload(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.api.post(API + 'officer/resolutions/', {
            'title': 'R2', 'description': 'd', 'date_passed': '2026-10-02', 'is_active': 'true',
            'document': SimpleUploadedFile('r2.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
        }, **bearer(self.tokens[self.admin.user_id]))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()['resolution']['document_name'].endswith('.pdf'))

    def test_edit_keeps_what_it_does_not_send(self):
        res = PassedResolution.objects.create(title='Old', description='d', date_passed=date(2026, 1, 1),
                                              legislation=self.bill, border_color='blue', display_order=3)
        r = self.call(self.admin, 'patch', f'officer/resolutions/{res.id}/', {'title': 'New'})
        self.assertEqual(r.status_code, 200, r.content)
        res.refresh_from_db()
        self.assertEqual((res.title, res.border_color, res.display_order, res.legislation_id),
                         ('New', 'blue', 3, self.bill.id))
        r = self.call(self.admin, 'patch', f'officer/resolutions/{res.id}/', {'border_color': 'plaid'})
        self.assertEqual(r.status_code, 400)

    def test_section_impacts_and_delete(self):
        res = PassedResolution.objects.create(title='R', description='d', date_passed=date(2026, 1, 1),
                                              legislation=self.bill)
        r = self.call(self.admin, 'post', f'officer/resolutions/{res.id}/sections/',
                      {'section_name': 'Article III', 'section_anchor': '#const-leadership'})
        self.assertEqual(r.status_code, 201, r.content)
        impact_id = r.json()['section_impact']['id']
        self.assertEqual(r.json()['section_impact']['section_type'], 'constitution')
        r = self.call(self.admin, 'post', f'officer/resolutions/{res.id}/sections/', {'section_name': ''})
        self.assertEqual(r.status_code, 400)
        r = self.call(self.admin, 'patch', f'officer/resolutions/sections/{impact_id}/', {'section_type': 'bylaws'})
        self.assertEqual(r.json()['section_impact']['section_type'], 'bylaws')
        self.assertEqual(r.json()['section_impact']['section_name'], 'Article III')
        d = self.call(self.admin, 'get', f'officer/resolutions/{res.id}/').json()
        self.assertEqual(len(d['resolution']['section_impacts']), 1)
        self.assertIn({'id': self.bill.id, 'title': 'Bylaw change', 'status': 'passed'}, d['options']['legislation'])
        self.assertEqual(self.call(self.admin, 'delete', f'officer/resolutions/sections/{impact_id}/').status_code,
                         200)
        ResolutionSectionImpact.objects.create(resolution=res, section_name='Again')
        r = self.call(self.admin, 'delete', f'officer/resolutions/{res.id}/')
        self.assertEqual(r.json(), {'ok': True, 'section_impacts_deleted': 1})
        self.assertFalse(PassedResolution.objects.filter(pk=res.pk).exists())
        self.assertFalse(ResolutionSectionImpact.objects.exists())
