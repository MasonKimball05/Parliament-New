"""
11-05-26: address search for the event form's location check-in map
(templates/officer/_checkin_area.html). An officer types an address, picks a
match, the pin drops there, and they drag it and size the circle.

Uses OpenStreetMap's Nominatim, called from the SERVER (the page's CSP allows
only 'self' for fetches, and the officer's own IP never reaches OSM). Its
usage policy is followed: an identifying User-Agent, at most one request a
second for the whole site, results cached, and searches only when the officer
presses Find (no search-as-you-type).

Tests never reach it: while PARLIAMENT_NETWORK_OFFLINE is set (the test
runner sets it) and nothing patched `requests.get`, a search is refused.
"""
import hashlib
import logging
import os
import time

import requests
from django.core.cache import cache

logger = logging.getLogger('src')

SEARCH_URL = 'https://nominatim.openstreetmap.org/search'
TIMEOUT_SECONDS = 8
MAX_RESULTS = 5
MAX_QUERY_CHARS = 200
CACHE_SECONDS = 30 * 24 * 3600
OFFLINE_ENV = 'PARLIAMENT_NETWORK_OFFLINE'
_REAL_GET = requests.get


class GeocodeError(Exception):
    pass


def _user_agent():
    from src.notifications import get_site_url
    return f'Parliament chapter site event check-in ({get_site_url()})'


def _wait_turn():
    """One request a second across the whole site (Nominatim's limit)."""
    for _ in range(20):
        if cache.add('geocode_nominatim_slot', 1, 1):
            return
        time.sleep(0.1)
    raise GeocodeError('Address search is busy. Try again in a moment.')


def search(query):
    """[{'label', 'lat', 'lng'}] for ``query`` (best match first), or raise GeocodeError."""
    q = ' '.join((query or '').split())[:MAX_QUERY_CHARS]
    if len(q) < 3:
        raise GeocodeError('Type an address or place name.')
    key = 'geocode_q_' + hashlib.sha256(q.lower().encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    if os.environ.get(OFFLINE_ENV) and requests.get is _REAL_GET:
        raise GeocodeError('Address search is offline during tests.')
    _wait_turn()
    try:
        response = requests.get(
            SEARCH_URL, params={'q': q, 'format': 'jsonv2', 'limit': MAX_RESULTS, 'addressdetails': 0},
            headers={'User-Agent': _user_agent(), 'Accept-Language': 'en'},
            timeout=TIMEOUT_SECONDS, allow_redirects=False,
        )
    except requests.RequestException as e:
        raise GeocodeError(f'Couldn’t reach the address search ({type(e).__name__}).') from e
    if response.status_code != 200:
        raise GeocodeError(f'The address search is unavailable right now ({response.status_code}).')
    try:
        rows = response.json()
    except ValueError as e:
        raise GeocodeError('The address search sent back something unreadable.') from e
    results = []
    for r in rows if isinstance(rows, list) else []:
        try:
            lat, lng = float(r['lat']), float(r['lon'])
        except (KeyError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            results.append({'label': str(r.get('display_name') or q)[:300], 'lat': lat, 'lng': lng})
    cache.set(key, results, CACHE_SECONDS)
    return results
