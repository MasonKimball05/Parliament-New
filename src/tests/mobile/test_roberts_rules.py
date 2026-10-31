"""
Robert's Rules in the app (v4.4.0, src/mobile/views_roberts_rules.py): the
website's reference page as data (src/roberts_rules_content.py), behind the
same gates as the page.

The web template is still hand-written HTML, so ``TemplateInSyncTests`` reads
it and checks the data says the same things: the same sections, every heading
and motion, every line of text, and the Quick Reference Table cell for cell.
A change to one without the other fails here.

Until its route is in src/mobile/urls.py, this module is its own urlconf: the
real mobile routes plus ``roberts-rules/``.

    python manage.py test src.tests.mobile.test_roberts_rules
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import include, path

from src.mobile import urls as mobile_urls
from src.mobile import views_roberts_rules
from src.roberts_rules_content import CATEGORIES, MOTIONS, MOTIONS_BY_ID, SECTIONS
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'

urlpatterns = [
    path('api/mobile/v1/', include(([
        path('roberts-rules/', views_roberts_rules.RobertsRulesView.as_view(), name='mobile_roberts_rules'),
        *mobile_urls.urlpatterns,
    ], 'mobile'))),
    path('', include('src.urls')),
]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver', 'localhost'])
class RobertsRulesViewTests(TestCase):

    def setUp(self):
        cache.clear()
        self.member = make_user('rr-1')
        _, self.access, _ = make_device(self.member)
        self.api = Client()

    def get(self, access=None):
        return self.api.get(API + 'roberts-rules/', **bearer(access or self.access))

    def test_the_whole_reference(self):
        r = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([c['key'] for c in body['categories']], [k for k, _, _ in CATEGORIES])
        self.assertEqual(len(body['motions']), len(MOTIONS))
        self.assertEqual([s['id'] for s in body['sections']], [s['id'] for s in SECTIONS])
        self.assertEqual(body['web'], {'page': '/roberts-rules/'})

        table = next(m for m in body['motions'] if m['id'] == 'lay_on_table')
        self.assertEqual(table['quick'], {'second': 'Yes', 'debate': 'No', 'amend': 'No', 'vote': 'Majority'})
        self.assertEqual((table['category'], table['rank']), ('subsidiary', 1))
        self.assertEqual(table['form'], '"I move to lay the question on the table"')
        self.assertIn({'label': 'Vote required', 'value': 'Majority'}, table['rules'])
        self.assertIsNone(next(m for m in body['motions'] if m['id'] == 'reconsider')['quick'])

    def test_chapter_rules_link_to_the_constitution(self):
        quorum = next(s for s in self.get().json()['sections'] if s['id'] == 'quorum')
        box = next(b for b in quorum['blocks'] if b['type'] == 'box' and b['tone'] == 'chapter')
        self.assertEqual(box['link'], {'label': 'Constitution Article IV, Section 3',
                                       'path': '/constitution-bylaws/#const-meetings'})
        amending = next(s for s in self.get().json()['sections'] if s['id'] == 'amending-bylaws')
        box = next(b for b in amending['blocks'] if b['type'] == 'box' and b['tone'] == 'chapter')
        self.assertEqual(box['link']['path'], '/constitution-bylaws/')
        # The module's own data is left as it was (each response resolves a copy).
        self.assertIn('anchor', next(b for b in SECTIONS[1]['blocks'] if b['type'] == 'box')['link'])

    def test_sign_in_required(self):
        self.assertEqual(self.api.get(API + 'roberts-rules/').status_code, 401)

    def test_pledge_restriction_applies_but_no_page_toggle(self):
        pledge = make_user('rr-pl', member_type='Pledge')
        _, pledge_access, _ = make_device(pledge)
        restrict('roberts_rules', phases=())
        r = self.get(pledge_access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        # The web page has no @require_page_enabled, so a toggle doesn't hide it here either.
        toggle('roberts_rules', False)
        self.assertEqual(self.get().status_code, 200)


# ── The template and the data say the same things ────────────────────────────

class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []


class _Tree(HTMLParser):
    VOID = {'br', 'img', 'input', 'hr', 'path'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node('root', [], None)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _text(node, skip_lists=False):
    if isinstance(node, str):
        return node
    return ''.join(_text(c, skip_lists) for c in node.children
                   if not (skip_lists and not isinstance(c, str) and c.tag in ('ul', 'ol')))


def _norm(text):
    """Compare words only: no markup, emoji, check marks or punctuation spacing."""
    text = text.replace('**', '').lower()
    text = re.sub(r'[^\w/$%.,:;!?\'"()\[\]+=*-]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _walk(node, tag):
    for c in node.children:
        if isinstance(c, str):
            continue
        if c.tag == tag:
            yield c
        yield from _walk(c, tag)


def _strip_number(title):
    return re.sub(r'^\d+\.\s*', '', title)


def _data_corpus():
    """Every string in the data, one per line, for "is this sentence in there?"."""
    out = []

    def blocks(bs):
        for b in bs:
            for key in ('text', 'title'):
                if b.get(key):
                    out.append(b[key])
            for item in b.get('items', []):
                out.append(item['text'])
                out.extend(i['text'] for i in item['items'])
            out.extend(b.get('columns', []))
            out.extend(cell for row in b.get('rows', []) for cell in row)
            out.extend(b.get('lines', []))
            if b['type'] == 'box':
                blocks(b['blocks'])

    for s in SECTIONS:
        out.append(s['title'])
        blocks(s['blocks'])
    for m in MOTIONS:
        out += [m['name'], m['short_name'], f"Purpose: {m['purpose']}", f"Form: {m['form']}", *m['notes']]
        out += [f"{r['label']}: {r['value']}" for r in m['rules']]
        blocks(m['blocks'])
    return [_norm(t) for t in out]


class TemplateInSyncTests(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        html = (Path(settings.BASE_DIR) / 'templates' / 'roberts_rules.html').read_text()
        tree = _Tree()
        tree.feed(re.sub(r'{%.*?%}', '', html))
        cls.sections = list(_walk(tree.root, 'section'))
        cls.corpus = _data_corpus()
        cls.blob = '\n'.join(cls.corpus)

    def test_same_sections_in_the_same_order(self):
        page = [(s.attrs['id'], _text(next(_walk(s, 'h2'))).strip()) for s in self.sections]
        self.assertEqual(page, [(s['id'], s['title']) for s in SECTIONS])

    def test_every_heading_is_in_the_data(self):
        missing = []
        for section in self.sections:
            for tag in ('h3', 'h4'):
                for h in _walk(section, tag):
                    title = _norm(_strip_number(_text(h)))
                    if (section.attrs['id'], title) == ('main-motions', 'requirements'):
                        continue   # the list under it is the main motion's card in the data
                    if not any(_norm(_strip_number(line)) == title for line in self.corpus):
                        missing.append(f"{section.attrs['id']}: {_text(h).strip()}")
        self.assertEqual(missing, [], 'headings on the page but not in src/roberts_rules_content.py')

    def test_every_line_of_text_is_in_the_data(self):
        missing = []
        for section in self.sections:
            if section.attrs['id'] == 'quick-reference':
                continue   # built from MOTIONS; test_quick_reference_table_matches checks it
            for tag in ('p', 'li', 'td', 'th'):
                for node in _walk(section, tag):
                    line = _norm(_text(node, skip_lists=True))
                    if line and line not in self.blob:
                        missing.append(f"{section.attrs['id']}: {line}")
        self.assertEqual(missing, [], 'text on the page but not in src/roberts_rules_content.py')

    def test_every_motion_card_matches_its_motion(self):
        """Each card's lines (purpose, form, rules, notes, examples) are exactly that motion's, nothing borrowed."""
        cards = {}
        for section in self.sections:
            if section.attrs['id'] not in ('subsidiary-motions', 'privileged-motions', 'incidental-motions',
                                           'reconsider-rescind'):
                continue
            for h in _walk(section, 'h3'):
                if not h.parent.attrs.get('class', '').startswith(('border-l-4', 'border-2')):
                    continue
                lines = {_norm(_text(n, skip_lists=True)) for tag in ('p', 'li') for n in _walk(h.parent, tag)}
                cards[_strip_number(_text(h).strip())] = lines - {''}
        self.assertEqual(set(cards), {m['name'] for m in MOTIONS} - {'Main Motion'})   # main has its own section
        for name, lines in cards.items():
            m = next(m for m in MOTIONS if m['name'] == name)
            data = {f"purpose: {m['purpose']}", f"form: {m['form']}", *m['notes'],
                    *(f"{r['label']}: {r['value']}" for r in m['rules'])}
            for b in m['blocks']:
                data.add(b['title'])
                data.update(i['text'] for inner in b['blocks'] for i in inner['items'])
            data = {_norm(t) for t in data}
            # Reconsider and rescind put "How it works:" over their form and notes.
            self.assertEqual(lines - {'how it works:'}, data, name)

    def test_quick_reference_table_matches(self):
        table = next(_walk(next(s for s in self.sections if s.attrs['id'] == 'quick-reference'), 'tbody'))
        rows = [[_text(td).strip() for td in tr.children if not isinstance(td, str) and td.tag == 'td']
                for tr in _walk(table, 'tr')]
        page = [r for r in rows if len(r) == 5]
        data = [[m['short_name'], *m['quick'].values()] for m in MOTIONS if m['quick']]
        self.assertEqual(page, data)
        # The table's group headings are the categories, in order.
        groups = [r[0] for r in rows if len(r) == 1]
        self.assertEqual(groups, [f'{lbl.upper()} MOTIONS' for k, lbl, _ in CATEGORIES if k != 'restore'])

    def test_motion_blocks_and_categories_line_up(self):
        referenced = [b['id'] for s in SECTIONS for b in s['blocks'] if b['type'] == 'motion']
        self.assertEqual(sorted(referenced), sorted(MOTIONS_BY_ID))
        keys = [k for k, _, _ in CATEGORIES]
        self.assertTrue(all(m['category'] in keys for m in MOTIONS))
        for key in ('subsidiary', 'privileged'):
            self.assertEqual([m['rank'] for m in MOTIONS if m['category'] == key],
                             list(range(1, 1 + sum(m['category'] == key for m in MOTIONS))))
