"""
Cache invalidation helpers for user-scoped caches.

Call these whenever code modifies a user's data server-side (e.g., admin edits)
so the per-user caches don't serve stale data for up to their TTL.
"""
from django.core.cache import cache


def invalidate_user_session_caches(user_pk):
    """
    Bust all per-user caches that are populated by context processors.

    Keys invalidated:
      - user_prefs_{pk}   — UserPreferences object (TTL 5 min)
      - 2fa_status_{pk}   — Two-factor auth status dict (TTL 5 min)

    Safe to call speculatively: cache.delete_many is a no-op for missing keys.
    """
    cache.delete_many([
        f'user_prefs_{user_pk}',
        f'2fa_status_{user_pk}',
    ])
