"""
Officer tools in the app, batch 5 (v4.x): adding, editing and initiating
members, editing the songbook and its categories, and managing chapter
documents and folders, each behind its website page's own gate and through
the web views' own helpers (src/view/officer/manage_members.py,
src/view/songbook.py, src/view/upload_chapter_document.py,
src/view/manage_chapter_document.py, src/view/manage_chapter_documents.py,
src/view/manage_folders.py).

    python manage.py test src.tests.mobile.test_officer_tools_batch5
"""
import json

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import (
    ActivityLog, ChapterFolder, Committee, CommitteeDocument, Notification, ParliamentUser, Role, Song, SongCategory,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'


def _pdf(name='doc.pdf'):
    content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% x\ntrailer\n%%EOF\n'
    return SimpleUploadedFile(name, content, content_type='application/pdf')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.officer = make_user('b5-off', member_type='Officer')
        self.chair = make_user('b5-chair', member_type='Chair')
        self.member = make_user('b5-mem')
        self.advisor = make_user('b5-adv', member_type='Advisor')
        self.admin = make_user('b5-admin', member_type='Member', is_admin=True)
        self.tokens = {}
        self.role = Role.objects.create(name='B5 Historian', code='B5HIST')
        self.admin_role = Role.objects.create(name='B5 Chief', code='B5CHIEF', grants_admin=True)

    def token(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def call(self, user, method, path, body=None):
        fn = getattr(self.api, method)
        kw = bearer(self.token(user))
        if body is None:
            return fn(API + path, **kw)
        return fn(API + path, json.dumps(body), content_type='application/json', **kw)

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c


# ── Members ──────────────────────────────────────────────────────────────────

class MemberFormTests(_Base):

    def test_who_may_open(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.admin, 200),
                           (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.call(user, 'get', 'officer/members/form/').status_code, code, user.user_id)

    def test_what_each_may_give(self):
        d = self.call(self.officer, 'get', 'officer/members/form/').json()
        roles = {r['code']: r for r in d['roles']}
        self.assertTrue(roles['B5HIST']['can_assign'])
        self.assertFalse(roles['B5CHIEF']['can_assign'])
        self.assertIn('Officer', d['addable_types'])
        self.assertTrue(d['can_change_roles'])
        c = self.call(self.chair, 'get', 'officer/members/form/').json()
        self.assertEqual(sorted(c['addable_types']), ['Member', 'Pledge'])
        self.assertFalse(c['can_change_roles'])
        a = self.call(self.admin, 'get', 'officer/members/form/').json()
        self.assertTrue({r['code']: r for r in a['roles']}['B5CHIEF']['can_assign'])


class MemberAddTests(_Base):

    def add(self, user, **body):
        return self.call(user, 'post', 'officer/members/add/', {'member_status': 'Active', **body})

    def test_add_matches_web_reply_and_log(self):
        r = self.add(self.officer, name='Alan Turing', member_type='Member', roles=[self.role.id])
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertTrue(d['success'])
        self.assertEqual(d['username'], 'aturing')
        self.assertFalse(d['password_is_username'])
        self.assertTrue(d['temp_password'])
        user = ParliamentUser.objects.get(username='aturing')
        self.assertTrue(user.force_password_change and user.has_default_password)
        self.assertTrue(user.check_password(d['temp_password']))
        self.assertEqual(list(user.roles.all()), [self.role])
        log = ActivityLog.objects.filter(metadata__action='add_member').latest('pk')
        self.assertEqual(log.metadata['new_member_id'], user.user_id)
        self.assertEqual(log.user, self.officer)
        # The web's reply has the same keys.
        w = self.web(self.officer).post(reverse('add_member'), json.dumps(
            {'name': 'Grace Hopper', 'member_type': 'Member', 'member_status': 'Active'}),
            content_type='application/json').json()
        self.assertEqual(set(w), set(d))

    def test_pledge_gets_username_password_and_welcome_email(self):
        d = self.add(self.officer, name='Pat Pledge', member_type='Pledge', email='pat@example.com').json()
        self.assertTrue(d['password_is_username'])
        self.assertEqual(d['temp_password'], d['username'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('pat@example.com', mail.outbox[0].to)

    def test_validation(self):
        r = self.add(self.officer, member_type='Member')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        self.assertIn('Name', r.json()['detail'])
        make_user('b5-dupe', email='dupe@example.com')
        r = self.add(self.officer, name='Dee Dupe', member_type='Member', email='dupe@example.com')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already exists', r.json()['detail'])
        self.assertEqual(self.call(self.officer, 'post', 'officer/members/add/', [1]).status_code, 400)

    def test_permissions_mirror_web(self):
        r = self.add(self.chair, name='Ollie Officer', member_type='Officer')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'forbidden')
        r = self.add(self.officer, name='Ada Admin', member_type='Member', roles=[self.admin_role.id])
        self.assertEqual(r.status_code, 403)
        self.assertFalse(ParliamentUser.objects.filter(name='Ada Admin').exists())
        self.assertEqual(self.add(self.member, name='Mo Member', member_type='Member').status_code, 403)
        self.assertEqual(self.add(self.advisor, name='Mo Member', member_type='Member').status_code, 403)
        self.assertEqual(self.add(self.admin, name='Ada Admin', member_type='Member',
                                  roles=[self.admin_role.id]).status_code, 201)


class MemberEditTests(_Base):

    def setUp(self):
        super().setUp()
        self.target = make_user('b5-target', email='t@example.com', role_number='101')

    def edit(self, user, target, body):
        return self.call(user, 'post', f'officer/members/{target.user_id}/edit/', body)

    def test_get_matches_web_member(self):
        self.target.roles.add(self.role)
        d = self.call(self.officer, 'get', 'officer/members/b5-target/edit/').json()
        w = self.web(self.officer).get(reverse('edit_member', args=['b5-target'])).json()
        self.assertEqual(d['member'], w['member'])
        self.assertTrue(d['can_edit'])
        self.assertIn(self.role.id, d['assignable_role_ids'])
        self.assertNotIn(self.admin_role.id, d['assignable_role_ids'])
        self.assertEqual(self.call(self.officer, 'get', 'officer/members/nobody/edit/').status_code, 404)

    def test_edit_saves_logs_and_reports_changes(self):
        r = self.edit(self.officer, self.target, {'name': 'Renamed Target', 'member_status': 'Alumni',
                                                  'roles': [self.role.id]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()['changes']), 3)
        self.target.refresh_from_db()
        self.assertEqual((self.target.name, self.target.member_status), ('Renamed Target', 'Alumni'))
        self.assertEqual(list(self.target.roles.all()), [self.role])
        log = ActivityLog.objects.filter(metadata__action='edit_member').latest('pk')
        self.assertEqual(log.metadata['member_id'], 'b5-target')
        # Nothing changed: no new log.
        before = ActivityLog.objects.count()
        self.assertEqual(self.edit(self.officer, self.target, {'name': 'Renamed Target'}).json()['changes'], [])
        self.assertEqual(ActivityLog.objects.count(), before)

    def test_validation(self):
        make_user('b5-other', email='o@example.com', role_number='202')
        for body, words in (({'email': 'o@example.com'}, 'already exists'),
                            ({'role_number': '202'}, 'already assigned'),
                            ({'roles': ['x']}, 'Invalid roles'),
                            ({'member_type': 'Wizard'}, '')):
            r = self.edit(self.officer, self.target, body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input')
            self.assertIn(words, r.json()['detail'])
        self.target.refresh_from_db()
        self.assertEqual(self.target.email, 't@example.com')

    def test_permissions_mirror_web(self):
        # A non-admin can't edit an admin's account, give an admin role, or (a chair) promote.
        self.assertEqual(self.edit(self.officer, self.admin, {'name': 'X'}).status_code, 403)
        self.assertFalse(self.call(self.officer, 'get', 'officer/members/b5-admin/edit/').json()['can_edit'])
        self.assertEqual(self.edit(self.officer, self.target, {'roles': [self.admin_role.id]}).status_code, 403)
        self.assertEqual(self.edit(self.chair, self.target, {'member_type': 'Officer'}).status_code, 403)
        self.assertEqual(self.edit(self.chair, self.chair, {'member_type': 'Member'}).status_code, 403)
        self.assertEqual(self.edit(self.member, self.target, {'name': 'X'}).status_code, 403)
        self.target.refresh_from_db()
        self.assertEqual(self.target.member_type, 'Member')
        self.assertEqual(self.edit(self.admin, self.target, {'roles': [self.admin_role.id]}).status_code, 200)


class InitiatePledgesTests(_Base):

    def setUp(self):
        super().setUp()
        self.p1 = make_user('b5-p1', member_type='Pledge')
        self.p2 = make_user('b5-p2', member_type='Pledge')

    def test_initiates_notifies_and_logs(self):
        r = self.call(self.officer, 'post', 'officer/members/initiate/', {'pledges': [
            {'user_id': 'b5-p1', 'role_number': '501'}, {'user_id': 'b5-p2', 'role_number': '502'}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['count'], 2)
        self.p1.refresh_from_db()
        self.assertEqual((self.p1.member_type, self.p1.role_number), ('Member', '501'))
        self.assertEqual(Notification.objects.filter(recipient__in=[self.p1, self.p2], source_type='Initiation').count(), 2)
        self.assertTrue(ActivityLog.objects.filter(metadata__action='initiate_pledges').exists())

    def test_validation(self):
        make_user('b5-old', role_number='600')
        for body, words in (({'pledges': []}, 'No pledges'),
                            ({'pledges': [{'user_id': 'b5-p1', 'role_number': ''}]}, 'must have a role number'),
                            ({'pledges': [{'user_id': 'b5-p1', 'role_number': '7'},
                                          {'user_id': 'b5-p2', 'role_number': '7'}]}, 'Duplicate'),
                            ({'pledges': [{'user_id': 'b5-p1', 'role_number': '600'}]}, 'already in use'),
                            ({'pledges': [{'user_id': 'b5-mem', 'role_number': '9'}]}, 'No valid pledges'),
                            ({'pledges': [{'role_number': '9'}]}, 'Invalid pledges'),
                            ({'pledges': 'b5-p1'}, 'Invalid pledges')):
            r = self.call(self.officer, 'post', 'officer/members/initiate/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input')
            self.assertIn(words, r.json()['detail'])
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.member_type, 'Pledge')
        # The website refuses a malformed row too, with a 400 now rather than a 500.
        w = self.web(self.officer).post(reverse('initiate_pledges'), json.dumps({'pledges': [{'role_number': '1'}]}),
                                        content_type='application/json')
        self.assertEqual(w.status_code, 400)

    def test_who_may(self):
        body = {'pledges': [{'user_id': 'b5-p1', 'role_number': '501'}]}
        self.assertEqual(self.call(self.member, 'post', 'officer/members/initiate/', body).status_code, 403)
        self.assertEqual(self.call(self.advisor, 'post', 'officer/members/initiate/', body).status_code, 403)


# ── Songbook ─────────────────────────────────────────────────────────────────

class SongTests(_Base):

    def setUp(self):
        super().setUp()
        self.cat = SongCategory.objects.create(name='B5 Hymns')
        self.song = Song.objects.create(title='Old Song', lyrics='La la', created_by=self.admin)
        self.chorister = make_user('b5-choir')
        from src.view.songbook import chorister_role
        self.chorister.roles.add(chorister_role())

    def test_who_may_manage(self):
        for user, code in ((self.officer, 201), (self.chair, 201), (self.admin, 201), (self.chorister, 201),
                           (self.member, 403), (self.advisor, 403)):
            r = self.call(user, 'post', 'songbook/songs/', {'title': f'By {user.user_id}', 'lyrics': 'x'})
            self.assertEqual(r.status_code, code, user.user_id)
            self.assertEqual(self.call(user, 'get', f'songbook/songs/{self.song.id}/edit/').status_code,
                             200 if code == 201 else 403)

    def test_create_edit_delete(self):
        r = self.call(self.officer, 'post', 'songbook/songs/',
                      {'title': 'New Song', 'lyrics': 'Verse', 'category': self.cat.id})
        self.assertEqual(r.status_code, 201, r.content)
        song = Song.objects.get(pk=r.json()['song']['id'])
        self.assertEqual((song.created_by, song.category), (self.officer, self.cat))
        d = self.call(self.officer, 'get', f'songbook/songs/{song.id}/edit/').json()
        self.assertEqual(d['song']['lyrics'], 'Verse')
        self.assertEqual(d['web_edit'], reverse('song_edit', args=[song.id]))
        r = self.call(self.officer, 'post', f'songbook/songs/{song.id}/edit/',
                      {'title': 'Newer Song', 'lyrics': 'Chorus', 'category': ''})
        self.assertEqual(r.status_code, 200, r.content)
        song.refresh_from_db()
        self.assertEqual((song.title, song.lyrics, song.category), ('Newer Song', 'Chorus', None))
        self.assertEqual(self.call(self.officer, 'post', f'songbook/songs/{song.id}/delete/').status_code, 200)
        song.refresh_from_db()
        self.assertFalse(song.is_active)
        self.assertNotIn(song.id, [s['id'] for s in self.call(self.officer, 'get', 'songbook/').json()['songs']])

    def test_validation(self):
        r = self.call(self.officer, 'post', 'songbook/songs/', {'lyrics': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        r = self.call(self.officer, 'post', 'songbook/songs/', {'title': 'T', 'lyrics': 'x', 'category': 99999})
        self.assertEqual(r.status_code, 400)
        bad = SimpleUploadedFile('song.exe', b'MZ', content_type='application/octet-stream')
        r = self.api.post(API + 'songbook/songs/', {'title': 'T', 'lyrics': 'x', 'audio_file': bad},
                          **bearer(self.token(self.officer)))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Invalid file type', r.json()['detail'])
        self.assertEqual(self.call(self.officer, 'get', 'songbook/songs/99999/edit/').status_code, 404)


class SongCategoryTests(_Base):

    def setUp(self):
        super().setUp()
        self.cat = SongCategory.objects.create(name='B5 Drinking', color='red')

    def post(self, user, body):
        return self.call(user, 'post', 'songbook/categories/', body)

    def test_admins_only(self):
        for user in (self.officer, self.chair, self.member):
            self.assertEqual(self.call(user, 'get', 'songbook/categories/').status_code, 403)
            self.assertEqual(self.post(user, {'action': 'add', 'name': 'X', 'color': 'blue',
                                              'display_order': 0}).status_code, 403)

    def test_get_matches_web_context(self):
        Song.objects.create(title='S', lyrics='x', category=self.cat)
        d = self.call(self.admin, 'get', 'songbook/categories/').json()
        ctx = self.web(self.admin).get(reverse('manage_song_categories')).context
        self.assertEqual([c['id'] for c in d['categories']], [c.id for c in ctx['categories']])
        self.assertEqual({c['id']: c['song_total'] for c in d['categories']}[self.cat.id], 1)
        self.assertEqual([m['user_id'] for m in d['eligible_members']], [m.user_id for m in ctx['eligible_members']])
        self.assertIn({'value': 'red', 'label': 'Red'}, d['colors'])

    def test_actions(self):
        r = self.post(self.admin, {'action': 'add', 'name': 'B5 Hymns', 'color': 'blue', 'display_order': 2})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(SongCategory.objects.filter(name='B5 Hymns').exists())
        r = self.post(self.admin, {'action': 'add', 'name': 'B5 Hymns', 'color': 'blue', 'display_order': 2})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        song = Song.objects.create(title='S', lyrics='x', category=self.cat)
        r = self.post(self.admin, {'action': 'delete', 'category_id': self.cat.id})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Reassign them first', r.json()['detail'])
        song.is_active = False
        song.save()
        self.assertEqual(self.post(self.admin, {'action': 'delete', 'category_id': self.cat.id}).status_code, 200)
        self.assertFalse(SongCategory.objects.filter(pk=self.cat.pk).exists())
        self.assertEqual(self.post(self.admin, {'action': 'add_chorister', 'user_id': 'b5-mem'}).status_code, 200)
        self.assertTrue(self.member.roles.filter(code='CHOIR').exists())
        self.assertEqual(self.post(self.admin, {'action': 'remove_chorister', 'user_id': 'b5-mem'}).status_code, 200)
        self.assertFalse(self.member.roles.filter(code='CHOIR').exists())
        self.assertEqual(self.post(self.admin, {'action': 'add_chorister', 'user_id': 'nobody'}).status_code, 400)
        self.assertEqual(self.post(self.admin, {'action': 'explode'}).status_code, 400)


# ── Chapter documents ────────────────────────────────────────────────────────

class _Docs(_Base):

    def setUp(self):
        super().setUp()
        self.committee = Committee.objects.create(name='B5 Finance', code='B5FIN', is_active=True)
        self.other = Committee.objects.create(name='B5 Other', code='B5OTH', is_active=True)
        self.committee.chairs.add(self.chair)
        self.folder = ChapterFolder.objects.create(name='B5 Bylaws', created_by=self.admin)
        self.docs = []

    def tearDown(self):
        for d in CommitteeDocument.objects.all():
            d.document.delete(save=False)
        super().tearDown()

    def doc(self, **kw):
        fields = dict(title='Doc', document=_pdf(), uploaded_by=self.admin)
        fields.update(kw)
        return CommitteeDocument.objects.create(**fields)


class ManageDocumentsTests(_Docs):

    def test_who_may_open(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.admin, 200),
                           (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.call(user, 'get', 'officer/documents/').status_code, code, user.user_id)

    def test_list_matches_web_context(self):
        draft = self.doc(title='Draft')
        pub = self.doc(title='Pub', published_to_chapter=True, chapter_folder=self.folder)
        loose = self.doc(title='Loose', published_to_chapter=True, committee=self.other)
        hidden = self.doc(title='Other draft', committee=self.other)
        d = self.call(self.officer, 'get', 'officer/documents/').json()
        ctx = self.web(self.officer).get(reverse('manage_chapter_documents')).context
        self.assertEqual([x['id'] for x in d['drafts']], [x.id for x in ctx['unpublished_docs']])
        self.assertEqual([x['id'] for x in d['uncategorized']], [x.id for x in ctx['uncategorized_published_docs']])
        self.assertEqual([(f['id'], [x['id'] for x in f['documents']]) for f in d['folders']],
                         [(f.id, [x.id for x in docs]) for f, docs in ctx['folders_with_published_docs']])
        self.assertEqual(d['total_documents'], ctx['total_documents'])
        ids = {x['id'] for x in d['drafts'] + d['uncategorized']}
        self.assertIn(draft.id, ids)
        self.assertNotIn(hidden.id, ids)
        self.assertEqual(d['folders'][0]['documents'][0]['id'], pub.id)
        self.assertFalse(next(x for x in d['uncategorized'] if x['id'] == loose.id)['can_manage'])
        self.assertEqual(d['upload']['committees'], [])
        self.assertEqual([c['id'] for c in self.call(self.chair, 'get', 'officer/documents/').json()
                          ['upload']['committees']], [self.committee.id])

    def test_upload(self):
        r = self.api.post(API + 'officer/documents/', {'file': _pdf(), 'title': 'Up', 'document_type': 'report',
                                                       'chapter_folder': self.folder.id, 'publish_now': 'true'},
                          **bearer(self.token(self.officer)))
        self.assertEqual(r.status_code, 201, r.content)
        doc = CommitteeDocument.objects.get(pk=r.json()['document']['id'])
        self.assertEqual((doc.uploaded_by, doc.chapter_folder, doc.published_to_chapter, doc.committee),
                         (self.officer, self.folder, True, None))
        self.assertIn('B5 Bylaws', r.json()['message'])

    def test_upload_validation_and_committee_gate(self):
        kw = bearer(self.token(self.chair))
        r = self.api.post(API + 'officer/documents/', {'title': 'No file'}, **kw)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')
        bad = SimpleUploadedFile('x.html', b'<script>', content_type='text/html')
        self.assertEqual(self.api.post(API + 'officer/documents/', {'file': bad, 'title': 'T'}, **kw).status_code, 400)
        r = self.api.post(API + 'officer/documents/', {'file': _pdf(), 'title': 'T', 'committee': self.other.id},
                          **kw)
        self.assertEqual(r.status_code, 400)
        self.assertIn('permission', r.json()['detail'])
        r = self.api.post(API + 'officer/documents/', {'file': _pdf(), 'title': 'T', 'document_type': 'nope'}, **kw)
        self.assertEqual(r.status_code, 400)
        r = self.api.post(API + 'officer/documents/', {'file': _pdf(), 'title': 'T', 'committee': self.committee.id},
                          **kw)
        self.assertEqual(r.status_code, 201)
        self.assertFalse(CommitteeDocument.objects.get(pk=r.json()['document']['id']).published_to_chapter)


class ManageDocumentTests(_Docs):

    def test_get_and_gate(self):
        doc = self.doc(committee=self.other)
        self.assertEqual(self.call(self.officer, 'get', f'officer/documents/{doc.id}/').status_code, 403)
        self.assertEqual(self.call(self.chair, 'get', f'officer/documents/{doc.id}/').status_code, 403)
        d = self.call(self.admin, 'get', f'officer/documents/{doc.id}/').json()
        self.assertEqual(d['document']['committee']['id'], self.other.id)
        mine = self.doc(committee=self.committee)
        d = self.call(self.chair, 'get', f'officer/documents/{mine.id}/').json()
        self.assertEqual([c['id'] for c in d['committees']], [self.committee.id])
        self.assertEqual(self.call(self.member, 'get', f'officer/documents/{mine.id}/').status_code, 403)
        self.assertEqual(self.call(self.chair, 'get', f'officer/documents/{mine.id}/file/').status_code, 200)
        self.assertEqual(self.call(self.chair, 'get', f'officer/documents/{doc.id}/file/').status_code, 403)
        self.assertEqual(self.call(self.admin, 'get', 'officer/documents/99999/').status_code, 404)

    def test_update(self):
        doc = self.doc(title='Before')
        r = self.call(self.officer, 'post', f'officer/documents/{doc.id}/', {
            'action': 'update', 'title': 'After', 'description': 'D', 'document_type': 'policy',
            'committee': '', 'chapter_folder': self.folder.id, 'published_to_chapter': True})
        self.assertEqual(r.status_code, 200, r.content)
        doc.refresh_from_db()
        self.assertEqual((doc.title, doc.description, doc.document_type, doc.chapter_folder, doc.published_to_chapter),
                         ('After', 'D', 'policy', self.folder, True))
        # A committee the officer doesn't chair is ignored, as on the web.
        self.call(self.officer, 'post', f'officer/documents/{doc.id}/',
                  {'action': 'update', 'committee': self.other.id, 'published_to_chapter': False})
        doc.refresh_from_db()
        self.assertEqual((doc.committee, doc.chapter_folder, doc.published_to_chapter, doc.title),
                         (None, None, False, 'After'))

    def test_update_validation(self):
        doc = self.doc(title='Keep')
        for body in ({'action': 'update', 'title': ''}, {'action': 'update', 'title': 'x' * 201},
                     {'action': 'update', 'document_type': 'nope'}, {'action': 'rename'}):
            r = self.call(self.officer, 'post', f'officer/documents/{doc.id}/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_input')
        doc.refresh_from_db()
        self.assertEqual(doc.title, 'Keep')

    def test_delete(self):
        doc = self.doc(committee=self.committee)
        self.assertEqual(self.call(self.officer, 'post', f'officer/documents/{doc.id}/',
                                   {'action': 'delete'}).status_code, 403)
        r = self.call(self.chair, 'post', f'officer/documents/{doc.id}/', {'action': 'delete'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['deleted'])
        self.assertFalse(CommitteeDocument.objects.filter(pk=doc.pk).exists())


class FolderTests(_Docs):

    def test_admins_only(self):
        for user in (self.officer, self.chair):
            self.assertEqual(self.call(user, 'post', 'officer/documents/folders/',
                                       {'folder_name': 'X'}).status_code, 403)
            self.assertEqual(self.call(user, 'post', f'officer/documents/folders/{self.folder.id}/delete/')
                             .status_code, 403)

    def test_create_and_delete(self):
        r = self.call(self.admin, 'post', 'officer/documents/folders/',
                      {'folder_name': '  Minutes  ', 'folder_description': 'Old ones'})
        self.assertEqual(r.status_code, 201, r.content)
        folder = ChapterFolder.objects.get(pk=r.json()['folder']['id'])
        self.assertEqual((folder.name, folder.created_by), ('Minutes', self.admin))
        for name in ('', 'Minutes', 'x' * 101):
            r = self.call(self.admin, 'post', 'officer/documents/folders/', {'folder_name': name})
            self.assertEqual(r.status_code, 400, name)
            self.assertEqual(r.json()['error'], 'invalid_input')
        doc = self.doc(chapter_folder=folder, published_to_chapter=True)
        r = self.call(self.admin, 'post', f'officer/documents/folders/{folder.id}/delete/')
        self.assertEqual(r.status_code, 200)
        doc.refresh_from_db()
        self.assertIsNone(doc.chapter_folder)
        self.assertEqual(self.call(self.admin, 'post', f'officer/documents/folders/{folder.id}/delete/')
                         .status_code, 404)

    def test_web_still_refuses_a_long_name(self):
        self.web(self.admin).post(reverse('create_folder'), {'folder_name': 'x' * 101})
        self.assertFalse(ChapterFolder.objects.filter(name='x' * 101).exists())
