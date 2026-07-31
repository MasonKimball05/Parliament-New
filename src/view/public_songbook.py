"""
Public-facing songbook — read-only, no authentication required.
Intentionally separate from src/view/songbook.py to guarantee zero
management functions are reachable from this surface.
"""
from django.db.models import Count, Q
from django.shortcuts import render, get_object_or_404
from src.models import Song, SongCategory


def public_songbook_list(request):
    """Public song listing — search and category filter, no auth."""
    songs = Song.objects.filter(is_active=True).select_related('category')
    # v3.17.5: `public_count` was a `.count()` inside the Python loop below —
    # one query per category, on a page that is **public and unauthenticated**,
    # i.e. the cheapest page on the site to hit repeatedly. One filtered
    # aggregate now rides along with the categories fetch.
    #
    # Found by the widened `test_url_smoke` fixtures, not by hand: this page
    # rendered with zero categories before v3.17.5 seeded them, so the per-row
    # query fired zero times and the N+1 detector saw a clean page.
    categories = list(
        SongCategory.objects.annotate(
            public_count=Count('songs', filter=Q(songs__is_active=True)),
        )
    )

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
