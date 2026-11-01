"""
The app's user guide, the native version of the website's ``guide_index``
(/guide/), its static guide pages (/guide/officers/events/ and the rest), the
two handoff documents and ``guide_article`` (/guide/article/<slug>/). Read
only; articles are edited in the Django admin, as before.

* ``GET guide/`` — the index: the Officer and Member sections, each page with
  its card's title, blurb and search words (the web index filters on those in
  the browser; the app does the same), plus the published ``GuideArticle``
  rows by category (the index's context has them, though its template doesn't
  list them yet).
* ``GET guide/pages/<url_name>/`` — one page of the registry
  (``src.view.guide.GUIDE_PAGES``): the HTML the page shows in its main column
  (``guide_page_html``: the template's content block, or the handoff doc's
  rendered markdown) turned into blocks (``src/markdown_blocks.py``). The page
  header's title and subtitle come out of the blocks into ``title`` and
  ``subtitle``, since the app draws its own header.
* ``GET guide/articles/<slug>/`` — a published article, as blocks, with the
  page's "related articles".

Who sees what is the web's: every page is ``@login_required`` only, with no
page toggle, so any signed-in member, unless a pledge restriction names that
page (checked against the page's own url_name, as the middleware does). The
Developer Handoff Guide is staff and Officers only (``can_read_developer_handoff``)
and is left out of the index for everyone else, as on the web.

Links: a site path (``/chats/``) is kept so the app opens it natively when it
has the screen; an in-page ``#anchor`` shows as text.
"""
import re

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.markdown_blocks import html_to_blocks
from src.mobile.api import MobileAPIView, check_web_page
from src.models import GuideArticle
from src.view.guide import (
    GUIDE_PAGES, GUIDE_SECTIONS, can_read_developer_handoff, guide_article_html, guide_page_html,
    visible_guide_pages,
)

_STEP_NUMBER = re.compile(r'^\d{1,2}$')
_NUMBERED_HEADING = re.compile(r'^(\d{1,2}) (?=\S)')
_CATEGORY_LABELS = dict(GuideArticle._meta.get_field('category').choices or ())


def _resolve_link(href):
    href = (href or '').strip()
    if not href or href.startswith('#'):
        return None
    if re.match(r'^(https?:|mailto:)', href, re.IGNORECASE) or (href.startswith('/') and not href.startswith('//')):
        return {'href': href}
    return None


def _plain(runs):
    return ''.join(r['text'] for r in runs).strip()


def _number_steps(blocks):
    """
    The static pages draw numbered steps as a badge (a lone "1") beside a
    heading or paragraph; as blocks the badge is a paragraph of its own. Fold
    it into what follows ("1. Your channels"). A badge inside a section's
    heading comes through as "1 What are..." and gets its full stop too.
    """
    out = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if (b['type'] == 'paragraph' and _STEP_NUMBER.match(_plain(b['text'])) and nxt is not None
                and nxt['type'] in ('heading', 'paragraph') and nxt['text']):
            runs = [dict(r) for r in nxt['text']]
            runs[0]['text'] = f"{_plain(b['text'])}. {runs[0]['text']}"
            out.append(dict(nxt, text=runs))
            i += 2
            continue
        if b['type'] == 'heading' and b['text'] and _NUMBERED_HEADING.match(b['text'][0]['text']):
            runs = [dict(r) for r in b['text']]
            runs[0]['text'] = _NUMBERED_HEADING.sub(r'\1. ', runs[0]['text'], count=1)
            b = dict(b, text=runs)
        elif b['type'] == 'list':
            b = dict(b, items=[_number_steps(item) for item in b['items']])
        elif b['type'] == 'quote':
            b = dict(b, blocks=_number_steps(b['blocks']))
        out.append(b)
        i += 1
    return out


def page_blocks(html, header=True):
    """
    A guide page's HTML as ``(title, subtitle, blocks)``. With ``header`` (a
    static page) the first level-1 heading is the title and the paragraph
    right after it the subtitle, both taken out of the blocks.
    """
    blocks = _number_steps(html_to_blocks(html, _resolve_link))
    title = subtitle = None
    if not header:
        return title, subtitle, blocks
    for i, b in enumerate(blocks):
        if b['type'] == 'heading' and b['level'] == 1:
            title = _plain(b['text'])
            rest = blocks[i + 1:]
            if rest and rest[0]['type'] == 'paragraph':
                subtitle = _plain(rest[0]['text'])
                rest = rest[1:]
            blocks = blocks[:i] + rest
            break
    return title, subtitle, blocks


def _page_json(page):
    return {
        'key': page['url_name'],
        'title': page['title'],
        'summary': page['summary'],
        'keywords': page['keywords'],
        'web_path': reverse(page['url_name']),
    }


def _article_json(a):
    return {
        'slug': a.slug,
        'title': a.title,
        'summary': a.summary or '',
        'category': a.category,
        'category_label': str(_CATEGORY_LABELS.get(a.category, a.category)),
        'web_path': reverse('guide_article', kwargs={'slug': a.slug}),
    }


class GuideIndexView(MobileAPIView):
    web_page = 'guide_index'
    web_page_toggle = False   # the guide pages have no @require_page_enabled

    def get(self, request):
        user = request.user
        articles = GuideArticle.objects.filter(is_published=True)
        return Response({
            'sections': [{
                'key': key,
                'title': title,
                'subtitle': subtitle,
                'pages': [_page_json(p) for p in visible_guide_pages(user, key)],
            } for key, title, subtitle in GUIDE_SECTIONS],
            'articles': [_article_json(a) for a in articles],
            'web': {'page': reverse('guide_index')},
        })


class GuidePageView(MobileAPIView):
    web_page = 'guide_index'
    web_page_toggle = False

    def get(self, request, key):
        page = GUIDE_PAGES.get(key)
        if page is None:
            raise exceptions.NotFound('No guide page by that name.')
        # The page's own url_name, as the pledge middleware checks it on the web.
        check_web_page(request.user, key, toggle=False)
        if page['officer_only'] and not can_read_developer_handoff(request.user):
            raise exceptions.PermissionDenied('Officers only.', code='forbidden')
        title, subtitle, blocks = page_blocks(guide_page_html(request, key), header=not page['doc'])
        return Response({
            **_page_json(page),
            'section': page['section'],
            'page_title': page['page_title'],
            # A static page's header; a handoff doc keeps its headings in the blocks.
            'heading': title or page['page_title'],
            'subtitle': subtitle or page['summary'],
            'blocks': blocks,
        })


class GuideArticleView(MobileAPIView):
    web_page = 'guide_article'
    web_page_toggle = False

    def get(self, request, slug):
        try:
            article = get_object_or_404(GuideArticle, slug=slug, is_published=True)
        except Http404:
            raise exceptions.NotFound('No guide article by that name.')
        related = (GuideArticle.objects.filter(is_published=True, category=article.category)
                   .exclude(id=article.id)[:5])
        return Response({
            **_article_json(article),
            'blocks': _number_steps(html_to_blocks(guide_article_html(article), _resolve_link)),
            'updated_at': article.updated_at.isoformat() if article.updated_at else None,
            'related': [_article_json(a) for a in related],
        })
