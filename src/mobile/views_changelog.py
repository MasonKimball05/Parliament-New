"""
What's new in the app (v4.4.0): the website's changelog, version and roadmap
pages, as data (src/view/changelog.py has the shared helpers).

* ``GET changelog/?page=`` — the changelog page. Its main column is
  CHANGELOG.md, rendered by the page's own ``render_markdown`` and turned into
  blocks (src/markdown_blocks.py), cut into one section per heading (each
  ``### v3.21.4 — Title (2026-08-19)`` entry is a section with its version,
  title and date parsed out) and paged 20 sections at a time, in the file's
  order (newest first). Page 1 also carries the page's "Detailed Version
  Changelogs" card: one group per minor series, newest first, each version
  flagged when an external contributor helped (``group_changelogs_by_minor``,
  the page's own grouping).
* ``GET changelog/<version>/`` — one version's file, as blocks. Unknown
  versions are a 404, checked against ``known_versions()`` before anything is
  cached, as on the page.
* ``GET roadmap/`` — the 3.0 roadmap page's content.

A link to another version's file (``./changelogs/v2.8.0.md``) carries
``version`` so the app opens that version natively; links to in-page anchors
and other relative files have no target the app can open, so they show as
text.

Who sees what: these pages are public on the website, with no page toggle, so
every signed-in member (and pledge, unless a pledge restriction says otherwise)
sees all of it. The parsed blocks are cached for as long as the page caches
its parsed HTML (content changes only on deploy); a failed read isn't cached.
"""
import logging
import re

from django.core.cache import cache
from django.core.paginator import Paginator
from django.urls import reverse
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from src.markdown_blocks import html_to_blocks
from src.mobile.api import MobileAPIView
from src.view import changelog as cl

logger = logging.getLogger('src')

PAGE_SIZE = 20

_INDEX_KEY = 'mobile_changelog_sections_v1'
_DETAIL_KEY = 'mobile_changelog_detail_v1:'

_VERSION_LINK = re.compile(r'^(?:\./)?(?:changelogs/)?([A-Za-z0-9][A-Za-z0-9.\-]*)\.md$')
_VERSION_HEADING = re.compile(
    r'^(v\d+(?:\.\d+){1,2}(?:-[\w.\-]+)?)\s*(?:[—–-]\s*(.*?))?\s*'
    r'(?:\((\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})\))?\s*$')


def _iso(date):
    """'2026-08-19' or '08-19-2026' as '2026-08-19'."""
    if not date:
        return None
    if re.match(r'^\d{4}-', date):
        return date
    month, day, year = date.split('-')
    return f'{year}-{month}-{day}'


def _resolve_link(href):
    href = (href or '').strip()
    if not href or href.startswith('#'):
        return None
    if re.match(r'^(https?:|mailto:)', href, re.IGNORECASE) or href.startswith('/'):
        return {'href': href}
    m = _VERSION_LINK.match(href.split('#', 1)[0])
    if m:
        version = cl.sanitize_version(m.group(1))
        if version in cl.known_versions():
            return {'href': reverse('changelog_detail', args=[version]), 'version': version}
    return None


def markdown_blocks(text):
    """A changelog file as the page renders it, as blocks."""
    return html_to_blocks(cl.render_markdown(text, hard_breaks=False), _resolve_link)


def _plain(runs):
    return ''.join(r['text'] for r in runs).strip()


def _trim_rules(blocks):
    """The `---` between entries is the page's divider; the app draws cards instead."""
    while blocks and blocks[0]['type'] == 'rule':
        blocks.pop(0)
    while blocks and blocks[-1]['type'] == 'rule':
        blocks.pop()
    return blocks


def split_sections(blocks):
    """
    One section per heading of level 1-3, holding the blocks up to the next.
    A version heading gets its version, title and date parsed out, and
    ``detail`` when that version has its own file.
    """
    known = cl.known_versions()
    sections = []
    current = None
    for b in blocks:
        if b['type'] == 'heading' and b['level'] <= 3:
            title = _plain(b['text'])
            current = {'id': len(sections), 'level': b['level'], 'title': title,
                       'version': None, 'date': None, 'detail': None, 'blocks': []}
            m = _VERSION_HEADING.match(title)
            if m:
                current['version'] = m.group(1)
                current['title'] = (m.group(2) or '').strip() or m.group(1)
                current['date'] = _iso(m.group(3))
                if m.group(1) in known:
                    current['detail'] = m.group(1)
            sections.append(current)
            continue
        if current is None:
            current = {'id': 0, 'level': 0, 'title': '', 'version': None, 'date': None,
                       'detail': None, 'blocks': []}
            sections.append(current)
        current['blocks'].append(b)
    for s in sections:
        _trim_rules(s['blocks'])
    return sections


def _message(text):
    return [{'id': 0, 'level': 0, 'title': '', 'version': None, 'date': None, 'detail': None,
             'blocks': [{'type': 'paragraph', 'text': [{'text': text}]}]}]


def changelog_index():
    """CHANGELOG.md's sections and the detailed-changelog groups (cached)."""
    data = cache.get(_INDEX_KEY)
    if data is not None:
        return data
    ok = True
    try:
        with open(cl.changelog_path(), 'r', encoding='utf-8') as f:
            sections = split_sections(markdown_blocks(f.read()))
    except FileNotFoundError:
        sections = _message('Changelog file not found.')   # the page's words
    except Exception:
        logger.exception('Failed to build the app changelog')
        sections = _message('The changelog could not be loaded.')
        ok = False
    groups = [
        {
            'key': g['key'],
            'label': g['label'],
            'count': g['count'],
            'has_external': g['has_external'],
            'entries': [{'version': e['version'], 'has_external': e['has_external']} for e in g['entries']],
        }
        for g in cl.group_changelogs_by_minor(cl.detailed_changelog_entries())
    ]
    data = {'sections': sections, 'groups': groups}
    if ok:
        cache.set(_INDEX_KEY, data, cl.CONTENT_CACHE_TTL)
    return data


class ChangelogView(MobileAPIView):
    web_page = 'changelog'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        data = changelog_index()
        paginator = Paginator(data['sections'], PAGE_SIZE)
        page = paginator.get_page(request.query_params.get('page'))
        body = {
            'page': page.number,
            'pages': paginator.num_pages,
            'total': paginator.count,
            'has_next': page.has_next(),
            'sections': list(page.object_list),
            'web': {'page': reverse('changelog'), 'roadmap': reverse('roadmap')},
        }
        if page.number == 1:
            body['groups'] = data['groups']
        return Response(body)


class ChangelogVersionView(MobileAPIView):
    web_page = 'changelog_detail'
    web_page_toggle = False

    def get(self, request, version):
        safe = cl.sanitize_version(version)
        # Before the cache, as on the page: an unknown version mints no entry.
        if safe not in cl.known_versions():
            raise NotFound('No changelog for that version.')
        key = _DETAIL_KEY + safe
        blocks = cache.get(key)
        if blocks is None:
            try:
                with open(cl.version_file_path(safe), 'r', encoding='utf-8') as f:
                    blocks = markdown_blocks(f.read())
            except FileNotFoundError:
                raise NotFound('No changelog for that version.')
            except Exception:
                logger.exception('Failed to build the app changelog for %s', safe)
                blocks = [{'type': 'paragraph', 'text': [{'text': 'This changelog could not be loaded.'}]}]
            else:
                cache.set(key, blocks, cl.CONTENT_CACHE_TTL)
        return Response({
            'version': safe,
            'blocks': blocks,
            'web': {'page': reverse('changelog_detail', args=[safe])},
        })


class RoadmapView(MobileAPIView):
    web_page = 'roadmap'
    web_page_toggle = False

    def get(self, request):
        return Response({
            **cl.ROADMAP_HEADER,
            'pillars': cl.ROADMAP_PILLARS,
            'ships_before': cl.ROADMAP_SHIPS_BEFORE,
            'prerequisites': cl.ROADMAP_PREREQUISITES,
            'web': {'page': reverse('roadmap'), 'changelog': reverse('changelog')},
        })
