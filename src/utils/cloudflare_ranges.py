"""
Cloudflare's published edge ranges, and the freshness question that comes with
them (v3.19.3).

WHAT THIS IS FOR
----------------
`security_utils._peer_is_cloudflare` uses these to decide whether a request's
socket peer is really a Cloudflare edge before honouring the `CF-Connecting-IP`
header it sent. See `get_client_ip`'s docstring for why that matters — short
version: that header decides what the IP blocklist blocks, what the login rate
limiters count, and what every audit row records, and it is an ordinary
request header on any connection that did not come through Cloudflare.

⚠️ THIS LIST GOES STALE, AND A STALE ALLOWLIST FAILS IN THE ANNOYING DIRECTION,
NOT THE DANGEROUS ONE.
-------------------------------------------------------------------------------
Cloudflare adds ranges occasionally. If they add one and this file does not have
it, requests through that edge stop being "verified" and `get_client_ip` falls
back to the rightmost X-Forwarded-For entry — which is the edge address. So the
symptom of staleness is **audit rows that record a Cloudflare IP again**, i.e.
exactly the v3.18.8 bug, for a subset of traffic. Irritating and visible; not a
security failure, because the fallback value is still unforgeable.

It never fails the other way. A range being absent cannot cause a forged header
to be trusted — only a range being *wrongly present* could do that, and the only
way that happens is somebody pasting the wrong data in here.

KEEPING IT FRESH
----------------
Canonical source: https://www.cloudflare.com/ips-v4 and .../ips-v6 (also
https://api.cloudflare.com/client/v4/ips). Refresh with:

    manage.py refresh_cloudflare_ranges     # not written yet — see below

Until that exists, this is a hand-edit against the URLs above, and
`manage.py preflight` reports the age of `GENERATED` so it does not rot
silently. **A cron that rewrites a security allowlist unattended is a worse
idea than a check that tells you it is old** — the whole point of this file is
that its contents are trusted, and a fetch-and-write job is a supply-chain
edge nobody would be watching.

There is deliberately no network fetch at import time. This module must be
importable with no I/O: it is consulted on requests, and a security decision
that depends on an outbound HTTP call is a security decision that fails when
the network does.
"""
import ipaddress
from functools import lru_cache

#: Date the lists below were copied from cloudflare.com/ips-v4 and /ips-v6.
#: `preflight` warns when this is more than a year old.
GENERATED = '2026-08-07'

#: https://www.cloudflare.com/ips-v4
CLOUDFLARE_IPV4 = (
    '173.245.48.0/20',
    '103.21.244.0/22',
    '103.22.200.0/22',
    '103.31.4.0/22',
    '141.101.64.0/18',
    '108.162.192.0/18',
    '190.93.240.0/20',
    '188.114.96.0/20',
    '197.234.240.0/22',
    '198.41.128.0/17',
    '162.158.0.0/15',
    '104.16.0.0/13',
    '104.24.0.0/14',
    '172.64.0.0/13',
    '131.0.72.0/22',
)

#: https://www.cloudflare.com/ips-v6
CLOUDFLARE_IPV6 = (
    '2400:cb00::/32',
    '2606:4700::/32',
    '2803:f800::/32',
    '2405:b500::/32',
    '2405:8100::/32',
    '2a06:98c0::/29',
    '2c0f:f248::/32',
)


@lru_cache(maxsize=1)
def cloudflare_networks():
    """
    The ranges as parsed network objects, built once per process.

    `lru_cache` because this is called on requests and `ip_network` parsing is
    pure work with a constant answer. A malformed entry is skipped rather than
    raised: one bad string in the table should narrow verification, not 500
    every request on the site. `preflight` asserts the parsed count matches the
    declared one, so a skipped entry is loud in the place built to be read
    rather than loud in the request path.
    """
    networks = []
    for cidr in CLOUDFLARE_IPV4 + CLOUDFLARE_IPV6:
        try:
            networks.append(ipaddress.ip_network(cidr))
        except ValueError:
            continue
    return tuple(networks)


def declared_range_count():
    """How many ranges the tables above claim, for `preflight` to check against."""
    return len(CLOUDFLARE_IPV4) + len(CLOUDFLARE_IPV6)
