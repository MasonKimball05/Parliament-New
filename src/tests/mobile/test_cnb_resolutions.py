"""
The C&B resolution builder in the app (11-04-26, src/mobile/views_cnb_resolutions.py).

The app goes through the website's own helpers (src/view/officer/cnb.py), so
these check the same rules from the app's side: who may create, edit and run
a resolution (the chair, the author while a brother, Editor collaborators;
viewers and other members not), the status locks, amendments, structure
changes, status changes and collaborators, and that the web pages still work
after the views were split into helpers.

    python manage.py test src.tests.mobile.test_cnb_resolutions
"""
import datetime
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import (
    Article, GoverningDocument, Resolution, ResolutionAmendment, ResolutionCollaborator, ResolutionStructureChange,
    Section,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        for name in ('cnb_constitution', 'cnb_bylaws', 'cnb_appendix'):
            flag(name, True)
        flag('cnb_foreword', False)
        self.author = make_user('cr-author')
        self.chair = make_user('cr-chair', is_admin=True)
        self.editor = make_user('cr-editor')
        self.viewer = make_user('cr-viewer')
        self.other = make_user('cr-other')
        self.pledge = make_user('cr-pledge', member_type='Pledge')
        self.con = GoverningDocument.objects.create(doc_type='constitution', title='Constitution', display_order=10)
        self.foreword = GoverningDocument.objects.create(doc_type='foreword', title='Foreword', display_order=1)
        self.art = Article.objects.create(document=self.con, number='I', title='Name', display_order=1)
        self.art2 = Article.objects.create(document=self.con, number='II', title='Purpose', display_order=2)
        self.sec = Section.objects.create(article=self.art, number='1', title='Name', display_order=1,
                                          content='The chapter is named Alpha Mu.')
        self.sec2 = Section.objects.create(article=self.art, number='2', title='Seal', display_order=2,
                                           content='The seal is round.')
        fart = Article.objects.create(document=self.foreword, number='I', title='Hidden', display_order=1)
        self.hidden = Section.objects.create(article=fart, number='1', content='Unpassed.', display_order=1)
        self.res = Resolution.objects.create(title='On Names', created_by=self.author)
        ResolutionCollaborator.objects.create(resolution=self.res, user=self.editor, role='editor')
        ResolutionCollaborator.objects.create(resolution=self.res, user=self.viewer, role='viewer')
        self.tokens = {}

    def call(self, user, method, path, body=None):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        kwargs = bearer(self.tokens[user.pk])
        client = Client()
        fn = getattr(client, method)
        if body is None:
            return fn(API + path, **kwargs)
        return fn(API + path, data=json.dumps(body), content_type='application/json', **kwargs)

    def base(self):
        return f'cnb/resolutions/{self.res.pk}/'


class CreateAndEditTests(_Base):

    def test_brother_creates_pledge_cannot(self):
        r = self.call(self.other, 'post', 'cnb/resolutions/create/', {
            'title': '  On Dues ', 'resolution_type': 'general', 'whereas_clauses': 'Dues are due\r\n',
            'vote_date': '2026-12-01'})
        self.assertEqual(r.status_code, 201, r.content)
        res = Resolution.objects.get(pk=r.json()['id'])
        self.assertEqual((res.title, res.resolution_type, res.created_by, res.vote_date),
                         ('On Dues', 'general', self.other, datetime.date(2026, 12, 1)))
        self.assertEqual(self.call(self.other, 'get', 'cnb/resolutions/create/').json()['types'][0]['value'],
                         'amendment')
        r = self.call(self.pledge, 'post', 'cnb/resolutions/create/', {'title': 'By a pledge'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['detail'], 'Pledges and advisors can read resolutions but not write them.')
        self.assertFalse(Resolution.objects.filter(title='By a pledge').exists())

    def test_create_validation_matches_the_website(self):
        for body, message in (
                ({'title': ' '}, 'A title is required.'),
                ({'title': 'x' * 301}, 'The title can be at most 300 characters (this one is 301).'),
                ({'title': 'T', 'resolution_type': 'bogus'}, 'Pick a resolution type from the list.'),
                ({'title': 'T', 'vote_date': 'soon'}, 'Invalid vote date format.')):
            r = self.call(self.other, 'post', 'cnb/resolutions/create/', body)
            self.assertEqual((r.status_code, r.json()['detail']), (400, message))
        self.assertEqual(Resolution.objects.count(), 1)

    def test_who_may_edit(self):
        for user, code in ((self.author, 200), (self.editor, 200), (self.chair, 200),
                           (self.viewer, 403), (self.other, 403), (self.pledge, 403)):
            r = self.call(user, 'patch', self.base() + 'edit/', {'fields': {'sponsors': user.pk}})
            self.assertEqual(r.status_code, code, (user.pk, r.content))
        self.res.refresh_from_db()
        self.assertEqual(self.res.sponsors, 'cr-chair')

    def test_author_who_becomes_an_advisor_loses_edit(self):
        type(self.author).objects.filter(pk=self.author.pk).update(member_type='Advisor')
        r = self.call(self.author, 'patch', self.base() + 'edit/', {'fields': {'title': 'Mine still?'}})
        self.assertEqual(r.status_code, 403)

    def test_edit_writes_only_sent_fields_and_reports_conflicts(self):
        Resolution.objects.filter(pk=self.res.pk).update(authors='A. Author', sponsors='Exec')
        r = self.call(self.author, 'get', self.base() + 'edit/')
        self.assertEqual(r.json()['fields']['authors'], 'A. Author')
        r = self.call(self.author, 'patch', self.base() + 'edit/', {
            'fields': {'title': 'On New Names', 'resolution_body': 'Line\r\n'},
            'original': {'title': 'On Names', 'resolution_body': ''}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(sorted(r.json()['saved']), ['resolution_body', 'title'])
        self.res.refresh_from_db()
        self.assertEqual((self.res.title, self.res.resolution_body, self.res.sponsors),
                         ('On New Names', 'Line', 'Exec'))
        # Someone else changed the sponsors since this was loaded: not written.
        r = self.call(self.editor, 'patch', self.base() + 'edit/', {
            'fields': {'sponsors': 'Committee', 'authors': 'B. Editor'},
            'original': {'sponsors': 'Old value', 'authors': 'A. Author'}})
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertEqual(body['error'], 'conflict')
        self.assertEqual(body['conflicts'][0]['field'], 'sponsors')
        self.assertEqual(body['conflicts'][0]['theirs'], 'Exec')
        self.assertEqual(body['saved'], ['authors'])
        self.res.refresh_from_db()
        self.assertEqual((self.res.sponsors, self.res.authors), ('Exec', 'B. Editor'))

    def test_a_refused_field_saves_nothing(self):
        r = self.call(self.author, 'patch', self.base() + 'edit/', {
            'fields': {'authors': 'Someone', 'title': '', 'vote_date': '2026-13-40'}})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'A title is required.'))
        self.res.refresh_from_db()
        self.assertEqual((self.res.authors, self.res.title), ('', 'On Names'))

    def test_closed_resolution_is_locked(self):
        Resolution.objects.filter(pk=self.res.pk).update(status='passed')
        r = self.call(self.chair, 'patch', self.base() + 'edit/', {'fields': {'title': 'Changed'}})
        self.assertEqual((r.status_code, r.json()['detail']),
                         (400, 'Only draft or pending resolutions can be edited.'))
        r = self.call(self.chair, 'post', self.base() + 'amendments/', {'section_id': self.sec.pk,
                                                                       'proposed_text': 'New.'})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Cannot modify a closed resolution.'))
        r = self.call(self.chair, 'post', self.base() + 'structure/', {'kind': 'rename_article',
                                                                      'article_id': self.art.pk, 'title': 'X'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ResolutionAmendment.objects.exists())
        self.assertFalse(ResolutionStructureChange.objects.exists())


class AmendmentTests(_Base):

    def amend(self, user, **body):
        return self.call(user, 'post', self.base() + 'amendments/', body)

    def test_picker_shows_member_documents_with_section_state(self):
        Section.objects.filter(pk=self.sec2.pk).update(amendment_protected=True,
                                                       protected_until=datetime.date(2027, 1, 1))
        r = self.call(self.editor, 'get', self.base() + 'amendments/')
        self.assertEqual(r.status_code, 200, r.content)
        docs = r.json()['documents']
        self.assertEqual([d['doc_type'] for d in docs], ['constitution'])   # the Foreword is switched off
        secs = docs[0]['articles'][0]['sections']
        self.assertEqual(secs[0]['identifier'], 'Constitution Art. I § 1')
        self.assertTrue(secs[1]['protected'])
        self.assertEqual(self.call(self.viewer, 'get', self.base() + 'amendments/').status_code, 403)

    def test_section_context_shows_current_text_and_existing_amendment(self):
        r = self.call(self.author, 'get', self.base() + f'sections/{self.sec.pk}/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['content'], 'The chapter is named Alpha Mu.')
        self.assertEqual(body['next_section']['number'], '2')
        self.assertIsNone(body['amendment'])
        self.amend(self.author, section_id=self.sec.pk, proposed_text='The chapter is named Beta.')
        body = self.call(self.author, 'get', self.base() + f'sections/{self.sec.pk}/').json()
        self.assertEqual(body['amendment']['proposed_text'], 'The chapter is named Beta.')
        # A switched-off document's text isn't readable through this.
        self.assertEqual(self.call(self.editor, 'get', self.base() + f'sections/{self.hidden.pk}/').status_code, 404)
        self.assertEqual(self.call(self.other, 'get', self.base() + f'sections/{self.sec.pk}/').status_code, 403)

    def test_add_update_and_remove(self):
        r = self.amend(self.editor, section_id=self.sec.pk,
                       proposed_text='The chapter is named Alpha Mu.\r\nIt was founded in 1900.')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Amendment for Constitution Art. I § 1 added.')
        a = ResolutionAmendment.objects.get()
        self.assertEqual((a.amendment_type, a.original_text_snapshot),
                         ('addition', 'The chapter is named Alpha Mu.'))
        self.assertIn('\n', a.proposed_text)
        self.assertNotIn('\r', a.proposed_text)
        r = self.amend(self.editor, section_id=self.sec.pk, proposed_text='Renamed.', scope_note='the name')
        self.assertEqual(r.json()['message'], 'Amendment for Constitution Art. I § 1 updated.')
        a.refresh_from_db()
        self.assertEqual((a.amendment_type, a.scope_note), ('change', 'the name'))
        self.assertEqual(self.call(self.viewer, 'delete', self.base() + f'amendments/{a.pk}/').status_code, 403)
        r = self.call(self.author, 'delete', self.base() + f'amendments/{a.pk}/')
        self.assertEqual(r.json()['message'], 'Amendment for Constitution Art. I § 1 removed.')
        self.assertFalse(ResolutionAmendment.objects.exists())

    def test_whole_section_removal_and_partial_deletion_rule(self):
        r = self.amend(self.author, section_id=self.sec2.pk, proposed_text='', scope_note='clause b')
        self.assertEqual(r.json()['detail'],
                         'For a partial deletion, provide the full section text with the removed clause omitted.')
        self.amend(self.author, section_id=self.sec2.pk, proposed_text='', section_title='Ignored')
        a = ResolutionAmendment.objects.get()
        self.assertTrue(a.is_whole_section_removal)
        self.assertFalse(ResolutionStructureChange.objects.exists())   # no title for a removed section
        detail = self.call(self.author, 'get', self.base()).json()
        self.assertTrue(detail['amendment_list'][0]['removes_section'])
        self.assertTrue(detail['amendment_list'][0]['can_remove'])

    def test_title_only_change_is_a_rename(self):
        r = self.amend(self.author, section_id=self.sec.pk, proposed_text='The chapter is named Alpha Mu.',
                       section_title='The Name')
        self.assertEqual(r.json()['message'],
                         'Constitution Art. I § 1: its title becomes "The Name" when this passes.')
        self.assertFalse(ResolutionAmendment.objects.exists())
        change = ResolutionStructureChange.objects.get()
        self.assertEqual((change.kind, change.title, change.old_title), ('rename_section', 'The Name', 'Name'))

    def test_protected_section_and_hidden_document_refused(self):
        Section.objects.filter(pk=self.sec.pk).update(amendment_protected=True,
                                                      protected_until=datetime.date(2027, 1, 1),
                                                      protection_note='Failed in R-3')
        r = self.amend(self.chair, section_id=self.sec.pk, proposed_text='No.')
        self.assertEqual(r.json()['detail'], 'Constitution Art. I § 1 is protected from new amendments until '
                                             '2027-01-01. Reason: Failed in R-3')
        r = self.amend(self.editor, section_id=self.hidden.pk, proposed_text='Changed.')
        self.assertEqual(r.json()['detail'], 'That section cannot be amended.')
        r = self.amend(self.editor, section_id='', proposed_text='Changed.')
        self.assertEqual(r.json()['detail'], 'A section must be selected.')
        self.assertEqual(self.amend(self.editor, section_id=99999, proposed_text='x').status_code, 404)
        self.assertFalse(ResolutionAmendment.objects.exists())

    def test_viewer_and_outsider_cannot_amend(self):
        for user in (self.viewer, self.other):
            r = self.amend(user, section_id=self.sec.pk, proposed_text='Changed.')
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()['detail'],
                             'Only the Constitution & Bylaws Chair, this resolution\'s author and its editors '
                             'can change it.')
        self.assertFalse(ResolutionAmendment.objects.exists())


class StructureTests(_Base):

    def post(self, user, **body):
        return self.call(user, 'post', self.base() + 'structure/', body)

    def test_maker_data_and_new_article_with_a_section(self):
        r = self.call(self.editor, 'get', self.base() + 'structure/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([d['label'] for d in r.json()['documents']], ['Constitution'])
        r = self.post(self.editor, kind='new_article', document_id=self.con.pk, before_article_id=self.art2.pk,
                      title='Standards')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['message'].startswith('Added: '))
        article = ResolutionStructureChange.objects.get(kind='new_article')
        self.assertEqual(self.call(self.editor, 'get', self.base() + 'structure/').json()['new_articles'][0]['id'],
                         article.pk)
        r = self.post(self.editor, kind='new_section', parent_id=article.pk, title='Board', content='There is a board.')
        self.assertEqual(r.status_code, 200, r.content)
        detail = self.call(self.other, 'get', self.base()).json()
        self.assertEqual([c['kind'] for c in detail['structure_changes']], ['new_article', 'new_section'])
        # Removing the article takes its section with it.
        r = self.call(self.editor, 'delete', self.base() + f'structure/{article.pk}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(ResolutionStructureChange.objects.exists())

    def test_rename_validation_and_edit_in_place(self):
        r = self.post(self.author, kind='rename_section', section_id=self.sec.pk, title='Name')
        self.assertEqual(r.json()['detail'], 'That is already the section\'s title.')
        r = self.post(self.author, kind='rename_section', section_id=self.hidden.pk, title='Shown')
        self.assertEqual(r.json()['detail'], 'Choose a section and give its new title.')
        self.post(self.author, kind='rename_section', section_id=self.sec.pk, title='Title')
        change = ResolutionStructureChange.objects.get()
        r = self.post(self.author, change_id=change.pk, kind='new_article', section_id=self.sec.pk, title='Better')
        self.assertTrue(r.json()['message'].startswith('Updated: '))
        change.refresh_from_db()
        self.assertEqual((change.kind, change.title), ('rename_section', 'Better'))
        self.assertEqual(self.post(self.author, kind='bogus').json()['detail'], 'Unknown kind of change.')

    def test_viewer_cannot_and_applied_change_stays(self):
        self.assertEqual(self.post(self.viewer, kind='rename_article', article_id=self.art.pk,
                                   title='X').status_code, 403)
        change = ResolutionStructureChange.objects.create(resolution=self.res, kind='rename_article',
                                                          article=self.art, title='X', applied=True)
        r = self.call(self.author, 'delete', self.base() + f'structure/{change.pk}/')
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Cannot modify a closed resolution.'))
        self.assertTrue(ResolutionStructureChange.objects.filter(pk=change.pk).exists())


class StatusTests(_Base):

    def status(self, user, value):
        return self.call(user, 'post', self.base() + 'status/', {'status': value})

    def test_author_moves_draft_pending_withdrawn_not_the_result(self):
        actions = self.call(self.author, 'get', self.base()).json()['status_actions']
        self.assertEqual([a['status'] for a in actions], ['pending', 'withdrawn'])
        r = self.status(self.author, 'pending')
        self.assertEqual((r.status_code, r.json()['status'], r.json()['message']),
                         (200, 'pending', 'Resolution moved to pending vote.'))
        detail = self.call(self.author, 'get', self.base()).json()
        self.assertEqual([a['status'] for a in detail['status_actions']], ['draft'])
        self.assertTrue(detail['status_note'])
        r = self.status(self.author, 'passed')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Resolution.objects.get(pk=self.res.pk).status, 'pending')

    def test_editors_and_others_cannot_change_status(self):
        for user in (self.editor, self.viewer, self.other):
            self.assertEqual(self.status(user, 'pending').status_code, 403)
            self.assertEqual(self.call(user, 'get', self.base()).json()['status_actions'], [])
        self.assertEqual(Resolution.objects.get(pk=self.res.pk).status, 'draft')

    def test_chair_passes_and_amendments_apply(self):
        ResolutionAmendment.objects.create(resolution=self.res, section=self.sec, proposed_text='New name.')
        self.assertEqual(self.status(self.chair, 'passed').json()['detail'],
                         'This resolution is draft; it cannot be changed to "passed". Nothing was changed.')
        self.status(self.chair, 'pending')
        actions = self.call(self.chair, 'get', self.base()).json()['status_actions']
        self.assertEqual([a['status'] for a in actions], ['passed', 'failed', 'draft'])
        r = self.status(self.chair, 'passed')
        self.assertEqual((r.status_code, r.json()['level']), (200, 'success'))
        self.sec.refresh_from_db()
        self.assertEqual(self.sec.content, 'New name.')
        self.assertEqual(self.status(self.chair, 'draft').status_code, 400)   # passed is final
        self.assertEqual(self.status(self.chair, 'bogus').json()['detail'], 'Invalid status.')

    def test_failure_protects_sections(self):
        ResolutionAmendment.objects.create(resolution=self.res, section=self.sec, proposed_text='New name.')
        self.status(self.chair, 'pending')
        r = self.status(self.chair, 'failed')
        self.assertEqual(r.json()['level'], 'warning')
        self.sec.refresh_from_db()
        self.assertTrue(self.sec.amendment_protected)


class CollaboratorTests(_Base):

    def test_author_manages_brothers(self):
        r = self.call(self.author, 'get', self.base() + 'collaborators/')
        self.assertEqual(r.status_code, 200, r.content)
        names = [m['user_id'] for m in r.json()['members']]
        self.assertIn(self.other.pk, names)
        self.assertNotIn(self.pledge.pk, names)
        self.assertNotIn(self.author.pk, names)
        r = self.call(self.author, 'post', self.base() + 'collaborators/', {'user_id': self.other.pk, 'role': 'editor'})
        self.assertEqual(r.json()['message'], f'{self.other.name} added as Editor.')
        r = self.call(self.author, 'post', self.base() + 'collaborators/', {'user_id': self.other.pk, 'role': 'viewer'})
        self.assertEqual(r.json()['message'], f'{self.other.name} updated to Viewer.')
        r = self.call(self.author, 'post', self.base() + 'collaborators/', {'user_id': self.pledge.pk})
        self.assertEqual(r.json()['detail'], 'User not found.')
        r = self.call(self.author, 'post', self.base() + 'collaborators/', {'user_id': self.author.pk})
        self.assertEqual(r.json()['detail'], 'You cannot add yourself as a collaborator.')
        r = self.call(self.author, 'post', self.base() + 'collaborators/', {'user_id': self.other.pk, 'role': 'owner'})
        self.assertEqual(r.json()['detail'], 'Invalid role.')
        collab = ResolutionCollaborator.objects.get(resolution=self.res, user=self.other)
        r = self.call(self.author, 'delete', self.base() + f'collaborators/{collab.pk}/')
        self.assertEqual(r.json()['message'], f'{self.other.name} removed from collaborators.')
        self.assertFalse(ResolutionCollaborator.objects.filter(pk=collab.pk).exists())

    def test_chair_adds_anyone_editors_cannot_manage(self):
        r = self.call(self.chair, 'post', self.base() + 'collaborators/', {'user_id': self.pledge.pk})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ResolutionCollaborator.objects.get(user=self.pledge).role, 'viewer')
        for user in (self.editor, self.viewer, self.other):
            self.assertEqual(self.call(user, 'get', self.base() + 'collaborators/').status_code, 403)
            r = self.call(user, 'post', self.base() + 'collaborators/', {'user_id': self.other.pk})
            self.assertEqual(r.status_code, 403)
        collab = ResolutionCollaborator.objects.get(user=self.viewer)
        self.assertEqual(self.call(self.editor, 'delete', self.base() + f'collaborators/{collab.pk}/').status_code,
                         403)

    def test_detail_lists_collaborators_for_everyone(self):
        detail = self.call(self.other, 'get', self.base()).json()
        self.assertEqual(sorted(c['role'] for c in detail['collaborators']), ['editor', 'viewer'])
        self.assertFalse(detail['can_manage'])
        self.assertFalse(detail['can_edit'])
        self.assertTrue(self.call(self.author, 'get', self.base()).json()['can_manage'])


class WebStillWorksTests(_Base):
    """The website's builder pages after its views were split into shared helpers."""

    def test_pages_and_posts(self):
        web = Client()
        web.force_login(self.author)
        self.assertEqual(web.get(reverse('cnb_resolution_detail', args=[self.res.pk])).status_code, 200)
        self.assertEqual(web.get(reverse('cnb_edit_resolution', args=[self.res.pk])).status_code, 200)
        self.assertEqual(web.get(reverse('cnb_create_resolution')).status_code, 200)
        r = web.post(reverse('cnb_add_amendment', args=[self.res.pk]),
                     {'section_id': self.sec.pk, 'proposed_text': 'Changed.', 'next': 'edit'})
        self.assertRedirects(r, reverse('cnb_edit_resolution', args=[self.res.pk]), fetch_redirect_response=False)
        self.assertTrue(ResolutionAmendment.objects.filter(resolution=self.res, section=self.sec).exists())
        r = web.post(reverse('cnb_edit_resolution', args=[self.res.pk]), {
            'title': 'On Better Names', 'changed_fields': 'title', 'orig_title': 'On Names', 'save_stay': '1'})
        self.assertRedirects(r, reverse('cnb_edit_resolution', args=[self.res.pk]), fetch_redirect_response=False)
        r = web.post(reverse('cnb_add_structure_change', args=[self.res.pk]),
                     {'kind': 'rename_article', 'article_id': self.art.pk, 'title': 'Our Name'})
        self.assertEqual(r.status_code, 302)
        r = web.post(reverse('cnb_set_status', args=[self.res.pk]), {'status': 'pending'})
        self.assertEqual(r.status_code, 302)
        self.res.refresh_from_db()
        self.assertEqual((self.res.title, self.res.status), ('On Better Names', 'pending'))
        r = web.post(reverse('cnb_add_collaborator', args=[self.res.pk]), {'user_id': self.other.pk, 'role': 'editor'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ResolutionCollaborator.objects.filter(user=self.other, role='editor').exists())

    def test_author_can_load_a_section_into_the_amendment_editor(self):
        """11-04-26 fix: the author (not chair, not anyone's Editor) got "Not allowed."."""
        ResolutionCollaborator.objects.all().delete()
        web = Client()
        web.force_login(self.author)
        r = web.get(reverse('cnb_section_api', args=[self.sec.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['content'], 'The chapter is named Alpha Mu.')
        self.assertEqual(web.get(reverse('cnb_section_api', args=[self.hidden.pk])).status_code, 403)
        # Not once the resolution is closed, and never for someone with no open resolution.
        Resolution.objects.filter(pk=self.res.pk).update(status='passed')
        self.assertEqual(web.get(reverse('cnb_section_api', args=[self.sec.pk])).status_code, 403)
        web.force_login(self.other)
        self.assertEqual(web.get(reverse('cnb_section_api', args=[self.sec.pk])).status_code, 403)
