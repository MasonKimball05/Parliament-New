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

The hash is computed once per process per file (the file only changes with a
deploy, which restarts the process). If the file cannot be found the plain
static URL is returned, unversioned, rather than breaking the page.
"""
import hashlib

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

_versions = {}


def _version(path):
    if path not in _versions:
        found = finders.find(path)
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
