"""
The app's Songbook and Quote Book (mobile plan, Phase 3j).

Songbook (``songbook_list`` / ``song_detail`` have no page toggle, only the
pledge restriction):

* ``GET songbook/`` — every active song (title, category, whether it has
  audio), the categories in tab order, and which of the two songbook PDFs are
  on disk. The whole list at once: a chapter songbook is a few dozen songs, so
  the app searches and filters on the phone, as it does the directory.
* ``GET songbook/songs/<id>/`` — one song's lyrics.
* ``GET songbook/songs/<id>/audio/`` — its recording, for the app's player
  (``serve_song_audio``'s file, with the sign-in token instead of a session).
* ``GET songbook/pdf/<sheet_music|songbook>/`` — the PDFs, likewise.

Adding and editing songs (an audio upload) stays on the website; ``can_manage``
tells the app whether to link there.

Quote Book (``PageToggle('quote_book')``):

* ``GET quote-book/`` — the book: one chapter per member, as ``quote_book``
  builds it, with what this member may do to each quote, and how many flagged
  quotes they can review.
* ``POST quote-book/`` ``{quoted_member, text, context}`` — add a quote.
* ``POST quote-book/<id>/flag/`` — flag a quote (or delete it, when it's your
  own quote about yourself), as ``flag_quote``.
* ``GET quote-book/flagged/`` — the flagged quotes this member has standing
  over (``review_flagged_quotes``); ``POST quote-book/<id>/restore/`` restores one.
* ``GET quote-book/members/`` — who a quote can be about (everyone not
  removed; alumni keep their chapter), for the add form.
"""
import mimetypes
import os

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import ActivityLog, ParliamentUser, Quote, Song, SongCategory
from src.models.users import MEMBER_ACCOUNT_FIELDS, MEMBER_PROFILE_FIELDS, member_defer
from src.utils.content_disposition import apply_disposition
from src.view.songbook import SHEET_MUSIC_PDF, SONGBOOK_PDF, can_manage_songs, song_audio_path

#: The quote form's limits: `context` is the model's max_length; the text has
#: none in the model, so the app's cap is generous.
QUOTE_TEXT_MAX = 2000
QUOTE_CONTEXT_MAX = 300

PDFS = {'sheet_music': SHEET_MUSIC_PDF, 'songbook': SONGBOOK_PDF}


def _pdf_path(name):
    path = os.path.join(settings.BASE_DIR, 'exportable_media', name)
    return path if os.path.exists(path) else None


def _file_response(path, default_type):
    name = os.path.basename(path)
    content_type = mimetypes.guess_type(name)[0] or default_type
    response = FileResponse(open(path, 'rb'), content_type=content_type)
    apply_disposition(response, content_type, name)
    response['Cache-Control'] = 'private, no-store'
    return response


def _category_json(c):
    return {'id': c.id, 'name': c.name, 'color': c.color} if c else None


# ── Songbook ─────────────────────────────────────────────────────────────────

class SongbookView(MobileAPIView):
    web_page = 'songbook'
    web_page_toggle = False

    def get(self, request):
        songs = Song.objects.filter(is_active=True).select_related('category').order_by('title')
        categories, _ = SongCategory.get_tabs_data()
        return Response({
            'songs': [{
                'id': s.id,
                'title': s.title,
                'category_id': s.category_id,
                'has_audio': bool(s.audio_file),
                # v4.4.0: what the app's "Play all" needs to fetch each recording
                # (the same as the song's own page gives).
                'audio_ext': (os.path.splitext(s.audio_file.name)[1].lower() or '.mp3') if s.audio_file else '',
                'updated_at': s.updated_at.isoformat(),
                # The first line, for the list (and so a search can match lyrics).
                'first_line': next((ln.strip() for ln in s.lyrics.splitlines() if ln.strip()), ''),
                'lyrics': s.lyrics,
            } for s in songs],
            'categories': [_category_json(c) for c in categories],
            'pdfs': [{'key': k, 'title': 'Sheet music' if k == 'sheet_music' else 'The 2005 songbook'}
                     for k, name in PDFS.items() if _pdf_path(name)],
            'can_manage': can_manage_songs(request.user),
            'web': {'songbook': reverse('songbook'), 'add': reverse('song_create')},
        })


class SongView(MobileAPIView):
    web_page = 'songbook'
    web_page_toggle = False

    def get(self, request, song_id):
        s = get_object_or_404(Song.objects.select_related('category', 'created_by')
                              .defer(*member_defer('created_by')), pk=song_id, is_active=True)
        return Response({
            'id': s.id,
            'title': s.title,
            'lyrics': s.lyrics,
            'category': _category_json(s.category),
            'has_audio': bool(s.audio_file),
            # The recording's file type (".mp3", ".m4a"): the app keeps a copy
            # by that name, and iOS's player goes by the extension.
            'audio_ext': (os.path.splitext(s.audio_file.name)[1].lower() or '.mp3') if s.audio_file else '',
            'added_by': s.created_by.get_display_name() if s.created_by else '',
            'added_at': s.created_at.isoformat(),
            'updated_at': s.updated_at.isoformat(),
            'can_manage': can_manage_songs(request.user),
            'web_edit': reverse('song_edit', args=[s.pk]),
        })


class SongAudioView(MobileAPIView):
    web_page = 'songbook'
    web_page_toggle = False

    def get(self, request, song_id):
        song = get_object_or_404(Song, pk=song_id, is_active=True)
        path = song_audio_path(song)
        if path is None:
            raise exceptions.NotFound()
        return _file_response(path, 'audio/mpeg')


class SongbookPdfView(MobileAPIView):
    web_page = 'songbook'
    web_page_toggle = False

    def get(self, request, key):
        path = _pdf_path(PDFS[key]) if key in PDFS else None
        if path is None:
            raise exceptions.NotFound()
        return _file_response(path, 'application/pdf')


# ── Quote Book ───────────────────────────────────────────────────────────────

def _flagged_scope(user):
    """The flagged quotes ``user`` has standing over (review_flagged_quotes' scoping)."""
    flagged = Quote.objects.filter(flagged_at__isnull=False)
    if not user.is_officer:
        flagged = flagged.filter(Q(flagged_by=user) | Q(quoted_member=user))
    return flagged


def _quote_json(q, user):
    return {
        'id': q.pk,
        'text': q.text,
        'context': q.context,
        'submitted_by': q.submitted_by.get_display_name() if q.submitted_by else 'Unknown',
        'created_at': q.created_at.isoformat(),
        'can_flag': q.can_be_flagged_by(user),
        'delete_not_flag': q.can_be_deleted_outright_by(user),
    }


class QuoteBookView(MobileAPIView):
    web_page = 'quote_book'

    def get(self, request):
        user = request.user
        quotes = (Quote.objects.visible()
                  .select_related('quoted_member', 'submitted_by')
                  .defer(*member_defer('quoted_member', 'submitted_by'))
                  .order_by('quoted_member__name', 'created_at'))
        chapters, by_member = [], {}
        for q in quotes:
            m = q.quoted_member
            chapter = by_member.get(m.pk)
            if chapter is None:
                chapter = by_member[m.pk] = {'user_id': m.pk, 'name': m.get_display_name(), 'quotes': []}
                chapters.append(chapter)
            chapter['quotes'].append(_quote_json(q, user))
        return Response({'chapters': chapters, 'flagged_count': _flagged_scope(user).count()})

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        text = str(body.get('text') or '').strip()
        context = str(body.get('context') or '').strip()
        member_id = str(body.get('quoted_member') or '').strip()
        if not member_id or not text:
            raise BadRequest('Choose who said it and enter the quote.', code='invalid')
        if len(text) > QUOTE_TEXT_MAX or len(context) > QUOTE_CONTEXT_MAX:
            raise BadRequest('That quote is too long.', code='invalid')
        member = ParliamentUser.objects.filter(pk=member_id).exclude(member_status='Removed').first()
        if member is None:
            raise BadRequest('Choose who said it.', code='invalid')
        quote = Quote.objects.create(quoted_member=member, text=text, context=context, submitted_by=request.user)
        # Structural details only, never the text (submit_quote's rule).
        ActivityLog.log_activity(
            action_type='quote_submitted', user=request.user,
            description=f'{request.user.get_display_name()} added a quote (#{quote.pk}) to '
                        f'{member.get_display_name()}\'s chapter',
            request=request, object_type='Quote', object_id=quote.pk)
        return Response({'quote': _quote_json(quote, request.user)}, status=201)


class QuoteMembersView(MobileAPIView):
    web_page = 'quote_book'

    def get(self, request):
        members = (ParliamentUser.objects.exclude(member_status='Removed').order_by('name')
                   .defer(*(MEMBER_PROFILE_FIELDS + MEMBER_ACCOUNT_FIELDS)))
        return Response({'members': [
            {'user_id': m.pk, 'name': m.get_display_name(), 'alumni': m.member_status == 'Alumni'}
            for m in members]})


class QuoteFlagView(MobileAPIView):
    web_page = 'quote_book'

    def post(self, request, quote_id):
        quote = get_object_or_404(Quote.objects.select_related('quoted_member'), pk=quote_id)
        user = request.user
        name = quote.quoted_member.get_display_name()
        if quote.can_be_deleted_outright_by(user):
            pk = quote.pk
            quote.delete()
            ActivityLog.log_activity(
                action_type='quote_deleted', user=user,
                description=f'{user.get_display_name()} deleted their own quote (#{pk}) from {name}\'s chapter',
                request=request, object_type='Quote', object_id=pk)
            return Response({'ok': True, 'deleted': True})
        if not quote.can_be_flagged_by(user):
            raise exceptions.PermissionDenied('You can’t flag this quote.', code='not_permitted')
        if quote.flagged_at is None:
            quote.flagged_at, quote.flagged_by = timezone.now(), user
            quote.save(update_fields=['flagged_at', 'flagged_by'])
            ActivityLog.log_activity(
                action_type='quote_flagged', user=user,
                description=f'{user.get_display_name()} flagged a quote (#{quote.pk}) on {name}\'s chapter',
                request=request, object_type='Quote', object_id=quote.pk)
        return Response({'ok': True, 'deleted': False})


class QuoteFlaggedView(MobileAPIView):
    web_page = 'quote_book'

    def get(self, request):
        flagged = (_flagged_scope(request.user)
                   .select_related('quoted_member', 'submitted_by', 'flagged_by')
                   .defer(*member_defer('quoted_member', 'submitted_by', 'flagged_by'))
                   .order_by('-flagged_at'))
        return Response({
            'viewing_as_officer': bool(request.user.is_officer),
            'quotes': [{
                **_quote_json(q, request.user),
                'quoted_member': q.quoted_member.get_display_name(),
                'flagged_by': q.flagged_by.get_display_name() if q.flagged_by else '',
                'flagged_at': q.flagged_at.isoformat(),
                'can_restore': q.can_be_restored_by(request.user),
            } for q in flagged],
        })


class QuoteRestoreView(MobileAPIView):
    web_page = 'quote_book'

    def post(self, request, quote_id):
        quote = get_object_or_404(Quote.objects.select_related('quoted_member'), pk=quote_id)
        if not quote.can_be_restored_by(request.user):
            raise exceptions.PermissionDenied('You can’t restore this quote.', code='not_permitted')
        if quote.flagged_at is not None:
            quote.flagged_at = quote.flagged_by = None
            quote.save(update_fields=['flagged_at', 'flagged_by'])
            ActivityLog.log_activity(
                action_type='quote_restored', user=request.user,
                description=f'{request.user.get_display_name()} restored a flagged quote (#{quote.pk}) on '
                            f'{quote.quoted_member.get_display_name()}\'s chapter',
                request=request, object_type='Quote', object_id=quote.pk)
        return Response({'ok': True})
