"""
Mobile plan, Phase 3j — the Songbook and the Quote Book in the app.

The app must show what the website shows and allow what it allows: songs and
their audio to members (pledge restrictions apply), the quote book behind its
page toggle, flagging by the same people the web lets flag (and deleting your
own quote about yourself), and the flagged list scoped to standing.

    python manage.py test src.tests.mobile.test_songbook_quotes
"""
import os
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings

from src.models import Quote, Song, SongCategory
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'
MEDIA = tempfile.mkdtemp()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], MEDIA_ROOT=MEDIA)
class _Base(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.user = make_user('sq-1')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def as_(self, user):
        _, access, _ = make_device(user)
        return access

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, body or {}, content_type='application/json', **bearer(access or self.access))


class SongbookTests(_Base):

    def setUp(self):
        super().setUp()
        self.hymns = SongCategory.objects.create(name='Hymns', color='green')
        self.song = Song.objects.create(title='Marching Song', lyrics='\n  We march on\nand on', category=self.hymns)
        self.song.audio_file.save('march.mp3', ContentFile(b'ID3fake'), save=True)
        Song.objects.create(title='Hidden', lyrics='x', is_active=False)

    def test_list_detail_and_audio(self):
        r = self.get('songbook/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([s['title'] for s in body['songs']], ['Marching Song'])   # inactive hidden
        s = body['songs'][0]
        self.assertEqual((s['first_line'], s['has_audio'], s['category_id']), ('We march on', True, self.hymns.id))
        self.assertEqual(body['categories'][0]['color'], 'green')
        self.assertFalse(body['can_manage'])

        d = self.get(f'songbook/songs/{self.song.id}/').json()
        self.assertEqual((d['category']['name'], d['audio_ext']), ('Hymns', '.mp3'))
        audio = self.get(f'songbook/songs/{self.song.id}/audio/')
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(b''.join(audio.streaming_content), b'ID3fake')
        self.assertEqual(audio['Content-Type'], 'audio/mpeg')

    def test_missing_audio_and_pdfs_404(self):
        os.remove(self.song.audio_file.path)
        self.assertEqual(self.get(f'songbook/songs/{self.song.id}/audio/').status_code, 404)
        self.assertEqual(self.get('songbook/pdf/../../settings.py/').status_code, 404)
        self.assertEqual(self.get('songbook/pdf/nope/').status_code, 404)

    def test_chorister_can_manage_and_pledge_restriction(self):
        officer = make_user('sq-off', member_type='Officer')
        self.assertTrue(self.get('songbook/', access=self.as_(officer)).json()['can_manage'])
        pledge = make_user('sq-pl', member_type='Pledge')
        restrict('songbook', phases=())
        r = self.get('songbook/', access=self.as_(pledge))
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))


class QuoteBookTests(_Base):

    def setUp(self):
        super().setUp()
        self.other = make_user('sq-2', name='Bob Smith')
        self.third = make_user('sq-3')
        self.q_about_other = Quote.objects.create(quoted_member=self.other, text='Hi', submitted_by=self.third)
        self.q_mine_self = Quote.objects.create(quoted_member=self.user, text='Me', submitted_by=self.user)

    def test_book_groups_by_member_with_permissions(self):
        r = self.get('quote-book/')
        self.assertEqual(r.status_code, 200, r.content)
        chapters = r.json()['chapters']
        self.assertEqual([c['name'] for c in chapters], ['Bob Smith', 'User sq-1'])
        bob = chapters[0]['quotes'][0]
        self.assertFalse(bob['can_flag'])                 # not quoted, not submitter, not officer
        mine = chapters[1]['quotes'][0]
        self.assertTrue(mine['can_flag'] and mine['delete_not_flag'])
        self.assertEqual(r.json()['flagged_count'], 0)

    def test_toggle_off(self):
        toggle('quote_book', False)
        r = self.get('quote-book/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'page_disabled'))

    def test_add_quote(self):
        r = self.post('quote-book/', {'quoted_member': self.other.pk, 'text': '  Said it  ', 'context': 'At dinner'})
        self.assertEqual(r.status_code, 201, r.content)
        q = Quote.objects.get(pk=r.json()['quote']['id'])
        self.assertEqual((q.text, q.submitted_by, q.quoted_member), ('Said it', self.user, self.other))
        self.assertEqual(self.post('quote-book/', {'quoted_member': self.other.pk, 'text': ''}).status_code, 400)
        removed = make_user('sq-x', member_status='Removed')
        self.assertEqual(self.post('quote-book/', {'quoted_member': removed.pk, 'text': 'x'}).status_code, 400)
        ids = [m['user_id'] for m in self.get('quote-book/members/').json()['members']]
        self.assertIn(self.other.pk, ids)
        self.assertNotIn(removed.pk, ids)

    def test_flag_delete_and_restore(self):
        # Own quote about yourself: deleted outright.
        r = self.post(f'quote-book/{self.q_mine_self.id}/flag/')
        self.assertEqual(r.json(), {'ok': True, 'deleted': True})
        self.assertFalse(Quote.objects.filter(pk=self.q_mine_self.id).exists())
        # Someone else's quote: refused.
        self.assertEqual(self.post(f'quote-book/{self.q_about_other.id}/flag/').status_code, 403)
        # The quoted member flags it; it leaves the book and shows in their flagged list.
        bob = self.as_(self.other)
        self.assertEqual(self.post(f'quote-book/{self.q_about_other.id}/flag/', access=bob).json()['deleted'], False)
        self.assertEqual(self.get('quote-book/', access=bob).json()['chapters'], [])
        self.assertEqual(self.get('quote-book/', access=bob).json()['flagged_count'], 1)
        flagged = self.get('quote-book/flagged/', access=bob).json()['quotes']
        self.assertEqual((flagged[0]['id'], flagged[0]['can_restore']), (self.q_about_other.id, True))
        # A member with no standing doesn't see it, and can't restore it.
        self.assertEqual(self.get('quote-book/flagged/').json()['quotes'], [])
        self.assertEqual(self.post(f'quote-book/{self.q_about_other.id}/restore/').status_code, 403)
        # An officer sees and restores it.
        officer = self.as_(make_user('sq-off', member_type='Officer'))
        self.assertEqual(len(self.get('quote-book/flagged/', access=officer).json()['quotes']), 1)
        self.assertEqual(self.post(f'quote-book/{self.q_about_other.id}/restore/', access=officer).status_code, 200)
        self.q_about_other.refresh_from_db()
        self.assertIsNone(self.q_about_other.flagged_at)
