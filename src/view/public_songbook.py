"""
Public-facing songbook — read-only, no authentication required.
Intentionally separate from src/view/songbook.py to guarantee zero
management functions are reachable from this surface.
"""
from django.shortcuts import render, get_object_or_404
from src.models import Song, SongCategory


def public_songbook_list(request):
    """Public song listing — search and category filter, no auth."""
    songs = Song.objects.filter(is_active=True).select_related('category')
    categories = SongCategory.objects.all()

    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()

    if query:
        songs = songs.filter(title__icontains=query) | Song.objects.filter(
            is_active=True, lyrics__icontains=query
        )
        songs = songs.select_related('category').distinct()

    active_category = None
    if category_id:
        try:
            active_category = SongCategory.objects.get(pk=int(category_id))
            songs = songs.filter(category=active_category)
        except (SongCategory.DoesNotExist, ValueError):
            pass

    songs = songs.order_by('title')

    # Attach song count to each category for the filter bar
    for cat in categories:
        cat.public_count = Song.objects.filter(is_active=True, category=cat).count()

    return render(request, 'public_songbook.html', {
        'songs': songs,
        'categories': categories,
        'query': query,
        'active_category': active_category,
    })


def public_song_detail(request, pk):
    """Public song detail — lyrics only, no auth."""
    song = get_object_or_404(Song, pk=pk, is_active=True)
    return render(request, 'public_songbook_detail.html', {'song': song})
