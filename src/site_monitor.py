"""
The platform owner's external site monitor (v3.35.3, 09-29-26).

go-sentinel (github.com/MasonKimball05/go-sentinel) checks the live site from
the outside every 30 minutes, including that trap paths such as `/.env` and
`/.git/config` are NOT publicly readable. Those are exactly the paths the
honeypot (src/view/honeypot.py) bans on sight, so on 09-27-26 the monitor got
the owner's own IPs blacklisted, and since then it has had to skip them.

A request that carries `X-Site-Monitor-Token: <settings.SITE_MONITOR_TOKEN>`
is recognised here, and the honeypot answers it with the site's ordinary 404
instead of banning, logging and alerting. That is also the *honest* answer for
a monitor: the honeypot's fake `.env` / `[core]` bodies look like real leaks.
A real leak (nginx serving the file itself) never reaches Django, so the
monitor still catches it.

What the token does NOT do, on purpose: authenticate, lift an existing
IPBlacklist ban, skip rate limits, or skip the input scanner. A leaked token
lets someone probe trap paths without being banned — i.e. the honeypot stops
working against that one person — and nothing more. Rotate it by changing the
env var on the server and the GitHub Actions secret.

The monitor runs from GitHub Actions, whose IPs change every run, so an IP
allowlist cannot work; a user-agent check would let any scanner opt out.
"""
import hmac

from django.conf import settings

HEADER_META_KEY = 'HTTP_X_SITE_MONITOR_TOKEN'   # the `X-Site-Monitor-Token` header
MIN_TOKEN_LENGTH = 32


def configured_token():
    """The server's token, or '' when unset or too short to be trusted."""
    token = getattr(settings, 'SITE_MONITOR_TOKEN', '') or ''
    return token if len(token) >= MIN_TOKEN_LENGTH else ''


def is_site_monitor_request(request):
    """True only when a usable token is configured AND the request presents it."""
    token = configured_token()
    if not token:
        return False
    sent = request.META.get(HEADER_META_KEY, '')
    if not sent:
        return False
    # Constant-time: a wrong guess must not leak how much of it was right.
    return hmac.compare_digest(sent.encode('utf-8', 'replace'), token.encode('utf-8'))
