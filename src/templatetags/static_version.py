"""
`{% static_v 'js/base/core.js' %}` — a static URL that changes when the file does (v3.45.6).

WHY
---
nginx serves /static/ with `expires 30d` and `Cache-Control: public,
immutable`, and STORAGES uses the plain StaticFilesStorage (no hashed file
names). That is fine for files that never change, but v3.45.6 moved ~90 KB of
base.html's JavaScript into static files, and those will change. Under
`immutable`, a browser would keep running the old script for up to 30 days
after a deploy. This tag appends `?v=<first 12 hex of the file's sha256>`, so
a changed file has a new URL and an unchanged one stays cached.

v3.45.7: in production the hash is of the copy in STATIC_ROOT, the file
nginx actually serves, not of the source under static/. Hashing the source
meant that a restart before `collectstatic` gave the OLD file a NEW url, and
under `immutable` (browser) and Cloudflare's cache that pairing stuck: a later
collectstatic produced the same url, so nothing ever fetched the new file. Now
the url only changes when the served file changes. With DEBUG on (runserver
serves the source) or when STATIC_ROOT has no copy, the source is hashed.

The hash is computed once per process per file (the file only changes with a
deploy, which restarts the process). If the file cannot be found the plain
static URL is returned, unversioned, rather than breaking the page.
"""
import hashlib
import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.templatetags.static import static

register = template.Library()

_versions = {}


def _served_file(path):
    """The file a browser will get for this static path."""
    if not settings.DEBUG:
        try:
            collected = staticfiles_storage.path(path)
        except Exception:  # noqa: BLE001 — storage without a local path, or no STATIC_ROOT
            collected = None
        if collected and os.path.isfile(collected):
            return collected
    return finders.find(path)


def _version(path):
    if path not in _versions:
        found = _served_file(path)
        if not found:
            _versions[path] = ''
        else:
            with open(found, 'rb') as fh:
                _versions[path] = hashlib.sha256(fh.read()).hexdigest()[:12]
    return _versions[path]


@register.simple_tag
def static_v(path):
    url = static(path)
    version = _version(path)
    return f'{url}?v={version}' if version else url
