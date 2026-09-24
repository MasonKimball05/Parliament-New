"""
Sticky notes on a resolution (09-24-26).

Mason: "a way for people who are working on a resolution to make notes for
themself off to the side of the resolution without that being added to the
resolution ... it says who put what note (or edited a note) like a sticky note
... where there can be multiple to work through ... like Word's comments but
not tied to a part of the page."

Plain form POSTs that redirect back to the page they came from — no JS, so
nothing here depends on CSP nonces, and the panel works the same on the
resolution page and the edit page. See `ResolutionNote` for who may do what.

Anyone outside the working group gets a 404, not a 403: a member reading a
resolution should not be able to tell from a response that notes exist.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from src.models import Resolution, ResolutionNote

_COLORS = {c for c, _ in ResolutionNote.COLOR_CHOICES}


def _resolution_for_notes(request, resolution_id):
    resolution = get_object_or_404(Resolution, pk=resolution_id)
    if not ResolutionNote.user_can_use_notes(request.user, resolution):
        raise Http404
    return resolution


def _back(request, resolution):
    """Return to the page the note form was on (detail or edit), else detail."""
    nxt = request.POST.get('next', '')
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return redirect(nxt.split('#')[0] + '#notes')
    return redirect(reverse('cnb_resolution_detail', args=[resolution.pk]) + '#notes')


def _clean_body(request):
    body = (request.POST.get('body') or '').strip()
    if not body:
        messages.error(request, 'A note cannot be empty.')
        return None
    if len(body) > ResolutionNote.MAX_LENGTH:
        messages.error(request, f'Notes are limited to {ResolutionNote.MAX_LENGTH} characters.')
        return None
    return body


def _clean_color(request, default='yellow'):
    color = request.POST.get('color', default)
    return color if color in _COLORS else default


@login_required
@require_POST
def add_resolution_note(request, resolution_id):
    resolution = _resolution_for_notes(request, resolution_id)
    body = _clean_body(request)
    if body is not None:
        ResolutionNote.objects.create(
            resolution=resolution, body=body, color=_clean_color(request),
            created_by=request.user,
        )
    return _back(request, resolution)


@login_required
@require_POST
def edit_resolution_note(request, resolution_id, note_id):
    resolution = _resolution_for_notes(request, resolution_id)
    note = get_object_or_404(ResolutionNote, pk=note_id, resolution=resolution)
    body = _clean_body(request)
    if body is not None:
        color = _clean_color(request, default=note.color)
        if body != note.body or color != note.color:
            note.body, note.color = body, color
            note.edited_by, note.edited_at = request.user, timezone.now()
            note.save(update_fields=['body', 'color', 'edited_by', 'edited_at'])
    return _back(request, resolution)


@login_required
@require_POST
def toggle_resolution_note_done(request, resolution_id, note_id):
    resolution = _resolution_for_notes(request, resolution_id)
    note = get_object_or_404(ResolutionNote, pk=note_id, resolution=resolution)
    note.is_done = not note.is_done
    if note.is_done:
        note.done_by, note.done_at = request.user, timezone.now()
    else:
        note.done_by, note.done_at = None, None
    note.save(update_fields=['is_done', 'done_by', 'done_at'])
    return _back(request, resolution)


@login_required
@require_POST
def delete_resolution_note(request, resolution_id, note_id):
    resolution = _resolution_for_notes(request, resolution_id)
    note = get_object_or_404(ResolutionNote, pk=note_id, resolution=resolution)
    if not note.user_can_delete(request.user):
        messages.error(request, 'Only the person who wrote a note (or a C&B chair) can delete it. You can mark it done instead.')
    else:
        note.delete()
    return _back(request, resolution)
