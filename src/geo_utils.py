"""
Shared IP geolocation utility for Parliament.

Lookups are LOCAL: a MaxMind GeoLite2 City database (``.mmdb``) on the
server's disk, read with the ``maxminddb`` package. No member or attacker IP
address ever leaves the server — there is no network call anywhere in this
module, and none as a fallback.

(This previously called ip-api.com over plain HTTP, sending every login IP
and its timing to a third party in cleartext. That code path, its
``GEO_API_BASE_URL`` setting and its rate-limit circuit breaker are gone.)

The database path is ``settings.GEOIP_CITY_DB`` (env ``GEOIP_CITY_DB``). If
the file is missing or unreadable, every lookup returns ``{}`` — the same
"lookup failed" result callers already handle (``is_foreign_ip`` gives the
benefit of the doubt) — and a warning is logged once per process. Setup:
DEPLOYMENT.md, "IP geolocation (GeoLite2)".

GeoLite2 City has no ISP/organisation/ASN data, so ``isp``, ``org`` and ``as``
are always ``''``. They are kept in the dict so its shape is unchanged.
"""
import logging
import os
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

PRIVATE_PREFIXES = ('10.', '172.16.', '172.17.', '172.18.', '172.19.',
                    '172.20.', '172.21.', '172.22.', '172.23.', '172.24.',
                    '172.25.', '172.26.', '172.27.', '172.28.', '172.29.',
                    '172.30.', '172.31.', '192.168.', '127.', '::1', 'unknown')

# How often (seconds) a process re-checks the database file: to pick up the
# weekly `geoipupdate` refresh (it replaces the file, so an open reader keeps
# serving the old copy) and to notice a file that was missing at start-up.
_RECHECK_INTERVAL = 300

# Lazily-opened, process-wide reader. maxminddb readers are safe to share
# between threads for lookups; the lock only guards opening/replacing it.
_lock = threading.Lock()
_reader = None
_reader_mtime = None
_next_check = 0.0
_warned = False


def _db_path():
    return getattr(settings, 'GEOIP_CITY_DB', '') or ''


def _warn_once(message, *args):
    global _warned
    if not _warned:
        _warned = True
        logger.warning(message, *args)


def _get_reader():
    """
    The open GeoLite2 reader, or ``None`` when the database is unavailable.
    Never raises, never touches the network.
    """
    global _reader, _reader_mtime, _next_check
    now = time.monotonic()
    if now < _next_check:
        return _reader
    with _lock:
        if now < _next_check:
            return _reader
        _next_check = now + _RECHECK_INTERVAL
        path = _db_path()
        try:
            mtime = os.stat(path).st_mtime if path else None
        except OSError:
            mtime = None
        if mtime is None:
            if _reader is None:
                _warn_once(
                    'GeoIP database not found at %r (settings.GEOIP_CITY_DB); '
                    'IP geolocation is disabled until it exists. See '
                    'DEPLOYMENT.md "IP geolocation (GeoLite2)".', path)
            # A reader that is already open keeps working (its file was
            # mid-replacement, or was removed after it was opened).
            return _reader
        if _reader is not None and mtime == _reader_mtime:
            return _reader
        try:
            import maxminddb
            new_reader = maxminddb.open_database(path)
        except Exception as e:  # ImportError, InvalidDatabaseError, OSError
            _warn_once('GeoIP database %r could not be opened (%s); IP '
                       'geolocation is disabled.', path, e)
            return _reader
        old, _reader, _reader_mtime = _reader, new_reader, mtime
        if old is not None:
            try:
                old.close()
            except Exception:  # nosec B110 # closing a stale reader is best-effort
                pass
        return _reader


def _reset():
    """Forget the open reader and the warn-once flag (tests; settings change)."""
    global _reader, _reader_mtime, _next_check, _warned
    with _lock:
        old, _reader, _reader_mtime, _next_check, _warned = _reader, None, None, 0.0, False
    if old is not None:
        try:
            old.close()
        except Exception:  # nosec B110 # closing a stale reader is best-effort
            pass


def _name(node):
    return ((node or {}).get('names') or {}).get('en', '') or ''


def _record_to_geo(record):
    """A GeoLite2-City record → the dict shape callers rely on."""
    country = record.get('country') or record.get('registered_country') or {}
    subdivisions = record.get('subdivisions') or [{}]
    location = record.get('location') or {}
    return {
        'country': _name(country),
        'country_code': country.get('iso_code', '') or '',
        'region': _name(subdivisions[0]),
        'city': _name(record.get('city')),
        'zip': (record.get('postal') or {}).get('code', '') or '',
        'lat': location.get('latitude'),
        'lon': location.get('longitude'),
        # Not in GeoLite2 City — kept for shape compatibility.
        'isp': '',
        'org': '',
        'as': '',
    }


def get_ip_geo(ip_address, timeout=None):
    """
    Return geolocation dict for an IP address, from the local database.
    Returns {} for private/local IPs, an unparseable address, an IP the
    database has no entry for, or when the database is unavailable.

    ``timeout`` is accepted for backward compatibility and ignored (the lookup
    is a local memory-mapped read, microseconds).

    Dict keys:
        country, country_code, region, city, zip, lat, lon, isp, org, as
    (isp/org/as are always '' — GeoLite2 City does not carry them.)
    """
    if not ip_address or any(ip_address.startswith(p) for p in PRIVATE_PREFIXES):
        return {}
    reader = _get_reader()
    if reader is None:
        return {}
    try:
        record = reader.get(ip_address)
    except Exception as e:  # ValueError for a malformed address
        logger.debug('Geo lookup failed for %r: %s', ip_address, e)
        return {}
    if not isinstance(record, dict):
        return {}
    geo = _record_to_geo(record)
    if not (geo['country_code'] or geo['city'] or geo['lat'] is not None):
        return {}
    return geo


def is_foreign_ip(ip_address, trusted_country='US', timeout=None):
    """
    Returns (is_foreign, geo_dict).
    is_foreign is True if the IP resolves to a country other than trusted_country.
    Returns (False, {}) for private IPs or lookup failures — benefit of the doubt.
    """
    geo = get_ip_geo(ip_address)
    if not geo:
        return False, geo
    country_code = geo.get('country_code', '')
    if not country_code:
        return False, geo
    return country_code != trusted_country, geo


def cached_is_foreign_ip(ip_address, trusted_country='US'):
    """
    ``is_foreign_ip`` for a hot request path (the mobile web-view handoff).

    This used to read only the 24 h ip-api.com cache so it could never block
    on the network. Lookups are now local and cheap, so it is the same as
    ``is_foreign_ip``; the name is kept for its caller.
    """
    return is_foreign_ip(ip_address, trusted_country=trusted_country)
