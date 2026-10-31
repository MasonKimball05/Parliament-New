"""
v4.4.0 — What's new in the app (src/mobile/views_changelog.py).

The app must show what the changelog, version and roadmap pages show: the same
CHANGELOG.md and version files, rendered by the pages' own markdown call and
turned into blocks (src/markdown_blocks.py), the page's version groups, and
the roadmap's text — public pages, so no page toggle; pledge restrictions
still apply.

Until its routes are in src/mobile/urls.py, this module is its own urlconf: the
project's urlconf plus the three routes.

    python manage.py test src.tests.mobile.test_changelog
"""
import html
import re

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import include, path

from src.markdown_blocks import blocks_plain_text, html_to_blocks
from src.mobile import views_changelog as v
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle
from src.view import changelog as cl

API = '/api/mobile/v1/'

_routes = [
    path('changelog/', v.ChangelogView.as_view(), name='mobile_changelog'),
    path('changelog/<str:version>/', v.ChangelogVersionView.as_view(), name='mobile_changelog_version'),
    path('roadmap/', v.RoadmapView.as_view(), name='mobile_roadmap'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_chlog')))] + root.urlpatterns


urlpatterns = _urlpatterns()


def _page_text(content):
    """A rendered page's visible text, whitespace collapsed."""
    body = content.decode()
    body = re.sub(r'<(script|style)\b.*?</\1>', ' ', body, flags=re.DOTALL)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', body)))


class MarkdownBlocksTests(SimpleTestCase):
    """The HTML -> blocks conversion, on the page's own markdown output."""

    def blocks(self, md):
        return html_to_blocks(cl.render_markdown(md, hard_breaks=False), v._resolve_link)

    def test_headings_paragraphs_and_inline_styles(self):
        b = self.blocks('## Title\n\nSome **bold**, *italic*, `code` and [a link](https://example.com/x).')
        self.assertEqual(b[0], {'type': 'heading', 'level': 2, 'text': [{'text': 'Title'}]})
        self.assertEqual(b[1]['type'], 'paragraph')
        self.assertEqual(b[1]['text'], [
            {'text': 'Some '}, {'text': 'bold', 'bold': True}, {'text': ', '},
            {'text': 'italic', 'italic': True}, {'text': ', '}, {'text': 'code', 'code': True},
            {'text': ' and '}, {'text': 'a link', 'href': 'https://example.com/x'}, {'text': '.'},
        ])

    def test_wrapped_lines_flow_but_hard_breaks_survive(self):
        # The files are hard-wrapped: the app lets a single newline flow (no
        # nl2br), while markdown's own hard break (two trailing spaces) stays.
        [p] = self.blocks('one\ntwo')
        self.assertEqual(p['text'], [{'text': 'one two'}])
        [p] = self.blocks('one  \ntwo')
        self.assertEqual(p['text'], [{'text': 'one\ntwo'}])

    def test_nested_lists(self):
        [lst] = self.blocks('- a\n- b\n    - b1\n    - b2\n- c')
        self.assertEqual(lst['type'], 'list')
        self.assertFalse(lst['ordered'])
        self.assertEqual(len(lst['items']), 3)
        inner = lst['items'][1][1]
        self.assertEqual(inner['type'], 'list')
        self.assertEqual(blocks_plain_text([inner]), 'b1\nb2')

    def test_ordered_list(self):
        [lst] = self.blocks('1. first\n2. second')
        self.assertTrue(lst['ordered'])
        self.assertEqual(lst['start'], 1)

    def test_table(self):
        [t] = self.blocks('| A | B |\n|---|---|\n| `x` | y |\n| z | **w** |')
        self.assertEqual(t['type'], 'table')
        self.assertEqual(t['header'], [[{'text': 'A'}], [{'text': 'B'}]])
        self.assertEqual(t['rows'], [[[{'text': 'x', 'code': True}], [{'text': 'y'}]],
                                     [[{'text': 'z'}], [{'text': 'w', 'bold': True}]]])

    def test_fenced_code_keeps_its_lines(self):
        [c] = self.blocks('```bash\npython manage.py migrate\n  indented\n```')
        self.assertEqual(c, {'type': 'code', 'text': 'python manage.py migrate\n  indented'})

    def test_quote_and_rule(self):
        b = self.blocks('> quoted\n\n---\n\nafter')
        self.assertEqual(b[0]['type'], 'quote')
        self.assertEqual(blocks_plain_text(b[0]['blocks']), 'quoted')
        self.assertEqual(b[1], {'type': 'rule'})

    def test_raw_html_lists_in_the_markdown(self):
        [lst] = self.blocks('<ul>\n<li><strong>v2.11.0</strong> (x)\n    <ul>\n    <li>one</li>\n    </ul>\n</li>\n</ul>')
        self.assertEqual(lst['items'][0][0]['text'][0], {'text': 'v2.11.0', 'bold': True})
        self.assertEqual(lst['items'][0][1]['type'], 'list')

    def test_scripts_are_dropped(self):
        b = self.blocks('<div><script>alert(1)</script>hi</div>')
        self.assertEqual(blocks_plain_text(b), 'hi')

    def test_links(self):
        real = sorted(cl.known_versions())[0]
        [p] = self.blocks(f'[file](./changelogs/{real}.md) [anchor](#x) [rel](other.md) [site](/vote/)')
        self.assertEqual(p['text'], [
            {'text': 'file', 'href': f'/changelog/{real}/', 'version': real},
            # An in-page anchor and another relative file: no target the app can open.
            {'text': ' anchor rel '},
            {'text': 'site', 'href': '/vote/'},
        ])

    def test_version_headings(self):
        b = self.blocks('# Doc\n\nintro\n\n---\n\n### v3.21.4 — A title (2026-08-19)\n\nbody\n\n---\n\n'
                        '### v2.24.0 - Older (05-29-2026)\n\nold\n\n### Plain heading\n\nx')
        s = v.split_sections(b)
        self.assertEqual([x['title'] for x in s], ['Doc', 'A title', 'Older', 'Plain heading'])
        self.assertEqual([x['version'] for x in s], [None, 'v3.21.4', 'v2.24.0', None])
        self.assertEqual([x['date'] for x in s], [None, '2026-08-19', '2026-05-29', None])
        # The --- dividers between entries are dropped.
        self.assertEqual([bl['type'] for bl in s[0]['blocks']], ['paragraph'])
        self.assertEqual([bl['type'] for bl in s[1]['blocks']], ['paragraph'])
        self.assertEqual(s[1]['detail'], 'v3.21.4' if 'v3.21.4' in cl.known_versions() else None)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class ChangelogViewTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('chlog-1')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, url, access=None):
        return self.api.get(API + url, **bearer(access or self.access))

    def test_index_first_page(self):
        r = self.get('changelog/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['page'], body['has_next']), (1, True))
        self.assertEqual(len(body['sections']), v.PAGE_SIZE)
        self.assertEqual(body['web'], {'page': '/changelog/', 'roadmap': '/roadmap/'})
        self.assertEqual(body['sections'][0]['title'], 'Parliament Development Changelog')
        entry = next(s for s in body['sections'] if s['version'])
        self.assertRegex(entry['date'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertTrue(entry['blocks'])

        # The page's groups: same grouping, newest series first, every v*.md file once.
        expected = cl.group_changelogs_by_minor(cl.detailed_changelog_entries())
        self.assertEqual([g['label'] for g in body['groups']], [g['label'] for g in expected])
        flat = [e['version'] for g in body['groups'] for e in g['entries']]
        self.assertEqual(sorted(flat), sorted(e['version'] for e in cl.detailed_changelog_entries()))
        self.assertNotIn('contributors', body['groups'][0]['entries'][0])

    def test_every_page_covers_the_whole_file_once(self):
        first = self.get('changelog/').json()
        titles = []
        for n in range(1, first['pages'] + 1):
            body = self.get(f'changelog/?page={n}').json()
            if n > 1:
                self.assertNotIn('groups', body)
            titles += [s['title'] for s in body['sections']]
        self.assertEqual(len(titles), first['total'])
        # Every heading of the page's rendered markdown is a section title or inside one.
        with open(cl.changelog_path(), encoding='utf-8') as f:
            page_text = blocks_plain_text(html_to_blocks(cl.render_markdown(f.read())))
        self.assertIn('Contributors', page_text)
        self.assertEqual(titles[-1], 'Contributors')

    def test_bad_page_numbers_clamp(self):
        last = self.get('changelog/').json()['pages']
        self.assertEqual(self.get('changelog/?page=9999').json()['page'], last)
        self.assertEqual(self.get('changelog/?page=abc').json()['page'], 1)

    def test_index_is_cached(self):
        self.get('changelog/')
        self.assertIsNotNone(cache.get(v._INDEX_KEY))

    def test_version_detail(self):
        version = cl.detailed_changelog_entries()[0]['version']
        r = self.get(f'changelog/{version}/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['version'], version)
        self.assertEqual(body['web'], {'page': f'/changelog/{version}/'})
        with open(cl.version_file_path(version), encoding='utf-8') as f:
            expected = html_to_blocks(cl.render_markdown(f.read(), hard_breaks=False), v._resolve_link)
        self.assertEqual(body['blocks'], expected)
        self.assertIsNotNone(cache.get(v._DETAIL_KEY + version))

    def test_unknown_version_is_a_404_and_caches_nothing(self):
        cl.known_versions()   # primed, as on any real request
        before = cache.get(v._DETAIL_KEY + 'v0.0.0-nope')
        r = self.get('changelog/v0.0.0-nope/')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error'], 'not_found')
        self.assertIsNone(before)
        self.assertIsNone(cache.get(v._DETAIL_KEY + 'v0.0.0-nope'))

    def test_roadmap_matches_the_page(self):
        r = self.get('roadmap/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['web'], {'page': '/roadmap/', 'changelog': '/changelog/'})
        self.assertEqual(len(body['pillars']), 4)
        self.assertEqual(body['ships_before'], cl.ROADMAP_SHIPS_BEFORE)

        # The page's pillar cards are written out in the template; every word
        # the app shows must be on the page, in the same order.
        page = _page_text(Client().get('/roadmap/').content)
        want = [body['badge'], body['title'], body['summary']]
        for pillar in body['pillars']:
            want += [pillar['icon'], pillar['title'], pillar['summary']]
            for item in pillar['items']:
                want.append(f"{item['name']} — {item['detail']}" + (f" {item['tag']}" if item.get('tag') else ''))
        want += [i for i in body['ships_before']]
        want += [f"{p['name']} — {p['detail']}" for p in body['prerequisites']]
        want.append(body['footer'])
        at = 0
        for text in want:
            found = page.find(text, at)
            self.assertNotEqual(found, -1, f'{text!r} is not on the roadmap page (after position {at})')
            at = found + len(text)

    def test_web_pages_still_render(self):
        web = Client()
        version = cl.detailed_changelog_entries()[0]['version']
        for url in ('/changelog/', f'/changelog/{version}/', '/roadmap/'):
            self.assertEqual(web.get(url).status_code, 200, url)

    def test_no_page_toggle_but_pledge_restrictions_apply(self):
        for name in ('changelog', 'changelog_detail', 'roadmap'):
            toggle(name, False)
        version = cl.detailed_changelog_entries()[0]['version']
        for url in ('changelog/', f'changelog/{version}/', 'roadmap/'):
            self.assertEqual(self.get(url).status_code, 200, url)

        pledge = make_user('chlog-p', member_status='Pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('roadmap', [])
        cache.clear()
        r = self.get('roadmap/', access)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'pledge_restricted')
        self.assertEqual(self.get('changelog/', access).status_code, 200)

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'changelog/').status_code, 401)
