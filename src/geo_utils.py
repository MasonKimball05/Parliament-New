"""
Shared IP geolocation utility for Parliament.
Uses ip-api.com (free, no API key, 45 req/min).
Results are cached 24h per IP to avoid redundant lookups.
"""
import logging
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Base URL for the IP geolocation provider. Defaults to ip-api.com's free
# (HTTP-only) endpoint. Set GEO_API_BASE_URL in the environment to an HTTPS
# endpoint (e.g. an ip-api.com Pro URL) to avoid sending lookups in cleartext.
GEO_API_BASE_URL = getattr(settings, 'GEO_API_BASE_URL', 'http://ip-api.com/json/')

PRIVATE_PREFIXES = ('10.', '172.16.', '172.17.', '172.18.', '172.19.',
                    '172.20.', '172.21.', '172.22.', '172.23.', '172.24.',
                    '172.25.', '172.26.', '172.27.', '172.28.', '172.29.',
                    '172.30.', '172.31.', '192.168.', '127.', '::1', 'unknown')

API_FIELDS = 'status,country,countryCode,regionName,city,zip,lat,lon,isp,org,as,query'

# --- Circuit breaker (v3.15.2) --------------------------------------------
# ip-api.com's free tier is 45 req/min. A brute-force/scanner flood on
# /login/ (each attempt does a geo lookup — including FAILED logins) blew past
# that, the calls stalled at the timeout, and the blocking calls piled up on
# Daphne's shared sync executor until the server wedged → 502 (07-19 incident).
# The breaker caps that: after a run of failures/timeouts the lookup
# short-circuits to {} INSTANTLY for a cooldown, so no matter how many unique
# IPs hammer login, the number of slow blocking calls is bounded. Cached IPs
# still resolve normally while the breaker is open. State lives in the shared
# cache (Redis) so it's coordinated across worker processes.
_BREAKER_OPEN_KEY = 'geo_breaker_open'
_BREAKER_FAIL_KEY = 'geo_breaker_fails'
_BREAKER_FAIL_THRESHOLD = 5      # consecutive-ish failures to trip
_BREAKER_FAIL_WINDOW = 120       # seconds the failure count accrues over
_BREAKER_COOLDOWN = 300          # seconds the breaker stays open once tripped

# Negative cache: remember a failed lookup for a single IP so a repeat hit on
# the same bad/unresolved IP doesn't re-issue the external call. Short TTL so a
# transient failure (or a valid IP caught during a brief provider blip) is
# retried soon. Only the geo *label* is affected — is_foreign_ip treats an
# empty geo as "benefit of the doubt" (same as a private IP), so there's no
# security regression, and once the breaker is open failures short-circuit
# before this point (so at most a handful of IPs are ever negative-cached).
_NEG_CACHE_TTL = 300             # seconds to remember a failed lookup per IP


def _breaker_is_open():
    return cache.get(_BREAKER_OPEN_KEY) is not None


def _breaker_record_failure():
    # Atomic increment so simultaneous failures across worker processes aren't
    # lost. A plain get()+set() (what this used to be) can drop concurrent
    # increments and delay tripping the breaker under exactly the flood it
    # guards against. cache.add() seeds the counter only if absent (no-op if it
    # already exists), so the window TTL is fixed from the first failure and the
    # count rolls over that window; incr() is atomic on Redis.
    cache.add(_BREAKER_FAIL_KEY, 0, _BREAKER_FAIL_WINDOW)
    try:
        n = cache.incr(_BREAKER_FAIL_KEY)
    except ValueError:
        # Counter expired between add() and incr() — reseed.
        cache.set(_BREAKER_FAIL_KEY, 1, _BREAKER_FAIL_WINDOW)
        n = 1
    if n >= _BREAKER_FAIL_THRESHOLD:
        cache.set(_BREAKER_OPEN_KEY, True, _BREAKER_COOLDOWN)
        logger.warning(
            "Geo lookup circuit breaker OPEN — ip-api.com failing/throttling; "
            "skipping lookups for %ss.", _BREAKER_COOLDOWN)


def _breaker_record_success():
    cache.delete(_BREAKER_FAIL_KEY)
    cache.delete(_BREAKER_OPEN_KEY)


def get_ip_geo(ip_address, timeout=2):
    """
    Return geolocation dict for an IP address.
    Returns {} for private/local IPs, or on any lookup failure.
    Always returns within `timeout` seconds (and instantly when the circuit
    breaker is open — see above).

    Cached 24h per IP. Dict keys:
        country, country_code, region, city, zip, lat, lon, isp, org, as
    """
    if not ip_address or any(ip_address.startswith(p) for p in PRIVATE_PREFIXES):
        return {}

    cache_key = f'geo_{ip_address}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Uncached IP: if the provider is currently failing, don't make the call —
    # returning {} instantly is what prevents the login-flood wedge.
    if _breaker_is_open():
        return {}

    try:
        resp = requests.get(
            f'{GEO_API_BASE_URL}{ip_address}',
            params={'fields': API_FIELDS},
            timeout=timeout,
        )
        data = resp.json()
        if data.get('status') == 'success':
            geo = {
                'country': data.get('country', ''),
                'country_code': data.get('countryCode', ''),
                'region': data.get('regionName', ''),
                'city': data.get('city', ''),
                'zip': data.get('zip', ''),
                'lat': data.get('lat'),
                'lon': data.get('lon'),
                'isp': data.get('isp', ''),
                'org': data.get('org', ''),
                'as': data.get('as', ''),
            }
            cache.set(cache_key, geo, 86400)
            _breaker_record_success()
            return geo
        else:
            # Non-success includes rate-limit responses — count toward the breaker.
            logger.warning(f"ip-api.com returned non-success for {ip_address}: {data.get('message')}")
            _breaker_record_failure()
            cache.set(cache_key, {}, _NEG_CACHE_TTL)
            return {}
    except Exception as e:
        logger.warning(f"Geo lookup failed for {ip_address}: {e}")
        _breaker_record_failure()
        cache.set(cache_key, {}, _NEG_CACHE_TTL)
        return {}


def is_foreign_ip(ip_address, trusted_country='US', timeout=2):
    """
    Returns (is_foreign, geo_dict).
    is_foreign is True if the IP resolves to a country other than trusted_country.
    Returns (False, {}) for private IPs or lookup failures — benefit of the doubt.
    """
    geo = get_ip_geo(ip_address, timeout=timeout)
    if not geo:
        return False, geo
    country_code = geo.get('country_code', '')
    if not country_code:
        return False, geo
    return country_code != trusted_country, geo
