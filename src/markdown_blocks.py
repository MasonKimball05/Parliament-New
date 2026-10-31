"""
Rendered markdown as structured blocks, for the app (v4.4.0).

The app has no HTML renderer, so a page that shows markdown on the website
(the changelog, first) sends the app the *same* HTML the page renders — made by
the same ``markdown`` call — turned into plain data the app draws with Text and
View. Converting the page's HTML rather than re-parsing the markdown means the
two can't disagree about what a line is: whatever the page shows as a table,
the app gets as a table.

Blocks::

    {'type': 'heading', 'level': 1-6, 'text': [inline]}
    {'type': 'paragraph', 'text': [inline]}
    {'type': 'list', 'ordered': bool, 'start': int, 'items': [[block]]}
    {'type': 'code', 'text': str}
    {'type': 'table', 'header': [[inline]], 'rows': [[[inline]]]}
    {'type': 'quote', 'blocks': [block]}
    {'type': 'rule'}

Inline runs: ``{'text': str}`` plus any of ``bold``, ``italic``, ``code``,
``strike`` (True) and ``href`` (a link). A line break inside a paragraph is a
``\\n`` in the text. ``link_resolver(href)`` decides what a link becomes: it
returns the run's extra keys (e.g. ``{'href': ...}``), or None to show the text
without a link (an in-page ``#anchor`` the app can't jump to, say).
"""
import re
from html.parser import HTMLParser

_VOID = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr', 'col'}
_SKIP = {'script', 'style', 'head', 'title', 'template'}
_HEADINGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}
#: Elements that only group other blocks: their children are read in place.
_TRANSPARENT_BLOCKS = {'div', 'section', 'article', 'main', 'header', 'footer', 'aside', 'nav',
                       'figure', 'details', 'center', 'dl', 'body', 'html', 'thead', 'tbody', 'tfoot'}
_BLOCK_TAGS = (set(_HEADINGS) | _TRANSPARENT_BLOCKS
               | {'p', 'ul', 'ol', 'li', 'pre', 'table', 'tr', 'blockquote', 'hr', 'summary',
                  'figcaption', 'dt', 'dd'})
_INLINE_STYLE = {'strong': 'bold', 'b': 'bold', 'em': 'italic', 'i': 'italic', 'code': 'code',
                 'kbd': 'code', 'samp': 'code', 'tt': 'code', 'del': 'strike', 's': 'strike',
                 'strike': 'strike'}
_WS = re.compile(r'\s+')


class _Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent')

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or ())
        self.children = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    """A forgiving DOM: an end tag closes up to its open element, a stray one is ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('#root')
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in _SKIP:
            if tag in _SKIP:
                self.skip += 1
            return
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if self.skip or tag in _SKIP:
            return
        self.stack[-1].children.append(_Node(tag, attrs, self.stack[-1]))

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in _VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip and data:
            self.stack[-1].children.append(data)


def _text_of(node):
    """All the text under a node, verbatim (for code blocks)."""
    if isinstance(node, str):
        return node
    if node.tag == 'br':
        return '\n'
    return ''.join(_text_of(c) for c in node.children)


class _Converter:
    def __init__(self, link_resolver):
        self.link_resolver = link_resolver or (lambda href: {'href': href} if href else None)

    # ── inline ──────────────────────────────────────────────────────────────

    def _inline(self, node, style, out):
        if isinstance(node, str):
            out.append(dict(style, text=node))
            return
        tag = node.tag
        if tag == 'br':
            out.append(dict(style, text='\n'))
            return
        if tag == 'img':
            alt = node.attrs.get('alt') or ''
            if alt:
                out.append(dict(style, text=alt))
            return
        if tag in ('input', 'hr'):
            return
        child_style = style
        if tag in _INLINE_STYLE:
            child_style = dict(style, **{_INLINE_STYLE[tag]: True})
        elif tag == 'a':
            extra = self.link_resolver(node.attrs.get('href') or '')
            child_style = dict(style)
            child_style.pop('href', None)
            if extra:
                child_style.update(extra)
        for c in node.children:
            self._inline(c, child_style, out)

    @staticmethod
    def _finish_runs(runs):
        """Collapse whitespace like a browser, trim the ends, merge same-styled neighbours."""
        cleaned = []
        for r in runs:
            text = r['text']
            if not r.get('code'):
                # Whitespace collapses; a <br>'s newline survives (it has no
                # other whitespace in its run).
                text = text if text == '\n' else _WS.sub(' ', text)
            else:
                text = text.replace('\n', ' ')
            if text:
                cleaned.append(dict(r, text=text))
        # Spaces around a line break, and doubled spaces across run edges.
        merged = []
        for r in cleaned:
            if merged:
                prev = merged[-1]
                if prev['text'].endswith((' ', '\n')) and r['text'].startswith(' ') and not r.get('code'):
                    r = dict(r, text=r['text'].lstrip(' '))
                    if not r['text']:
                        continue
                if r['text'].startswith('\n') and prev['text'].endswith(' ') and not prev.get('code'):
                    prev['text'] = prev['text'].rstrip(' ')
                    if not prev['text']:
                        merged.pop()
                        prev = merged[-1] if merged else None
                if prev is not None and {k: v for k, v in prev.items() if k != 'text'} == \
                        {k: v for k, v in r.items() if k != 'text'}:
                    prev['text'] += r['text']
                    continue
            merged.append(dict(r))
        # Trim the paragraph's ends.
        while merged and not merged[0]['text'].strip():
            merged.pop(0)
        while merged and not merged[-1]['text'].strip():
            merged.pop()
        if merged:
            merged[0]['text'] = merged[0]['text'].lstrip()
            merged[-1]['text'] = merged[-1]['text'].rstrip()
        return merged

    def runs(self, node):
        out = []
        for c in (node.children if not isinstance(node, str) else [node]):
            self._inline(c, {}, out)
        return self._finish_runs(out)

    # ── blocks ──────────────────────────────────────────────────────────────

    def blocks(self, node):
        """The blocks in a node's children; loose inline content becomes paragraphs."""
        out = []
        pending = []

        def flush():
            if pending:
                runs = self._finish_runs(pending[:])
                pending.clear()
                if runs:
                    out.append({'type': 'paragraph', 'text': runs})

        for c in node.children:
            if isinstance(c, str) or c.tag not in _BLOCK_TAGS:
                self._inline(c, {}, pending)
                continue
            flush()
            out.extend(self.block(c))
        flush()
        return out

    def block(self, node):
        tag = node.tag
        if tag in _HEADINGS:
            runs = self.runs(node)
            return [{'type': 'heading', 'level': _HEADINGS[tag], 'text': runs}] if runs else []
        if tag in ('p', 'summary', 'figcaption', 'dt', 'dd'):
            # A <p> that holds block content (raw HTML in the markdown) is read as blocks.
            if any(not isinstance(c, str) and c.tag in _BLOCK_TAGS for c in node.children):
                return self.blocks(node)
            runs = self.runs(node)
            return [{'type': 'paragraph', 'text': runs}] if runs else []
        if tag in ('ul', 'ol'):
            items = []
            for c in node.children:
                if isinstance(c, str):
                    continue
                if c.tag == 'li':
                    items.append(self.blocks(c))
                elif c.tag in ('ul', 'ol') and items:
                    # A nested list written outside its <li>: it belongs to the item before it.
                    items[-1].extend(self.block(c))
            items = [i for i in items if i]
            if not items:
                return []
            try:
                start = int(node.attrs.get('start') or 1)
            except ValueError:
                start = 1
            return [{'type': 'list', 'ordered': tag == 'ol', 'start': start, 'items': items}]
        if tag == 'li':
            # An <li> outside a list.
            return [{'type': 'list', 'ordered': False, 'start': 1, 'items': [self.blocks(node)]}]
        if tag == 'pre':
            text = _text_of(node).rstrip('\n')
            return [{'type': 'code', 'text': text}] if text.strip() else []
        if tag == 'blockquote':
            inner = self.blocks(node)
            return [{'type': 'quote', 'blocks': inner}] if inner else []
        if tag == 'hr':
            return [{'type': 'rule'}]
        if tag == 'table':
            return self.table(node)
        if tag == 'tr':
            return self.table(_wrap('table', node))
        # A grouping element (div, the codehilite wrapper, details, thead...).
        return self.blocks(node)

    def table(self, node):
        header, rows = [], []

        def walk(n):
            for c in n.children:
                if isinstance(c, str):
                    continue
                if c.tag == 'tr':
                    cells = [x for x in c.children if not isinstance(x, str) and x.tag in ('th', 'td')]
                    texts = [self.runs(x) for x in cells]
                    in_head = n.tag == 'thead' or (cells and all(x.tag == 'th' for x in cells) and not rows
                                                   and not header)
                    if in_head and not header:
                        header.extend(texts)
                    else:
                        rows.append(texts)
                elif c.tag in ('thead', 'tbody', 'tfoot'):
                    walk(c)

        walk(node)
        if not header and not rows:
            return []
        return [{'type': 'table', 'header': header, 'rows': rows}]


def _wrap(tag, child):
    node = _Node(tag)
    node.children = [child]
    return node


def html_to_blocks(html, link_resolver=None):
    """The blocks of an HTML fragment (the page's rendered markdown)."""
    builder = _TreeBuilder()
    builder.feed(html or '')
    builder.close()
    return _Converter(link_resolver).blocks(builder.root)


def blocks_plain_text(blocks):
    """A block list's text, flattened (for tests and search)."""
    parts = []
    for b in blocks:
        t = b['type']
        if t in ('heading', 'paragraph'):
            parts.append(''.join(r['text'] for r in b['text']))
        elif t == 'list':
            for item in b['items']:
                parts.append(blocks_plain_text(item))
        elif t == 'code':
            parts.append(b['text'])
        elif t == 'quote':
            parts.append(blocks_plain_text(b['blocks']))
        elif t == 'table':
            for row in [b['header']] + b['rows']:
                parts.append(' | '.join(''.join(r['text'] for r in cell) for cell in row))
    return '\n'.join(p for p in parts if p)
