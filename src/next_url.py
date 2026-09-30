"""
One place to decide whether a `?next=` value is safe to redirect to (v3.37.0).

Every sign-in step has to carry `next` forward, or a deep link (an email's
"confirm your new address" link, a shared event link) dead-ends on the home
page. The password login already honoured it; the 2FA verify step and passkey
sign-in did not, so a member with 2FA or a passkey who opened such a link
while signed out lost it. See changelogs/v3.37.0.md.
"""
from urllib.parse import quote

from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, value=None):
    """`value` (default: ?next=) if it's a same-site URL, else ''."""
    if value is None:
        value = request.GET.get('next', '')
    if value and url_has_allowed_host_and_scheme(
            value, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return value
    return ''


def with_next(url, request):
    """`url` plus ?next=<this request's path>, for GET page loads only.

    A POST or an XHR can't be replayed by a later redirect, so those get the
    bare URL (the old behaviour).
    """
    if request.method != 'GET' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return url
    return f'{url}?next={quote(request.get_full_path(), safe="/")}'
