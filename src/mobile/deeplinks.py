"""
Map a site URL (a Notification.link / push URL) to an app screen.

A native push carries ``data = {url, screen, id}``. The app opens ``screen``
natively when it knows it (``id`` being the object it is about) and falls back
to opening ``url`` in its web view for ``screen == 'web'``. Only paths are
looked at: query strings and fragments are dropped for matching, never for
``url`` itself (so ``/calendar/?year=2026&month=11`` is screen ``calendar`` and
the app still gets the month from ``url``). ``screen == 'chat'`` with no ``id``
is the channel list.

Kept deliberately small: a screen is added here when the app grows one.
"""
import re
from urllib.parse import urlsplit

_INT = r'(\d+)'

#: (compiled path regex, screen, how to get the id from the match)
_RULES = [
    (re.compile(r'^/vote/$'), 'votes', None),
    (re.compile(rf'^/legislation/detail/{_INT}/$'), 'vote', 1),
    (re.compile(rf'^/legislation/{_INT}/$'), 'vote', 1),
    (re.compile(rf'^/chat/{_INT}/$'), 'chat', 1),
    (re.compile(r'^/chats/$'), 'chat', None),          # the channel list
    (re.compile(r'^/calendar/$'), 'calendar', None),
    (re.compile(r'^/notifications/$'), 'notifications', None),
    (re.compile(r'^/announcements/$'), 'home', None),
    (re.compile(r'^/home/$'), 'home', None),
    (re.compile(r'^/$'), 'home', None),
]

#: Committee chat URLs name the committee, not the channel.
_COMMITTEE_CHAT = [
    re.compile(r'^/chat/committee/([^/]+)/$'),
    re.compile(r'^/committee/([^/]+)/chat/$'),
]


def _committee_channel_id(code):
    from src.models import ChatChannel
    return (ChatChannel.objects
            .filter(committee__code=code, channel_type='committee')
            .values_list('id', flat=True).first())


def screen_for_url(url):
    """Return ``(screen, id)`` for a site URL; ``('web', None)`` if unmapped."""
    path = urlsplit(url or '').path or '/'
    if not path.endswith('/'):
        path += '/'
    for rx, screen, group in _RULES:
        m = rx.match(path)
        if m:
            return screen, (int(m.group(group)) if group else None)
    for rx in _COMMITTEE_CHAT:
        m = rx.match(path)
        if m:
            try:
                channel_id = _committee_channel_id(m.group(1))
            except Exception:
                channel_id = None
            if channel_id is not None:
                return 'chat', channel_id
            return 'web', None
    return 'web', None


HOME_URL = '/home/'


def safe_app_url(url):
    """
    ``url`` if it is a same-site path, else ``'/home/'``.

    The app opens ``url`` in its web view, which holds the member's session, so
    only a path on this site may get there: it must start with exactly one
    ``/`` (``//host`` is protocol-relative, and browsers treat ``\\`` like
    ``/``), and have no scheme, host or control characters.
    """
    if not isinstance(url, str) or not url.startswith('/') or url.startswith('//'):
        return HOME_URL
    if '\\' in url or any(ord(c) < 0x20 or ord(c) == 0x7f for c in url):
        return HOME_URL
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return HOME_URL
    return url


def push_data_for_url(url):
    """The ``data`` dict of a native push for ``url`` (same-site paths only)."""
    url = safe_app_url(url)
    screen, obj_id = screen_for_url(url)
    return {'url': url, 'screen': screen, 'id': obj_id}
