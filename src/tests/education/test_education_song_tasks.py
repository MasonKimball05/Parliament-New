"""
Song-linked pledge tasks — 09-22-26, Mason's request: pledges have to learn
some of the fraternity's songs, so a chair creating a Song-type task can
link it to a real Songbook entry, and the pledge sees that song's lyrics and
audio example right on their own My Tasks page.

Covers:
* `PledgeTask.song` (FK to `Song`) — saved only for task_type='song',
  cleared otherwise, and only ever points at an active song.
* `education_duplicate_task` carries the link forward on a clone.
* `my_pledge_tasks` (the pledge-facing page) renders the linked song's
  lyrics and, when present, an audio player — and says something sane
  when a Song task has no song linked yet, rather than rendering nothing
  with no explanation.
* A soft-deleted song stays linked to an existing task (SET_NULL only
  fires on a hard delete) but is no longer offered when creating/linking a
  NEW task.

Run with: python manage.py test src.tests.education.test_education_song_tasks
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, PledgeTask, PledgeTaskCompletion, Song, SongCategory
from src.tests.education._fixtures import EducationFixtureMixin


def make_song(title='Chapter Hymn', lyrics='Some lyrics here', category=None, is_active=True):
    return Song.objects.create(title=title, lyrics=lyrics, category=category, is_active=is_active)


class TaskFormSavesSongLinkTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.song = make_song()

    def _create(self, **overrides):
        payload = {
            'title': 'Learn the hymn', 'task_type': 'song', 'phase': 'all',
            'points': '0', 'display_order': '0', 'activation_mode': 'immediate',
            'song': str(self.song.pk),
        }
        payload.update(overrides)
        return self.client.post(reverse('education_add_task', args=[self.committee.code]), payload)

    def test_a_song_task_saves_the_link(self):
        self._create()
        task = PledgeTask.objects.get(title='Learn the hymn')
        self.assertEqual(task.song_id, self.song.pk)

    def test_the_link_is_cleared_for_a_non_song_type(self):
        self._create(task_type='task', song=str(self.song.pk))
        task = PledgeTask.objects.get(title='Learn the hymn')
        self.assertIsNone(task.song_id)

    def test_a_blank_song_selection_is_fine(self):
        """A chair can create the task before deciding which song, or before
        anyone has added it to the songbook yet."""
        self._create(song='')
        task = PledgeTask.objects.get(title='Learn the hymn')
        self.assertIsNone(task.song_id)

    def test_a_song_pk_that_does_not_exist_is_silently_ignored_not_a_500(self):
        self._create(song='999999')
        task = PledgeTask.objects.get(title='Learn the hymn')
        self.assertIsNone(task.song_id)

    def test_a_soft_deleted_song_is_not_offered_as_a_new_choice(self):
        """
        The dropdown is the actual boundary here (see
        `TaskFormOffersActiveSongsAndKeepsTheCurrentOneTests`), not the
        server-side parser — `_apply_task_fields` deliberately does not
        re-check `is_active` (see its comment), because a stricter server
        check would also strip a task's own already-linked song the moment
        it's deactivated and the chair saves an unrelated edit. This is a
        chair-only, no-confidentiality-boundary field; a crafted POST
        naming an inactive song's pk is not a case worth adding real
        complexity to block.
        """
        self.song.is_active = False
        self.song.save()
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertNotIn(self.song, list(response.context['songs']))

    def test_editing_an_existing_task_can_change_the_song(self):
        self._create()
        task = PledgeTask.objects.get(title='Learn the hymn')
        other_song = make_song(title='Another Song')
        self.client.post(
            reverse('education_edit_task', args=[self.committee.code, task.pk]),
            {
                'title': task.title, 'task_type': 'song', 'phase': 'all',
                'points': '0', 'display_order': '0', 'activation_mode': 'immediate',
                'song': str(other_song.pk),
            },
        )
        task.refresh_from_db()
        self.assertEqual(task.song_id, other_song.pk)

    def test_switching_type_away_from_song_on_edit_clears_the_link(self):
        self._create()
        task = PledgeTask.objects.get(title='Learn the hymn')
        self.client.post(
            reverse('education_edit_task', args=[self.committee.code, task.pk]),
            {
                'title': task.title, 'task_type': 'milestone', 'phase': 'all',
                'points': '0', 'display_order': '0', 'activation_mode': 'immediate',
            },
        )
        task.refresh_from_db()
        self.assertIsNone(task.song_id)


class DuplicateTaskCarriesSongTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.song = make_song()
        self.original = PledgeTask.objects.create(
            title='Original song task', task_type='song', song=self.song, created_by=self.chair,
        )

    def test_the_clone_keeps_the_same_song(self):
        self.client.post(
            reverse('education_duplicate_task', args=[self.committee.code, self.original.pk])
        )
        clone = PledgeTask.objects.get(title__contains='(copy)')
        self.assertEqual(clone.song_id, self.song.pk)


class TaskFormOffersActiveSongsAndKeepsTheCurrentOneTests(EducationFixtureMixin, TestCase):
    """The edit page's song dropdown must not silently drop the task's own
    (possibly since-deactivated) song out from under a chair editing
    something unrelated."""

    def setUp(self):
        self.build()
        self.active_song = make_song(title='Still Active')
        self.deactivated_song = make_song(title='Now Retired', is_active=False)

    def test_the_add_task_modal_only_offers_active_songs(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        songs = list(response.context['songs'])
        self.assertIn(self.active_song, songs)
        self.assertNotIn(self.deactivated_song, songs)

    def test_the_edit_page_still_offers_a_task_s_own_deactivated_song(self):
        task = PledgeTask.objects.create(
            title='Old link', task_type='song', song=self.deactivated_song, created_by=self.chair,
        )
        response = self.client.get(reverse('education_edit_task', args=[self.committee.code, task.pk]))
        songs = list(response.context['songs'])
        self.assertIn(self.deactivated_song, songs)

    def test_saving_the_edit_page_without_touching_the_field_keeps_the_deactivated_song(self):
        """
        Regression: if the deactivated song were missing from the dropdown,
        submitting the form (leaving 'song' pointed at whatever option the
        browser defaults to) would silently clear a link the chair never
        touched.
        """
        task = PledgeTask.objects.create(
            title='Old link', task_type='song', song=self.deactivated_song, created_by=self.chair,
        )
        self.client.post(
            reverse('education_edit_task', args=[self.committee.code, task.pk]),
            {
                'title': task.title, 'task_type': 'song', 'phase': 'all',
                'points': '0', 'display_order': '0', 'activation_mode': 'immediate',
                'song': str(self.deactivated_song.pk),
            },
        )
        task.refresh_from_db()
        self.assertEqual(task.song_id, self.deactivated_song.pk)


class MyTasksRendersTheSongTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.category = SongCategory.objects.create(name='Hymns', color='blue')
        self.song = make_song(
            title='The Chapter Hymn', lyrics='Verse one\nVerse two', category=self.category,
        )

    def _get(self):
        client = Client()
        client.force_login(self.pledge)
        response = client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_lyrics_are_shown_for_a_linked_song(self):
        PledgeTask.objects.create(title='Learn it', task_type='song', song=self.song)
        html = self._get()
        self.assertIn('The Chapter Hymn', html)
        self.assertIn('Verse one', html)
        self.assertIn('Verse two', html)
        self.assertIn('Hymns', html)

    def test_no_audio_player_when_the_song_has_no_audio_file(self):
        PledgeTask.objects.create(title='Learn it', task_type='song', song=self.song)
        html = self._get()
        self.assertNotIn('<audio', html)

    def test_a_song_task_with_no_song_linked_shows_a_placeholder_not_nothing(self):
        PledgeTask.objects.create(title='Learn it', task_type='song', song=None)
        html = self._get()
        self.assertIn('No song linked', html)

    def test_a_non_song_task_never_renders_a_lyrics_block(self):
        PledgeTask.objects.create(title='Do a normal thing', task_type='task')
        html = self._get()
        self.assertNotIn('Verse one', html)

    def test_the_link_out_to_the_full_songbook_entry_is_present(self):
        PledgeTask.objects.create(title='Learn it', task_type='song', song=self.song)
        html = self._get()
        self.assertIn(reverse('song_detail', args=[self.song.pk]), html)

    def test_completing_a_song_task_shows_learned(self):
        task = PledgeTask.objects.create(title='Learn it', task_type='song', song=self.song)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        html = self._get()
        self.assertIn('Learned', html)

    def test_a_pledge_the_song_task_is_not_assigned_to_does_not_see_it(self):
        task = PledgeTask.objects.create(title='Just for the other pledge', task_type='song', song=self.song)
        task.assigned_to.add(self.other_pledge)
        html = self._get()
        self.assertNotIn('Just for the other pledge', html)
