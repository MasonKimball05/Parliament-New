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
_ANCHORS = {a for a, _ in ResolutionNote.ANCHOR_CHOICES}


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


def clean_body(raw):
    """(body, error): the note's text, or why it can't be saved."""
    body = (raw or '').strip()
    if not body:
        return None, 'A note cannot be empty.'
    if len(body) > ResolutionNote.MAX_LENGTH:
        return None, f'Notes are limited to {ResolutionNote.MAX_LENGTH} characters.'
    return body, None


def clean_anchor(raw, default=''):
    """'' (not pinned) or one of ANCHOR_CHOICES; anything else (or None) keeps `default`."""
    if raw is None:
        return default
    return raw if (raw == '' or raw in _ANCHORS) else default


def clean_color(raw, default='yellow'):
    return raw if raw in _COLORS else default


# ── Shared with the app (src/mobile/views_cnb_manage.py) ─────────────────────

def create_note(resolution, user, body, color, anchor):
    return ResolutionNote.objects.create(
        resolution=resolution, body=body, color=color, anchor=anchor, created_by=user,
    )


def update_note(note, user, body, color, anchor):
    """Save a changed note, recording who edited it; an unchanged one is left alone."""
    if (body, color, anchor) != (note.body, note.color, note.anchor):
        note.body, note.color, note.anchor = body, color, anchor
        note.edited_by, note.edited_at = user, timezone.now()
        note.save(update_fields=['body', 'color', 'anchor', 'edited_by', 'edited_at'])


def toggle_note_done(note, user):
    note.is_done = not note.is_done
    if note.is_done:
        note.done_by, note.done_at = user, timezone.now()
    else:
        note.done_by, note.done_at = None, None
    note.save(update_fields=['is_done', 'done_by', 'done_at'])


DELETE_REFUSED = ('Only the person who wrote a note (or a C&B chair) can delete it. '
                  'You can mark it done instead.')


def delete_note(note, user):
    """Delete ``note`` if ``user`` may; returns the refusal message otherwise."""
    if not note.user_can_delete(user):
        return DELETE_REFUSED
    note.delete()
    return None


def _post_anchor(request, default=''):
    return clean_anchor(request.POST['anchor'] if 'anchor' in request.POST else None, default)


@login_required
@require_POST
def add_resolution_note(request, resolution_id):
    resolution = _resolution_for_notes(request, resolution_id)
    body, error = clean_body(request.POST.get('body'))
    if error:
        messages.error(request, error)
    else:
        create_note(resolution, request.user, body, clean_color(request.POST.get('color', 'yellow')),
                    _post_anchor(request))
    return _back(request, resolution)


@login_required
@require_POST
def edit_resolution_note(request, resolution_id, note_id):
    resolution = _resolution_for_notes(request, resolution_id)
    note = get_object_or_404(ResolutionNote, pk=note_id, resolution=resolution)
    body, error = clean_body(request.POST.get('body'))
    if error:
        messages.error(request, error)
    else:
        update_note(note, request.user, body,
                    clean_color(request.POST.get('color', note.color), default=note.color),
                    _post_anchor(request, default=note.anchor))
    return _back(request, resolution)


@login_required
@require_POST
def toggle_resolution_note_done(request, resolution_id, note_id):
    resolution = _resolution_for_notes(request, resolution_id)
    note = get_object_or_404(ResolutionNote, pk=note_id, resolution=resolution)
    toggle_note_done(note, request.user)
    return _back(request, resolution)


@login_required
@require_POST
def delete_resolution_note(request, resolution_id, note_id):
    resolution = _resolution_for_notes(request, resolution_id)
    note = get_object_or_404(ResolutionNote, pk=note_id, resolution=resolution)
    error = delete_note(note, request.user)
    if error:
        messages.error(request, error)
    return _back(request, resolution)
