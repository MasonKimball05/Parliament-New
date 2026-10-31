"""
The app's Robert's Rules reference (v4.4.0), the native version of the
website's ``roberts_rules`` page.

* ``GET roberts-rules/`` — the whole reference at once, as data rather than
  HTML (``src/roberts_rules_content.py``): the motion categories, every motion
  (purpose, what to say, second / debate / amendment / vote rules and the
  Quick Reference Table's short cells) and the page's sections as blocks. It's
  static text of under 100 KB, so the app takes it in one request and searches
  it on the phone.

Each chapter rule that cites the Constitution & Bylaws carries
``link: {label, path}``, a site path the app opens natively. ``web.page`` is
the page itself, for "Open on the website".

The web page has no ``@require_page_enabled``, so only the pledge restriction
applies, as it does there.
"""
import copy

from django.urls import reverse
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.roberts_rules_content import content


def _resolve_links(blocks, cnb_path):
    """Turn each box's {label, anchor} link into {label, path} (in place)."""
    for block in blocks:
        if block['type'] != 'box':
            continue
        link = block.get('link')
        if link:
            anchor = link.pop('anchor')
            link['path'] = f'{cnb_path}#{anchor}' if anchor else cnb_path
        _resolve_links(block['blocks'], cnb_path)


class RobertsRulesView(MobileAPIView):
    web_page = 'roberts_rules'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        data = copy.deepcopy(content())
        cnb_path = reverse('constitution_bylaws')
        for section in data['sections']:
            _resolve_links(section['blocks'], cnb_path)
        data['web'] = {'page': reverse('roberts_rules')}
        return Response(data)
