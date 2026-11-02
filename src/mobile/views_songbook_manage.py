"""
Adding and editing songs, and the song categories page, in the app (v4.x,
officer tools batch 5), through the Songbook's own helpers
(src/view/songbook.py) and forms (``SongForm``, ``SongCategoryForm``):

* ``POST songbook/songs/`` ``{title, category?, lyrics}`` — ``song_create``.
* ``GET songbook/songs/<id>/edit/`` — the song as the edit form shows it;
  ``POST`` the same fields — ``song_edit``. A recording already on the song
  stays when none is sent.
* ``POST songbook/songs/<id>/delete/`` — ``song_delete`` (hides the song).
* ``GET songbook/categories/`` — ``manage_song_categories``: the categories with
  their song counts, the choristers and who could become one;
  ``POST`` ``{action: add|delete|add_chorister|remove_chorister, ...}`` the
  page's four forms.

Songs: ``can_manage_songs`` (officers, chairs, admins and Choristers), no page
toggle. Categories: admins only. All also multipart, so an ``audio_file`` the
form accepts is accepted here; the app has no audio picker, so it links to the
website's song form to add or replace a recording.
"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.forms import SongCategoryForm, SongForm
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data, _form_errors
from src.models import Song
from src.view.songbook import (
    apply_category_action, can_manage_songs, create_song, remove_song, song_categories_context, update_song,
)

#: SongForm.clean_audio_file's rules, for the app to describe.
AUDIO = {'extensions': ['.mp3', '.wav', '.m4a', '.ogg', '.flac'], 'max_bytes': 50 * 1024 * 1024}


class _SongManagers(MobileAPIView):
    web_page_toggle = False   # song_create / song_edit have no @require_page_enabled
    parser_classes = [JSONParser, MultiPartParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not can_manage_songs(request.user):
            raise exceptions.PermissionDenied("You don't have permission to edit songs.", code='forbidden')


def _song_json(song):
    return {
        'id': song.id,
        'title': song.title,
        'category_id': song.category_id,
        'lyrics': song.lyrics,
        'has_audio': bool(song.audio_file),
        'is_active': song.is_active,
    }


def _song_form(request, instance=None):
    form = SongForm(_form_data(request), request.FILES, instance=instance)
    if not form.is_valid():
        raise BadRequest(_form_errors(form), code='invalid_input')
    return form


class SongCreateView(_SongManagers):
    web_page = 'song_create'

    def post(self, request):
        song = create_song(request.user, _song_form(request))
        return Response({'song': _song_json(song), 'message': f'Song "{song.title}" has been added to the songbook.'},
                        status=201)


class SongEditView(_SongManagers):
    web_page = 'song_edit'

    def get(self, request, song_id):
        song = get_object_or_404(Song, pk=song_id)
        return Response({
            'song': _song_json(song),
            'audio': AUDIO,
            'web_edit': reverse('song_edit', args=[song.pk]),
        })

    def post(self, request, song_id):
        song = get_object_or_404(Song, pk=song_id)
        song = update_song(request.user, _song_form(request, song))
        return Response({'song': _song_json(song), 'message': f'Song "{song.title}" has been updated.'})


class SongDeleteView(_SongManagers):
    web_page = 'song_delete'

    def post(self, request, song_id):
        title = remove_song(request.user, get_object_or_404(Song, pk=song_id))
        return Response({'ok': True, 'message': f'Song "{title}" has been removed from the songbook.'})


class SongCategoriesView(MobileAPIView):
    web_page = 'manage_song_categories'
    web_page_toggle = False
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_admin:
            raise exceptions.PermissionDenied('Only administrators can manage categories.', code='forbidden')

    def get(self, request):
        c = song_categories_context()
        return Response({
            'categories': [{
                'id': cat.id,
                'name': cat.name,
                'color': cat.color,
                'description': cat.description,
                'display_order': cat.display_order,
                'song_total': cat.song_total,
            } for cat in c['categories']],
            # The add form's colour choices (SongCategoryForm's select).
            'colors': [{'value': v, 'label': label} for v, label in SongCategoryForm().fields['color'].widget.choices],
            'choristers': [{'user_id': m.user_id, 'name': m.get_display_name()} for m in c['choristers']],
            'eligible_members': [{'user_id': m.user_id, 'name': m.get_display_name()}
                                 for m in c['eligible_members']],
        })

    def post(self, request):
        ok, message, form = apply_category_action(request.user, _form_data(request))
        if not ok:
            if form is not None:
                raise BadRequest(_form_errors(form), code='invalid_input')
            raise BadRequest(message or 'Unknown action.', code='invalid_input')
        return Response({'ok': True, 'message': message})
