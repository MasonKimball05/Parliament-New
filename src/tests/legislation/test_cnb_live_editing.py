"""
Live editing on the resolution edit page (v3.42.0, 10-02-26).

Mason: "the edit resolution page should have a websocket so work isn't
written and overwritten by multiple people working on it at the same time."

Design: `src/cnb_live.py`. The promise these tests guard, in order:

1. a save writes only the fields that person changed;
2. a field someone else changed in the meantime is never overwritten — the
   editor gets both versions back;
3. the socket only admits people who may edit, a field has one lock holder at
   a time, and a disconnect gives the locks back.

Run with: python manage.py test src.tests.legislation.test_cnb_live_editing
"""
import json

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from src import cnb_live
from src.models import ParliamentUser, Resolution, ResolutionCollaborator
from src.routing import websocket_urlpatterns


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid.lower(), member_type='Member',
                    member_status='Active', email=f'{uid.lower()}@example.com')
    defaults.update(kwargs)
    return ParliamentUser.objects.create(user_id=uid, **defaults)


class FieldLevelSaveTests(TestCase):

    def setUp(self):
        self.chair = make_user('LIVE-H1', is_admin=True)
        self.editor = make_user('LIVE-E1')
        self.resolution = Resolution.objects.create(
            title='Title', created_by=self.chair, authors='A. Author',
            whereas_clauses='Whereas one.', resolved_text='Resolved one.')
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.editor, role='editor')
        self.url = reverse('cnb_edit_resolution', args=[self.resolution.pk])
        self.client.force_login(self.editor)

    def _reload(self):
        self.resolution.refresh_from_db()
        return self.resolution

    def test_only_changed_fields_are_written(self):
        """The stale-overwrite bug: my tab still shows the OLD authors."""
        Resolution.objects.filter(pk=self.resolution.pk).update(authors='Changed by someone else')
        self.client.post(self.url, {
            'title': 'Title', 'authors': 'A. Author',            # stale, untouched
            'whereas_clauses': 'Whereas one, edited.',
            'changed_fields': 'whereas_clauses', 'orig_whereas_clauses': 'Whereas one.',
            'save_stay': '1',
        })
        r = self._reload()
        self.assertEqual(r.whereas_clauses, 'Whereas one, edited.')
        self.assertEqual(r.authors, 'Changed by someone else')

    def test_a_field_changed_by_someone_else_is_not_overwritten(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(whereas_clauses='Their version.')
        response = self.client.post(self.url, {
            'title': 'Title', 'whereas_clauses': 'My version.', 'resolved_text': 'Resolved, mine.',
            'changed_fields': 'whereas_clauses,resolved_text',
            'orig_whereas_clauses': 'Whereas one.', 'orig_resolved_text': 'Resolved one.',
            'save_stay': '1',
        })
        self.assertEqual(response.status_code, 409)
        r = self._reload()
        self.assertEqual(r.whereas_clauses, 'Their version.')        # kept
        self.assertEqual(r.resolved_text, 'Resolved, mine.')         # no conflict: saved
        html = response.content.decode()
        self.assertIn('My version.', html)                            # still in my box
        self.assertIn('Their version.', html)                         # shown to me
        # The page's baseline is now THEIR version, so my next save is a
        # deliberate replace rather than another conflict.
        self.assertEqual(response.context['live_baseline']['whereas_clauses'], 'Their version.')

    def test_saving_again_after_a_conflict_replaces_their_version(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(whereas_clauses='Their version.')
        self.client.post(self.url, {
            'title': 'Title', 'whereas_clauses': 'Merged.', 'changed_fields': 'whereas_clauses',
            'orig_whereas_clauses': 'Their version.', 'save_stay': '1',
        })
        self.assertEqual(self._reload().whereas_clauses, 'Merged.')

    def test_same_text_from_both_people_is_not_a_conflict(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(whereas_clauses='Same.')
        response = self.client.post(self.url, {
            'title': 'Title', 'whereas_clauses': 'Same.', 'changed_fields': 'whereas_clauses',
            'orig_whereas_clauses': 'Whereas one.', 'save_stay': '1',
        })
        self.assertEqual(response.status_code, 302)

    def test_line_endings_alone_are_not_a_change_or_a_conflict(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(whereas_clauses='One.\r\nTwo.')
        response = self.client.post(self.url, {
            'title': 'Title', 'whereas_clauses': 'One.\nTwo.\nThree.', 'changed_fields': 'whereas_clauses',
            'orig_whereas_clauses': 'One.\nTwo.', 'save_stay': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._reload().whereas_clauses, 'One.\nTwo.\nThree.')

    def test_a_post_without_changed_fields_still_saves_everything(self):
        """No JavaScript, or a tab opened before this release."""
        self.client.post(self.url, {'title': 'New title', 'resolution_type': 'general', 'authors': 'B'})
        r = self._reload()
        self.assertEqual((r.title, r.resolution_type, r.authors), ('New title', 'general', 'B'))

    def test_the_page_carries_the_baseline_and_the_socket(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('id="live-baseline"', html)
        self.assertIn('name="changed_fields"', html)
        self.assertIn(f'/ws/cnb/resolutions/{self.resolution.pk}/', html)

    def test_the_create_page_has_none_of_it(self):
        self.client.force_login(self.chair)
        html = self.client.get(reverse('cnb_create_resolution')).content.decode()
        self.assertNotIn('live-baseline', html)
        self.assertNotIn('/ws/cnb/resolutions/', html)


class LockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a = {'cid': 'aaa', 'uid': '1', 'name': 'A'}
        self.b = {'cid': 'bbb', 'uid': '2', 'name': 'B'}

    def test_one_holder_at_a_time(self):
        self.assertEqual(cnb_live.acquire_lock(1, 'title', self.a)['cid'], 'aaa')
        self.assertEqual(cnb_live.acquire_lock(1, 'title', self.b)['cid'], 'aaa')
        self.assertEqual(cnb_live.acquire_lock(1, 'authors', self.b)['cid'], 'bbb')
        self.assertEqual(set(cnb_live.current_locks(1)), {'title', 'authors'})

    def test_only_the_holder_can_release(self):
        cnb_live.acquire_lock(1, 'title', self.a)
        self.assertFalse(cnb_live.release_lock(1, 'title', 'bbb'))
        self.assertTrue(cnb_live.release_lock(1, 'title', 'aaa'))
        self.assertEqual(cnb_live.acquire_lock(1, 'title', self.b)['cid'], 'bbb')

    def test_locks_are_per_resolution(self):
        cnb_live.acquire_lock(1, 'title', self.a)
        self.assertEqual(cnb_live.acquire_lock(2, 'title', self.b)['cid'], 'bbb')


class ConsumerTests(TransactionTestCase):
    """Real consumer, in-memory channel layer."""

    def setUp(self):
        cache.clear()
        self.chair = make_user('LIVE-H2', is_admin=True)
        self.editor = make_user('LIVE-E2')
        self.viewer = make_user('LIVE-V2')
        self.member = make_user('LIVE-M2')
        self.resolution = Resolution.objects.create(title='T', created_by=self.chair)
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.editor, role='editor')
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.viewer, role='viewer')

    def _communicator(self, user, resolution=None):
        comm = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f'/ws/cnb/resolutions/{(resolution or self.resolution).pk}/')
        comm.scope['user'] = user
        return comm

    async def _until(self, comm, kind):
        for _ in range(10):
            msg = json.loads(await comm.receive_from(timeout=2))
            if msg['t'] == kind:
                return msg
        raise AssertionError(f'no {kind} message')

    def test_only_people_who_may_edit_can_connect(self):
        async def run():
            for user, expected in ((self.chair, True), (self.editor, True),
                                   (self.viewer, False), (self.member, False)):
                comm = self._communicator(user)
                connected, _ = await comm.connect()
                self.assertEqual(connected, expected, user.user_id)
                await comm.disconnect()
        async_to_sync(run)()

    def test_a_closed_resolution_cannot_be_joined(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='passed')

        async def run():
            comm = self._communicator(self.chair)
            connected, _ = await comm.connect()
            self.assertFalse(connected)
        async_to_sync(run)()

    def test_presence_lock_denied_and_release_on_disconnect(self):
        async def run():
            a = self._communicator(self.chair)
            await a.connect()
            init_a = await self._until(a, 'init')
            b = self._communicator(self.editor)
            await b.connect()
            await self._until(b, 'init')

            # presence, both directions
            self.assertEqual((await self._until(a, 'join'))['who']['name'], self.editor.get_display_name())
            self.assertEqual((await self._until(b, 'here'))['who']['cid'], init_a['me']['cid'])

            # A takes the title; B is told; B cannot take it
            await a.send_to(text_data=json.dumps({'t': 'lock', 'f': 'title'}))
            self.assertEqual((await self._until(b, 'lock'))['f'], 'title')
            await b.send_to(text_data=json.dumps({'t': 'lock', 'f': 'title'}))
            denied = await self._until(b, 'denied')
            self.assertEqual(denied['who']['cid'], init_a['me']['cid'])

            # a field name that is not a form field is ignored
            await b.send_to(text_data=json.dumps({'t': 'lock', 'f': 'status'}))
            locks = await database_sync_to_async(cnb_live.current_locks)(self.resolution.pk)
            self.assertEqual(set(locks), {'title'})

            # A leaves: the lock comes back and B hears about both
            await a.disconnect()
            self.assertEqual((await self._until(b, 'unlock'))['f'], 'title')
            await self._until(b, 'leave')
            locks = await database_sync_to_async(cnb_live.current_locks)(self.resolution.pk)
            self.assertEqual(locks, {})
            await b.disconnect()
        async_to_sync(run)()

    def test_a_draft_is_relayed_only_from_the_lock_holder(self):
        """v3.42.2 — the others watch the holder type; nobody else can push text."""
        async def run():
            a = self._communicator(self.chair)
            await a.connect()
            await self._until(a, 'init')
            b = self._communicator(self.editor)
            await b.connect()
            await self._until(b, 'init')
            await self._until(a, 'join')

            # B does not hold the title: B's "draft" goes nowhere.
            await b.send_to(text_data=json.dumps({'t': 'draft', 'f': 'title', 'v': 'not mine'}))
            self.assertTrue(await a.receive_nothing(timeout=0.3))

            await a.send_to(text_data=json.dumps({'t': 'lock', 'f': 'title'}))
            await self._until(b, 'lock')
            await a.send_to(text_data=json.dumps({'t': 'draft', 'f': 'title', 'v': 'Typing'}))
            draft = await self._until(b, 'draft')
            self.assertEqual((draft['f'], draft['v']), ('title', 'Typing'))
            await a.disconnect()
            await b.disconnect()
        async_to_sync(run)()

    def test_an_amendment_change_says_what_happened(self):
        async def run():
            b = self._communicator(self.editor)
            await b.connect()
            await self._until(b, 'init')
            await database_sync_to_async(cnb_live.broadcast_amendments_changed)(
                self.resolution.pk, self.chair, 'added an amendment to Constitution Art. III § 3')
            msg = await self._until(b, 'amendments')
            self.assertEqual(msg['what'], 'added an amendment to Constitution Art. III § 3')
            await b.disconnect()
        async_to_sync(run)()

    def test_a_save_is_broadcast_to_the_other_editor(self):
        async def run():
            b = self._communicator(self.editor)
            await b.connect()
            await self._until(b, 'init')
            await database_sync_to_async(self._save_as_chair)()
            saved = await self._until(b, 'saved')
            self.assertEqual(saved['fields'], {'authors': 'New author'})
            await b.disconnect()
        async_to_sync(run)()

    def _save_as_chair(self):
        from django.test import Client
        client = Client()
        client.force_login(self.chair)
        client.post(reverse('cnb_edit_resolution', args=[self.resolution.pk]),
                    {'title': 'T', 'authors': 'New author', 'changed_fields': 'authors',
                     'orig_authors': '', 'save_stay': '1'})
