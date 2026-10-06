"""
v3.44.0 (10-02-26) — the batch Mason picked after the article/section maker:

  1. removing a section closes the numbering gap;
  2. the amendment editor is locked while someone is in it;
  3. the chair can take a lock over;
  4. a proposed article / section / rename can be edited in place;
  6. "how the document would read" preview;
  8. the resolution's own page shows who is editing (read-only socket);
  9. an article can be renamed directly in the C&B manager.

Run with: python manage.py test src.tests.legislation.test_cnb_v344
"""
import json
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from src import cnb_live, cnb_projection
from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment,
    ResolutionCollaborator, ResolutionStructureChange, Section,
)
from src.routing import websocket_urlpatterns


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid.lower(), member_type='Member',
                    member_status='Active', email=f'{uid.lower()}@example.com')
    defaults.update(kwargs)
    return ParliamentUser.objects.create(user_id=uid, **defaults)


class _Docs:
    def build_docs(self):
        self.chair = make_user('V44-H', is_admin=True)
        self.editor = make_user('V44-E')
        self.member = make_user('V44-M')
        self.doc = GoverningDocument.objects.filter(doc_type='bylaws').first() \
            or GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws')
        self.doc.articles.all().delete()
        self.arts = [Article.objects.create(document=self.doc, number=n, title=t, display_order=i)
                     for i, (n, t) in enumerate([('I', 'Name'), ('II', 'Officers'), ('III', 'Meetings')], 1)]
        self.secs = [Section.objects.create(article=self.arts[1], number=str(i), title=f'Sec {i}',
                                            content=f'Text {i}.', display_order=i) for i in (1, 2, 3, 4)]
        self.resolution = Resolution.objects.create(title='Restructure', created_by=self.chair)
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.editor, role='editor')

    def strike(self, section):
        return ResolutionAmendment.objects.create(
            resolution=self.resolution, section=section, proposed_text='',
            original_text_snapshot=section.content, amendment_type='deletion')

    def pass_it(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='pending')
        return self.client.post(reverse('cnb_set_status', args=[self.resolution.pk]), {'status': 'passed'})

    def live_sections(self):
        return [(s.number, s.title) for s in self.arts[1].sections.order_by('display_order')]


class RemovingASectionClosesTheGapTests(_Docs, TestCase):
    def setUp(self):
        self.build_docs()
        self.client.force_login(self.chair)

    def test_later_sections_move_up(self):
        amendment = self.strike(self.secs[1])
        self.assertEqual(amendment.renumbering_note, 'Current §§ 3–4 move up to 2–3.')
        self.pass_it()
        self.assertEqual(self.live_sections(), [('1', 'Sec 1'), ('2', 'Sec 3'), ('3', 'Sec 4')])

    def test_the_removed_section_leaves_every_listing_but_keeps_its_history(self):
        self.strike(self.secs[1])
        self.pass_it()
        self.assertFalse(Section.objects.filter(pk=self.secs[1].pk).exists())
        gone = Section.all_objects.get(pk=self.secs[1].pk)
        self.assertEqual(gone.former_number, '2')
        self.assertIsNotNone(gone.removed_at)
        self.assertEqual(gone.revisions.count(), 1)                       # the text it had
        response = self.client.get(reverse('cnb_section_history', args=[gone.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_passed_resolution_still_cites_the_old_number(self):
        amendment = self.strike(self.secs[1])
        other = ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[3], proposed_text='Changed.',
            original_text_snapshot='Text 4.', amendment_type='change')
        self.pass_it()
        amendment.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(amendment.identifier_snapshot, 'Bylaws Art. II § 2')
        self.assertEqual(other.identifier_snapshot, 'Bylaws Art. II § 4')   # voted on as § 4
        self.assertEqual(other.section.full_identifier, 'Bylaws Art. II § 3')

    def test_two_removals_and_an_edit_in_the_same_article(self):
        """Each amendment holds a copy of its section read BEFORE any renumbering."""
        self.strike(self.secs[0])
        self.strike(self.secs[2])
        ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[3], proposed_text='Four, amended.',
            original_text_snapshot='Text 4.', amendment_type='change')
        self.pass_it()
        self.assertEqual(self.live_sections(), [('1', 'Sec 2'), ('2', 'Sec 4')])
        self.assertEqual(Section.objects.get(pk=self.secs[3].pk).content, 'Four, amended.')
        cited = sorted(ResolutionAmendment.objects.values_list('identifier_snapshot', flat=True))
        self.assertEqual(cited, ['Bylaws Art. II § 1', 'Bylaws Art. II § 3', 'Bylaws Art. II § 4'])

    def test_last_section_renumbers_nothing(self):
        amendment = self.strike(self.secs[3])
        self.assertIn('nothing is renumbered', amendment.renumbering_note)
        self.pass_it()
        self.assertEqual(self.live_sections(), [('1', 'Sec 1'), ('2', 'Sec 2'), ('3', 'Sec 3')])

    def test_a_number_that_cannot_shift_leaves_it_switched_off_as_before(self):
        Section.objects.filter(pk=self.secs[3].pk).update(number='4a')
        amendment = self.strike(self.secs[1])
        self.assertIn('a gap is left', amendment.renumbering_note)
        self.pass_it()
        still = Section.objects.get(pk=self.secs[1].pk)                   # still listed
        self.assertFalse(still.is_active)
        self.assertEqual(still.number, '2')

    def test_a_partial_deletion_removes_nothing(self):
        ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[1], proposed_text='Text.',
            original_text_snapshot='Text 2.', amendment_type='deletion', scope_note='second sentence')
        self.pass_it()
        self.assertEqual(len(self.live_sections()), 4)

    def test_as_of_a_date_before_the_removal_still_shows_it(self):
        self.strike(self.secs[1])
        self.pass_it()
        # The section predates tracking (as seeded text does), so only the removal decides.
        Section.all_objects.filter(article=self.arts[1]).update(created_at=None)
        from src.view.cnb_history import apply_as_of
        from django.db.models import Prefetch
        docs = list(GoverningDocument.objects.filter(pk=self.doc.pk).prefetch_related(
            Prefetch('articles__sections', queryset=Section.all_objects.prefetch_related('revisions'))))
        apply_as_of(docs, timezone.now() - timedelta(days=1))
        shown = [s for a in docs[0].articles.all() for s in a.sections.all()
                 if s.pk == self.secs[1].pk and not getattr(s, 'as_of_hidden', False)]
        self.assertEqual(len(shown), 1)
        self.assertEqual((shown[0].number, shown[0].content), ('2', 'Text 2.'))


class RenameArticleDirectlyTests(_Docs, TestCase):
    def setUp(self):
        self.build_docs()

    def test_chair_can_and_others_cannot(self):
        url = reverse('cnb_rename_article', args=[self.arts[1].pk])
        self.client.force_login(self.editor)
        self.client.post(url, {'title': 'Hijack'})
        self.assertEqual(Article.objects.get(pk=self.arts[1].pk).title, 'Officers')
        self.client.force_login(self.chair)
        self.client.post(url, {'title': 'Executive Board'})
        self.assertEqual(Article.objects.get(pk=self.arts[1].pk).title, 'Executive Board')

    def test_blank_title_is_refused(self):
        self.client.force_login(self.chair)
        self.client.post(reverse('cnb_rename_article', args=[self.arts[1].pk]), {'title': '  '})
        self.assertEqual(Article.objects.get(pk=self.arts[1].pk).title, 'Officers')

    def test_manager_page_has_the_form(self):
        self.client.force_login(self.chair)
        html = self.client.get(reverse('cnb_manage_document', args=['bylaws'])).content.decode()
        self.assertIn(reverse('cnb_rename_article', args=[self.arts[1].pk]), html)


class EditAProposalInPlaceTests(_Docs, TestCase):
    def setUp(self):
        self.build_docs()
        self.client.force_login(self.chair)
        self.url = reverse('cnb_add_structure_change', args=[self.resolution.pk])

    def test_title_and_position_change_on_the_same_row(self):
        self.client.post(self.url, {'kind': 'new_article', 'document_id': self.doc.pk,
                                    'before_article_id': self.arts[1].pk, 'title': 'Standards'})
        change = ResolutionStructureChange.objects.get()
        self.client.post(self.url, {'change_id': change.pk, 'document_id': self.doc.pk,
                                    'before_article_id': '', 'title': 'Standards Board'})
        self.assertEqual(ResolutionStructureChange.objects.count(), 1)
        change.refresh_from_db()
        self.assertEqual((change.title, change.before_article), ('Standards Board', None))

    def test_new_section_text_can_be_edited(self):
        self.client.post(self.url, {'kind': 'new_section', 'article_id': self.arts[1].pk,
                                    'before_section_id': '', 'title': 'T', 'content': 'First draft.'})
        change = ResolutionStructureChange.objects.get()
        self.client.post(self.url, {'change_id': change.pk, 'article_id': self.arts[1].pk,
                                    'before_section_id': self.secs[0].pk, 'title': 'T', 'content': 'Second draft.'})
        change.refresh_from_db()
        self.assertEqual((change.content, change.before_section_id), ('Second draft.', self.secs[0].pk))

    def test_a_proposal_from_another_resolution_cannot_be_edited_through_this_one(self):
        other = Resolution.objects.create(title='Other', created_by=self.chair)
        foreign = ResolutionStructureChange.objects.create(
            resolution=other, kind='rename_article', article=self.arts[0], title='X', old_title='Name')
        self.client.post(self.url, {'change_id': foreign.pk, 'article_id': self.arts[0].pk, 'title': 'Y'})
        foreign.refresh_from_db()
        self.assertEqual(foreign.title, 'X')


class DocumentPreviewTests(_Docs, TestCase):
    def setUp(self):
        self.build_docs()
        self.client.force_login(self.chair)

    def _build(self):
        self.strike(self.secs[1])
        ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[0], proposed_text='Text one, amended.',
            original_text_snapshot='Text 1.', amendment_type='change')
        ResolutionStructureChange.objects.create(
            resolution=self.resolution, kind='new_article', document=self.doc,
            before_article=self.arts[1], title='Standards')
        ResolutionStructureChange.objects.create(
            resolution=self.resolution, kind='rename_article', article=self.arts[2],
            title='Chapter Meetings', old_title='Meetings')

    def test_it_reports_what_would_change(self):
        self._build()
        result = cnb_projection.project(self.resolution, self.chair)
        self.assertEqual(result['error'], '')
        articles = result['documents'][0]['articles']
        self.assertEqual([(a['number'], a['title']) for a in articles],
                         [('I', 'Name'), ('II', 'Standards'), ('III', 'Officers'), ('IV', 'Chapter Meetings')])
        self.assertTrue(articles[1]['is_new'])
        self.assertEqual(articles[2]['old_number'], 'II')
        self.assertEqual((articles[3]['old_number'], articles[3]['old_title']), ('III', 'Meetings'))
        self.assertFalse(articles[0]['touched'])
        status = {(s['number'], s['title']): s for s in articles[2]['sections']}
        self.assertEqual(status[('1', 'Sec 1')]['status'], 'changed')
        self.assertIn('<ins', status[('1', 'Sec 1')]['html'])
        self.assertEqual(status[('2', 'Sec 2')]['status'], 'removed')
        self.assertEqual((status[('2', 'Sec 3')]['status'], status[('2', 'Sec 3')]['old_number']), ('moved', '3'))

    def test_previewing_changes_nothing(self):
        self._build()
        before = list(Section.all_objects.order_by('pk').values_list('pk', 'number', 'content', 'is_active', 'removed_at'))
        self.client.get(reverse('cnb_resolution_document_preview', args=[self.resolution.pk]))
        self.client.get(reverse('cnb_resolution_document_preview', args=[self.resolution.pk]))
        after = list(Section.all_objects.order_by('pk').values_list('pk', 'number', 'content', 'is_active', 'removed_at'))
        self.assertEqual(before, after)
        self.assertEqual([a.number for a in self.doc.articles.order_by('display_order')], ['I', 'II', 'III'])
        self.assertFalse(ResolutionAmendment.objects.filter(applied=True).exists())
        self.assertFalse(ResolutionStructureChange.objects.filter(applied=True).exists())
        self.assertEqual(Resolution.objects.get(pk=self.resolution.pk).status, 'draft')
        self.assertEqual(self.secs[1].revisions.count(), 0)

    def test_page_renders_for_a_member(self):
        self._build()
        self.client.force_login(self.member)
        response = self.client.get(reverse('cnb_resolution_document_preview', args=[self.resolution.pk]))
        self.assertContains(response, 'New article')
        self.assertContains(response, 'was Article II')

    def test_a_resolution_that_cannot_apply_says_so(self):
        self._build()
        Article.objects.filter(pk=self.arts[2].pk).update(number='III-A')
        response = self.client.get(reverse('cnb_resolution_document_preview', args=[self.resolution.pk]))
        self.assertContains(response, 'could not be applied')

    def test_a_passed_resolution_has_nothing_to_preview(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='passed')
        response = self.client.get(reverse('cnb_resolution_document_preview', args=[self.resolution.pk]))
        self.assertContains(response, 'nothing to preview')


class SocketTests(_Docs, TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.build_docs()

    def _comm(self, user, watch=False):
        comm = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f'/ws/cnb/resolutions/{self.resolution.pk}/' + ('watch/' if watch else ''))
        comm.scope['user'] = user
        return comm

    async def _until(self, comm, kind, field=None):
        for _ in range(12):
            msg = json.loads(await comm.receive_from(timeout=2))
            if msg['t'] == kind and (field is None or msg.get('f') == field):
                return msg
        raise AssertionError(f'no {kind} message')

    def test_an_observer_sees_who_is_editing_and_nothing_else(self):
        async def run():
            editor = self._comm(self.editor)
            await editor.connect()
            await self._until(editor, 'init')
            watcher = self._comm(self.member, watch=True)          # a plain member may watch
            connected, _ = await watcher.connect()
            self.assertTrue(connected)
            self.assertTrue((await self._until(watcher, 'init'))['observer'])
            self.assertEqual((await self._until(watcher, 'here'))['who']['name'], self.editor.get_display_name())
            # The editor is not told about the observer.
            self.assertTrue(await editor.receive_nothing(timeout=0.3))
            # Locks, drafts and saves do not reach the observer.
            await editor.send_to(text_data=json.dumps({'t': 'lock', 'f': 'title'}))
            await self._until(editor, 'lock')
            await editor.send_to(text_data=json.dumps({'t': 'draft', 'f': 'title', 'v': 'secret draft'}))
            self.assertTrue(await watcher.receive_nothing(timeout=0.4))
            # An observer cannot lock.
            await watcher.send_to(text_data=json.dumps({'t': 'lock', 'f': 'authors'}))
            locks = await database_sync_to_async(cnb_live.current_locks)(self.resolution.pk)
            self.assertEqual(set(locks), {'title'})
            await editor.disconnect()
            await self._until(watcher, 'leave')
            await watcher.disconnect()
        async_to_sync(run)()

    def test_a_plain_member_still_cannot_use_the_editor_socket(self):
        async def run():
            connected, _ = await self._comm(self.member).connect()
            self.assertFalse(connected)
        async_to_sync(run)()

    def test_amendment_lock_and_chair_take_over(self):
        key = f'amend:{self.secs[0].pk}'

        async def run():
            editor = self._comm(self.editor)
            await editor.connect()
            init_e = await self._until(editor, 'init')
            self.assertFalse(init_e['chair'])
            chair = self._comm(self.chair)
            await chair.connect()
            self.assertTrue((await self._until(chair, 'init'))['chair'])

            await editor.send_to(text_data=json.dumps({'t': 'lock', 'f': key}))
            await self._until(chair, 'lock')
            await chair.send_to(text_data=json.dumps({'t': 'lock', 'f': key}))
            self.assertEqual((await self._until(chair, 'denied'))['who']['cid'], init_e['me']['cid'])

            # A newcomer is told about the amendment lock too.
            late = self._comm(self.chair)
            await late.connect()
            self.assertIn(key, (await self._until(late, 'init'))['locks'])
            await late.disconnect()

            # The editor cannot take over; the chair can.
            await chair.send_to(text_data=json.dumps({'t': 'lock', 'f': 'title'}))
            await self._until(editor, 'lock', 'title')
            await editor.send_to(text_data=json.dumps({'t': 'takeover', 'f': 'title'}))
            await editor.send_to(text_data=json.dumps({'t': 'ping'}))
            locks = await database_sync_to_async(cnb_live.current_locks)(self.resolution.pk)
            self.assertIn('title', locks)

            await chair.send_to(text_data=json.dumps({'t': 'takeover', 'f': key}))
            unlock = await self._until(editor, 'unlock', key)
            self.assertEqual(unlock['taken_by'], self.chair.get_display_name())
            await chair.send_to(text_data=json.dumps({'t': 'lock', 'f': key}))
            lock = await self._until(editor, 'lock', key)
            self.assertEqual(lock['who']['name'], self.chair.get_display_name())
            await editor.disconnect()
            await chair.disconnect()
        async_to_sync(run)()

    def test_only_field_names_and_amendment_keys_are_lockable(self):
        for name, ok in (('title', True), ('amend:12', True), ('amend:', False), ('amend:1; DROP', False),
                         ('status', False), (None, False), (12, False)):
            self.assertEqual(cnb_live.lockable(name), ok, name)
