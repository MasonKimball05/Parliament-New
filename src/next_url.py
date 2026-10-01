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
    """`value` (default: ?next=) if it's a same-site PATH, else ''.

    Must start with one `/` (v3.38.2). A bare word such as `logout` is a
    valid relative URL, but `redirect()` tries it as a route NAME first: it
    sent the member to /logout/, and any other word was a NoReverseMatch 500
    straight after a successful 2FA.
    """
    if value is None:
        value = request.GET.get('next', '')
    if not value.startswith('/') or value.startswith(('//', '/\\')):
        return ''
    if url_has_allowed_host_and_scheme(
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
