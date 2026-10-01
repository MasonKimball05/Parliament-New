"""
Signatures for email tracking-pixel URLs (v3.38.2, 10-01-26).

A tracking pixel has to work with no login (a mail client fetches it), and its
URL used to carry only ids: /kai/track-email/<report_id>.gif,
/track/announcement/<id>/user/<user_id>/. Ids are small and sequential, so
anyone could request one and write "Accused person viewed notification email"
onto a Kai report, or mark any member as having read any announcement.

Each URL now ends in `?s=<signature>`, an HMAC of the pixel's kind and ids
under SECRET_KEY. A request without the right signature still gets the same
1x1 GIF (no oracle), and records nothing.

Emails sent before v3.38.2 carry unsigned URLs, so opening one of those is no
longer recorded.

Still true, and not fixable here: a recorded "view" means something fetched
the image. Mail clients and privacy proxies prefetch images, so treat it as a
hint, never as proof a person read the message.
"""
from django.utils.crypto import constant_time_compare, salted_hmac

_SALT = 'src.utils.tracking_sig'
_LENGTH = 32   # hex characters (128 bits)

KAI_ACCUSED = 'kai-accused'
KAI_SUBMITTER = 'kai-submitter'
ANNOUNCEMENT = 'announcement'
EVENT_REMINDER = 'event-reminder'


def pixel_signature(kind, *ids):
    value = ':'.join([kind, *(str(i) for i in ids)])
    return salted_hmac(_SALT, value).hexdigest()[:_LENGTH]


def signed_pixel_url(url, kind, *ids):
    """`url` plus its ?s= signature."""
    return f'{url}?s={pixel_signature(kind, *ids)}'


def pixel_request_is_signed(request, kind, *ids):
    sent = request.GET.get('s', '')
    return bool(sent) and constant_time_compare(sent, pixel_signature(kind, *ids))
