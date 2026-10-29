"""
Mobile plan, Phase 3e — Committees: the list, a committee's dashboard, its
people, documents and minutes.

Access must be committee_home's (member, chair, advisor, VP, committee admin,
site admin), documents must pass can_user_view, and minutes need a member or
above, as on the website.

    python manage.py test src.tests.mobile.test_committees
"""
from datetime import date, time

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from src.models import ChapterMinutes, Committee, CommitteeDocument
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class CommitteeTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('cm-1')
        _, self.access, _ = make_device(self.user)
        self.chair = make_user('cm-chair')
        self.outsider = make_user('cm-out')
        _, self.outsider_access, _ = make_device(self.outsider)
        self.c = Committee.objects.create(name='Finance', code='FIN')
        self.c.members.add(self.user)
        self.c.chairs.add(self.chair)
        self.c.voting_members.add(self.user)
        self.api = Client()

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))

    def doc(self, title, **extra):
        return CommitteeDocument.objects.create(
            committee=self.c, title=title, uploaded_by=self.chair,
            document=SimpleUploadedFile(f'{title}.pdf', b'%PDF-1.4 test', content_type='application/pdf'), **extra)

    def test_list_is_my_committees_with_roles(self):
        Committee.objects.create(name='Other', code='OTH')
        body = self.get('committees/').json()
        self.assertEqual([c['code'] for c in body['committees']], ['FIN'])
        self.assertEqual(body['committees'][0]['roles'], ['Member'])
        self.assertTrue(body['committees'][0]['is_voting_member'])
        self.assertFalse(body['can_show_all'])

    def test_dashboard(self):
        self.doc('Budget')
        body = self.get('committees/FIN/').json()
        self.assertEqual(body['committee']['name'], 'Finance')
        self.assertEqual(body['me']['roles'], ['Member'])
        self.assertFalse(body['me']['can_manage'])
        self.assertEqual(body['stats']['people'], 2)
        self.assertEqual([d['title'] for d in body['recent_documents']], ['Budget'])
        self.assertFalse([l for l in body['web_links'] if l['kind'] == 'tool' and l['title'] != 'Write minutes'])

    def test_outsider_refused(self):
        self.assertEqual(self.get('committees/FIN/', self.outsider_access).json()['error'], 'no_access')
        self.assertEqual(self.get('committees/FIN/documents/', self.outsider_access).status_code, 403)
        self.assertEqual(self.get('committees/NOPE/').status_code, 404)

    def test_members(self):
        body = self.get('committees/FIN/members/').json()
        self.assertEqual([m['user_id'] for m in body['chairs']], ['cm-chair'])
        self.assertEqual(body['members'], [{'user_id': 'cm-1', 'name': self.user.get_display_name(),
                                            'photo': None, 'votes': True}])

    def test_documents_respect_visibility_and_file(self):
        shown = self.doc('Budget')
        self.doc('Chairs', visibility='chairs_only')
        body = self.get('committees/FIN/documents/').json()
        self.assertEqual([d['title'] for d in body['documents']], ['Budget'])
        r = self.get(f'committees/FIN/documents/{shown.id}/file/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b''.join(r.streaming_content), b'%PDF-1.4 test')
        hidden = CommitteeDocument.objects.get(title='Chairs')
        self.assertEqual(self.get(f'committees/FIN/documents/{hidden.id}/file/').status_code, 404)

    def test_minutes(self):
        doc = self.doc('Minutes 1')
        ChapterMinutes.objects.create(title='Week 1', date=date(2026, 10, 1), start_time=time(19), committee=self.c,
                                      created_by=self.chair, status='published', published_document=doc)
        ChapterMinutes.objects.create(title='Week 2', date=date(2026, 10, 8), start_time=time(19), committee=self.c,
                                      created_by=self.chair)
        rows = self.get('committees/FIN/minutes/').json()['minutes']
        self.assertEqual([(m['title'], m['status']) for m in rows], [('Week 2', 'draft'), ('Week 1', 'published')])
        self.assertIsNone(rows[0]['document'])
        self.assertEqual(rows[1]['document']['id'], doc.id)

    def test_flag_off(self):
        flag('committee_system', False)
        self.assertEqual(self.get('committees/').json()['error'], 'feature_disabled')
