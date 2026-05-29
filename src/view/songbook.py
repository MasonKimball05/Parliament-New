from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden, FileResponse, Http404
from django.views.decorators.http import require_http_methods
from django.conf import settings
from ..decorators import log_function_call
from ..models import Song, SongCategory, Role, ParliamentUser
from ..forms import SongForm, SongCategoryForm
import os
import logging
import mimetypes

logger = logging.getLogger('function_calls')


def can_manage_songs(user):
    """Check if user can add/edit/delete songs"""
    if user.is_admin or user.member_type in ['Officer', 'Chair']:
        return True
    # Check if user has the Chorister role
    return user.roles.filter(code='Chorister').exists()


@login_required
@log_function_call
def songbook_list(request):
    """Display all songs with search and category filtering"""
    search_query = request.GET.get('q', '').strip()
    category_filter = request.GET.get('category', '')

    # Base queryset - active songs only
    songs = Song.objects.filter(is_active=True).select_related('category', 'created_by')

    # Apply category filter
    if category_filter:
        songs = songs.filter(category_id=category_filter)

    # Apply search filter
    if search_query:
        songs = songs.filter(
            Q(title__icontains=search_query) |
            Q(lyrics__icontains=search_query)
        )

    # Order by title
    songs = songs.order_by('title')

    # Get all categories for filter tabs with song counts
    categories = list(SongCategory.objects.all().order_by('display_order', 'name'))
    for cat in categories:
        cat.song_count = cat.songs.filter(is_active=True).count()

    # Category counts
    category_counts = {
        'all': Song.objects.filter(is_active=True).count(),
    }

    # Paginate
    paginator = Paginator(songs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Check for sheet music PDF
    sheet_music_url = None
    sheet_music_path = os.path.join(settings.BASE_DIR, 'exportable_media', 'BTP Sheet Music.pdf')
    if os.path.exists(sheet_music_path):
        sheet_music_url = '/exportable_media/BTP Sheet Music.pdf'

    # Check for songbook PDF
    songbook_url = None
    songbook_path = os.path.join(settings.BASE_DIR, 'exportable_media', 'Beta Theta Pi Song Book Revised 2005.pdf')
    if os.path.exists(songbook_path):
        songbook_url = '/exportable_media/Beta Theta Pi Song Book Revised 2005.pdf'

    return render(request, 'songbook.html', {
        'songs': page_obj,
        'categories': categories,
        'search_query': search_query,
        'category_filter': category_filter,
        'category_counts': category_counts,
        'can_manage': can_manage_songs(request.user),
        'sheet_music_url': sheet_music_url,
        'songbook_url': songbook_url,
    })


@login_required
@log_function_call
def song_detail(request, pk):
    """Display a single song with full lyrics and audio player"""
    song = get_object_or_404(Song, pk=pk, is_active=True)

    return render(request, 'songbook_detail.html', {
        'song': song,
        'can_manage': can_manage_songs(request.user),
    })


@login_required
@log_function_call
@require_http_methods(["GET", "POST"])
def song_create(request):
    """Create a new song - officers/admins only"""
    if not can_manage_songs(request.user):
        return HttpResponseForbidden("You don't have permission to add songs.")

    if request.method == 'POST':
        form = SongForm(request.POST, request.FILES)
        if form.is_valid():
            song = form.save(commit=False)
            song.created_by = request.user
            song.save()

            logger.info(f"{request.user.username} added song: {song.title}")
            messages.success(request, f'Song "{song.title}" has been added to the songbook.')
            return redirect('song_detail', pk=song.pk)
    else:
        form = SongForm()

    return render(request, 'songbook_form.html', {
        'form': form,
        'action': 'Add',
        'categories': SongCategory.objects.all(),
    })


@login_required
@log_function_call
@require_http_methods(["GET", "POST"])
def song_edit(request, pk):
    """Edit an existing song - officers/admins only"""
    if not can_manage_songs(request.user):
        return HttpResponseForbidden("You don't have permission to edit songs.")

    song = get_object_or_404(Song, pk=pk)

    if request.method == 'POST':
        form = SongForm(request.POST, request.FILES, instance=song)
        if form.is_valid():
            form.save()

            logger.info(f"{request.user.username} edited song: {song.title}")
            messages.success(request, f'Song "{song.title}" has been updated.')
            return redirect('song_detail', pk=song.pk)
    else:
        form = SongForm(instance=song)

    return render(request, 'songbook_form.html', {
        'form': form,
        'song': song,
        'action': 'Edit',
        'categories': SongCategory.objects.all(),
    })


@login_required
@log_function_call
@require_http_methods(["POST"])
def song_delete(request, pk):
    """Delete a song (soft delete) - officers/admins only"""
    if not can_manage_songs(request.user):
        return HttpResponseForbidden("You don't have permission to delete songs.")

    song = get_object_or_404(Song, pk=pk)
    title = song.title

    # Soft delete
    song.is_active = False
    song.save()

    logger.info(f"{request.user.username} deleted song: {title}")
    messages.success(request, f'Song "{title}" has been removed from the songbook.')
    return redirect('songbook')


@login_required
@log_function_call
@require_http_methods(["GET", "POST"])
def manage_categories(request):
    """Manage song categories and Chorister role - admins only"""
    if not request.user.is_admin:
        return HttpResponseForbidden("Only administrators can manage categories.")

    categories = SongCategory.objects.all()

    # Get or create the Chorister role
    chorister_role, _ = Role.objects.get_or_create(
        code='CHOIR',
        defaults={
            'name': 'Chorister',
            'description': 'Can manage songs and lyrics in the Songbook',
            'one_per_chapter': False,
            'grants_admin': False,
        }
    )

    # Get current choristers
    choristers = ParliamentUser.objects.filter(roles=chorister_role, is_active=True).order_by('name')

    # Get eligible members (active, not already a chorister)
    eligible_members = ParliamentUser.objects.filter(
        is_active=True
    ).exclude(
        roles=chorister_role
    ).order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            form = SongCategoryForm(request.POST)
            if form.is_valid():
                category = form.save()
                logger.info(f"{request.user.username} added song category: {category.name}")
                messages.success(request, f'Category "{category.name}" has been added.')
            else:
                messages.error(request, 'Please correct the errors below.')
                return render(request, 'songbook_categories.html', {
                    'categories': categories,
                    'form': form,
                    'choristers': choristers,
                    'eligible_members': eligible_members,
                })

        elif action == 'delete':
            category_id = request.POST.get('category_id')
            try:
                category = SongCategory.objects.get(id=category_id)
                name = category.name
                # Check if category has songs
                song_count = category.songs.filter(is_active=True).count()
                if song_count > 0:
                    messages.error(request, f'Cannot delete "{name}" - it has {song_count} song(s). Reassign them first.')
                else:
                    category.delete()
                    logger.info(f"{request.user.username} deleted song category: {name}")
                    messages.success(request, f'Category "{name}" has been deleted.')
            except SongCategory.DoesNotExist:
                messages.error(request, 'Category not found.')

        elif action == 'add_chorister':
            user_id = request.POST.get('user_id')
            try:
                user = ParliamentUser.objects.get(user_id=user_id)
                user.roles.add(chorister_role)
                logger.info(f"{request.user.username} assigned Chorister role to {user.name}")
                messages.success(request, f'{user.name} has been assigned the Chorister role.')
            except ParliamentUser.DoesNotExist:
                messages.error(request, 'User not found.')

        elif action == 'remove_chorister':
            user_id = request.POST.get('user_id')
            try:
                user = ParliamentUser.objects.get(user_id=user_id)
                user.roles.remove(chorister_role)
                logger.info(f"{request.user.username} removed Chorister role from {user.name}")
                messages.success(request, f'{user.name} is no longer a Chorister.')
            except ParliamentUser.DoesNotExist:
                messages.error(request, 'User not found.')

        return redirect('manage_song_categories')

    return render(request, 'songbook_categories.html', {
        'categories': categories,
        'form': SongCategoryForm(),
        'choristers': choristers,
        'eligible_members': eligible_members,
    })


@login_required
def serve_song_audio(request, pk):
    """Serve audio file for a song, checking both media and exportable_media locations"""
    song = get_object_or_404(Song, pk=pk, is_active=True)

    if not song.audio_file:
        raise Http404("No audio file for this song")

    # Get the relative path stored in the database
    relative_path = song.audio_file.name

    # Check media folder first
    media_path = os.path.join(settings.MEDIA_ROOT, relative_path)
    if os.path.exists(media_path):
        file_path = media_path
    else:
        # Check exportable_media folder
        exportable_path = os.path.join(settings.BASE_DIR, 'exportable_media', relative_path)
        if os.path.exists(exportable_path):
            file_path = exportable_path
        else:
            raise Http404("Audio file not found")

    # Determine content type
    content_type, _ = mimetypes.guess_type(file_path)
    if content_type is None:
        content_type = 'audio/mpeg'  # Default to MP3

    # Serve the file
    response = FileResponse(open(file_path, 'rb'), content_type=content_type)
    response['Content-Disposition'] = f'inline; filename="{os.path.basename(file_path)}"'
    return response


def serve_exportable_media(request, filename):
    """Serve files from exportable_media folder"""
    # Build the full path
    file_path = os.path.join(settings.BASE_DIR, 'exportable_media', filename)

    # Resolve the path and ensure it's within exportable_media (prevent directory traversal)
    exportable_root = os.path.realpath(os.path.join(settings.BASE_DIR, 'exportable_media'))
    resolved_path = os.path.realpath(file_path)

    if not resolved_path.startswith(exportable_root):
        raise Http404("File not found")

    if not os.path.exists(resolved_path) or not os.path.isfile(resolved_path):
        raise Http404("File not found")

    # Determine content type
    content_type, _ = mimetypes.guess_type(resolved_path)
    if content_type is None:
        content_type = 'application/octet-stream'

    # Serve the file
    response = FileResponse(open(resolved_path, 'rb'), content_type=content_type)
    response['Content-Disposition'] = f'inline; filename="{os.path.basename(resolved_path)}"'
    return response
