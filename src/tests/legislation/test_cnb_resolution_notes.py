"""
09-24-26 — sticky notes on a resolution for the people working on it.

Mason: "a way for people who are working on a resolution to make notes ...
without that being added to the resolution ... it says who put what note (or
edited a note) like a sticky note ... where there can be multiple."

Pins: who can see/use them (CNB holders + collaborators, nobody else), that
authorship/edits/done are attributed, that only the author or a CNB holder can
delete, and that notes never reach the resolution text or the print view.

Run with: python manage.py test src.tests.legislation.test_cnb_resolution_notes
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser, Resolution, ResolutionCollaborator, ResolutionNote


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('cnb-notes-test-12345!')
    user.save()
    return user


class ResolutionNotesTests(TestCase):
    def setUp(self):
        self.cnb = make_user('RN-CNB', name='Chair Person', is_admin=True)   # has_cnb_permission
        self.editor = make_user('RN-ED', name='Eddie Editor')
        self.viewer = make_user('RN-VW', name='Vera Viewer')
        self.outsider = make_user('RN-OUT', name='Oscar Outsider')
        self.resolution = Resolution.objects.create(
            title='Board size', resolved_text='the board shrink.', created_by=self.cnb,
        )
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.editor, role='editor')
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.viewer, role='viewer')
        self.detail = reverse('cnb_resolution_detail', args=[self.resolution.pk])

    def _c(self, user):
        c = Client(); c.force_login(user); return c

    def _add(self, user, body='Check §3 wording', color='yellow'):
        return self._c(user).post(
            reverse('cnb_add_resolution_note', args=[self.resolution.pk]),
            {'body': body, 'color': color, 'next': self.detail},
        )

    # ── who ────────────────────────────────────────────────────────────────
    def test_cnb_editor_and_viewer_collaborators_can_add_notes(self):
        for user in (self.cnb, self.editor, self.viewer):
            with self.subTest(user=user.name):
                r = self._add(user, body=f'from {user.name}')
                self.assertEqual(r.status_code, 302)
        self.assertEqual(ResolutionNote.objects.count(), 3)

    def test_an_outsider_gets_404_and_never_sees_the_panel(self):
        self.assertEqual(self._add(self.outsider).status_code, 404)
        self.assertFalse(ResolutionNote.objects.exists())
        ResolutionNote.objects.create(resolution=self.resolution, body='SECRET-NOTE', created_by=self.cnb)
        html = self._c(self.outsider).get(self.detail).content.decode()
        self.assertNotIn('SECRET-NOTE', html)
        self.assertNotIn('Working Notes', html)

    def test_collaborators_see_every_note_with_its_author(self):
        self._add(self.cnb, body='chair note')
        self._add(self.editor, body='editor note')
        html = self._c(self.viewer).get(self.detail).content.decode()
        self.assertIn('chair note', html)
        self.assertIn('editor note', html)
        self.assertIn('Chair Person', html)
        self.assertIn('Eddie Editor', html)

    # ── attribution ────────────────────────────────────────────────────────
    def test_editing_records_who_and_when(self):
        self._add(self.cnb, body='original')
        note = ResolutionNote.objects.get()
        self._c(self.editor).post(
            reverse('cnb_edit_resolution_note', args=[self.resolution.pk, note.pk]),
            {'body': 'reworded', 'color': 'blue', 'next': self.detail},
        )
        note.refresh_from_db()
        self.assertEqual(note.body, 'reworded')
        self.assertEqual(note.color, 'blue')
        self.assertEqual(note.created_by, self.cnb)
        self.assertEqual(note.edited_by, self.editor)
        self.assertIsNotNone(note.edited_at)
        self.assertIn('Edited by Eddie Editor', self._c(self.cnb).get(self.detail).content.decode())

    def test_an_unchanged_save_is_not_recorded_as_an_edit(self):
        self._add(self.cnb, body='same')
        note = ResolutionNote.objects.get()
        self._c(self.editor).post(
            reverse('cnb_edit_resolution_note', args=[self.resolution.pk, note.pk]),
            {'body': 'same', 'color': 'yellow'},
        )
        note.refresh_from_db()
        self.assertIsNone(note.edited_by)

    def test_mark_done_and_reopen_record_who(self):
        self._add(self.editor)
        note = ResolutionNote.objects.get()
        url = reverse('cnb_toggle_resolution_note_done', args=[self.resolution.pk, note.pk])
        self._c(self.viewer).post(url)
        note.refresh_from_db()
        self.assertTrue(note.is_done)
        self.assertEqual(note.done_by, self.viewer)
        self._c(self.viewer).post(url)
        note.refresh_from_db()
        self.assertFalse(note.is_done)
        self.assertIsNone(note.done_by)

    # ── delete ─────────────────────────────────────────────────────────────
    def test_only_the_author_or_a_cnb_holder_can_delete(self):
        self._add(self.editor, body='editor wrote this')
        note = ResolutionNote.objects.get()
        url = reverse('cnb_delete_resolution_note', args=[self.resolution.pk, note.pk])
        self._c(self.viewer).post(url)
        self.assertTrue(ResolutionNote.objects.filter(pk=note.pk).exists())
        self._c(self.cnb).post(url)
        self.assertFalse(ResolutionNote.objects.filter(pk=note.pk).exists())

    def test_the_author_can_delete_their_own(self):
        self._add(self.viewer)
        note = ResolutionNote.objects.get()
        self._c(self.viewer).post(reverse('cnb_delete_resolution_note', args=[self.resolution.pk, note.pk]))
        self.assertFalse(ResolutionNote.objects.exists())

    # ── isolation ──────────────────────────────────────────────────────────
    def test_a_note_id_from_another_resolution_is_404(self):
        other = Resolution.objects.create(title='Other', created_by=self.cnb)
        note = ResolutionNote.objects.create(resolution=other, body='x', created_by=self.cnb)
        r = self._c(self.editor).post(
            reverse('cnb_edit_resolution_note', args=[self.resolution.pk, note.pk]), {'body': 'hijack'},
        )
        self.assertEqual(r.status_code, 404)

    def test_notes_never_reach_the_resolution_or_the_print_view(self):
        self._add(self.cnb, body='DO-NOT-PRINT-THIS')
        self.resolution.refresh_from_db()
        self.assertNotIn('DO-NOT-PRINT-THIS', self.resolution.resolved_text)
        html = self._c(self.cnb).get(reverse('cnb_resolution_print', args=[self.resolution.pk])).content.decode()
        self.assertNotIn('DO-NOT-PRINT-THIS', html)

    # ── validation ─────────────────────────────────────────────────────────
    def test_empty_overlong_and_bad_color_input(self):
        self._add(self.cnb, body='   ')
        self._add(self.cnb, body='x' * (ResolutionNote.MAX_LENGTH + 1))
        self.assertFalse(ResolutionNote.objects.exists())
        self._add(self.cnb, body='ok', color='<script>')
        self.assertEqual(ResolutionNote.objects.get().color, 'yellow')

    def test_an_offsite_next_is_ignored(self):
        r = self._c(self.cnb).post(
            reverse('cnb_add_resolution_note', args=[self.resolution.pk]),
            {'body': 'x', 'next': 'https://evil.example/'},
        )
        self.assertTrue(r['Location'].startswith(self.detail))

    def test_the_edit_page_shows_the_panel_too(self):
        self._add(self.cnb, body='visible on edit page')
        r = self._c(self.cnb).get(reverse('cnb_edit_resolution', args=[self.resolution.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('visible on edit page', r.content.decode())
