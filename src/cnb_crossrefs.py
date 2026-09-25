"""
Cross-references inside the Constitution & Bylaws (09-25-26).

WHY: a scan of the August 2025 text found four references pointing at the
wrong place — leftovers from renumbering nobody could see:

  * Constitution V §1 cites "Article VII, Section 1 (a)" of the Constitution,
    which has six articles.
  * Bylaws VII §10 sends amendments to the Bylaws to "Article VI of the
    Bylaws" (Executive Board Expectations; amendments are Art. X) and
    amendments to the Constitution to "Article VII of the Constitution"
    (doesn't exist; it is Art. VI).
  * Bylaws VI §2 says Kai operations are in "Article VI, Section 1 (a)"
    (Kai is Bylaws III §2 / VII §1).

Every resolution that adds, deletes or moves a section can create more.
This module finds them. It does NOT fix the text — that is a chapter
resolution; the tool's job is to make the problem visible to the C&B chair.

Two kinds of finding:
  * ERROR   — the cited article/section does not exist.
  * WARNING — it exists, but the sentence around the reference names a topic
              that is the TITLE of a different article/section (e.g. the
              sentence says "amendments to the Bylaws" and the reference
              points at "Executive Board Expectations"). A heuristic: it can
              be wrong, and says so in its message.

Also provides `link_references(text, source_doc_type)` — escaped HTML with
every resolvable reference turned into a link to that section's anchor on
the C&B viewer (`<doc>-art-<N>-sec-<M>`). Unresolvable ones stay plain text.
"""
import re
from dataclasses import dataclass, field

from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

# "Article VII, Section 1 (a) of the Bylaws", "Article VI of the Bylaws",
# "Article II, Section 2 (6)", "Article X: ..." — the optional "of the X"
# must follow within the same clause (no . ; or newline in between).
REF_RE = re.compile(
    r'\bArticle\s+(?P<art>[IVXLC]+)\b'
    r'(?:\s*,?\s*Section\s+(?P<sec>\d+))?'
    r'(?P<tail>[^.;\n]{0,60}?\bof the\s+(?P<doc>Bylaws|Constitution)\b)?'
)
# The builder's own bracketed form, e.g. "Bylaws Art. VII § 1".
BRACKET_RE = re.compile(r'\b(?P<doc>Constitution|Bylaws) Art\. (?P<art>[IVXLC]+) § (?P<sec>\d+)')

_DOC_WORD = {'Bylaws': 'bylaws', 'Constitution': 'constitution'}
# A reference with no "of the X" means "this document"; the Appendix is an
# appendix to the Bylaws in practice (its refs are slating/officer bylaws).
_DEFAULT_TARGET = {'constitution': 'constitution', 'bylaws': 'bylaws', 'appendix': 'bylaws'}


@dataclass
class Ref:
    start: int
    end: int
    doc: str
    art: str
    sec: str = ''          # '' = article-level reference
    text: str = ''


@dataclass
class Finding:
    level: str             # 'error' | 'warning'
    source: str            # e.g. "Bylaws VII §10"
    ref_text: str
    message: str
    source_anchor: str = ''
    suggestions: list = field(default_factory=list)


def find_references(text, source_doc_type):
    refs = []
    for m in REF_RE.finditer(text or ''):
        doc = _DOC_WORD[m.group('doc')] if m.group('doc') else _DEFAULT_TARGET.get(source_doc_type, source_doc_type)
        # Link only the "Article …, Section N" part, not the whole tail.
        end = m.end('sec') if m.group('sec') else m.end('art')
        refs.append(Ref(m.start(), end, doc, m.group('art'), m.group('sec') or '', m.group(0)))
    for m in BRACKET_RE.finditer(text or ''):
        refs.append(Ref(m.start(), m.end(), _DOC_WORD[m.group('doc')], m.group('art'), m.group('sec'), m.group(0)))
    refs.sort(key=lambda r: r.start)
    # Drop overlaps (keep the first).
    out, last_end = [], -1
    for r in refs:
        if r.start >= last_end:
            out.append(r)
            last_end = r.end
    return out


class Structure:
    """Snapshot of the live documents: which articles/sections exist, and their titles."""

    def __init__(self, documents):
        # documents: iterable of (doc_type, doc_label, [(art_no, art_title, [(sec_no, sec_title, content), ...]), ...])
        self.articles = {}      # (doc, art) -> title
        self.sections = {}      # (doc, art, sec) -> title
        self.contents = []      # (doc, doc_label, art, sec, content)
        self.labels = {}
        for doc, label, arts in documents:
            self.labels[doc] = label
            for art, art_title, secs in arts:
                self.articles[(doc, art)] = art_title or ''
                for sec, sec_title, content in secs:
                    self.sections[(doc, art, sec)] = sec_title or ''
                    self.contents.append((doc, label, art, sec, content or ''))
        # Topic index: every article/section title of 2+ words → its locations.
        self.topics = {}
        for (doc, art), t in self.articles.items():
            self._index(t, (doc, art, ''))
        for (doc, art, sec), t in self.sections.items():
            self._index(t, (doc, art, sec))

    def _index(self, title, loc):
        t = (title or '').strip().rstrip(':').lower()
        if len(t.split()) >= 2:
            self.topics.setdefault(t, set()).add(loc)

    @classmethod
    def from_db(cls):
        from src.models import GoverningDocument
        return cls.from_documents(GoverningDocument.objects.prefetch_related('articles__sections'))

    @classmethod
    def from_documents(cls, documents):
        """From GoverningDocument objects whose articles__sections are prefetched (no extra queries)."""
        docs = []
        for d in documents:
            arts = []
            for a in d.articles.all():
                arts.append((a.number, a.title, [(s.number, s.title, s.content) for s in a.sections.all()]))
            docs.append((d.doc_type, d.get_doc_type_display(), arts))
        return cls(docs)

    def exists(self, ref):
        if ref.sec:
            return (ref.doc, ref.art, ref.sec) in self.sections
        return (ref.doc, ref.art) in self.articles

    def describe(self, doc, art, sec=''):
        name = {'bylaws': 'Bylaws', 'constitution': 'Constitution', 'appendix': 'Appendix'}.get(doc, doc)
        if sec:
            return f'{name} {art} §{sec}'
        return f'{name} Art. {art}'

    def title_of(self, doc, art, sec=''):
        return self.sections.get((doc, art, sec), '') if sec else self.articles.get((doc, art), '')


def anchor_for(doc, art, sec=''):
    return f'{doc}-art-{art}-sec-{sec}' if sec else f'{doc}-art-{art}'


def check(structure):
    findings = []
    for doc, label, art, sec, content in structure.contents:
        source = structure.describe(doc, art, sec)
        for ref in find_references(content, doc):
            if not structure.exists(ref):
                what = structure.describe(ref.doc, ref.art, ref.sec)
                findings.append(Finding(
                    'error', source, ref.text.strip(),
                    f'Points to {what}, which does not exist.',
                    source_anchor=anchor_for(doc, art, sec),
                ))
                continue
            # Topic check: the clause before the reference names a title that
            # belongs elsewhere.
            clause_start = max(content.rfind('.', 0, ref.start), content.rfind('\n', 0, ref.start),
                               content.rfind(';', 0, ref.start)) + 1
            context = content[clause_start:ref.start + len(ref.text) + 40].lower()
            target_titles = (structure.title_of(ref.doc, ref.art) + ' ' +
                             structure.title_of(ref.doc, ref.art, ref.sec)).lower()
            for topic, locs in structure.topics.items():
                if topic in context and topic not in target_titles:
                    target_loc = (ref.doc, ref.art, ref.sec)
                    if any(l[:2] == target_loc[:2] for l in locs):
                        continue          # topic lives in the same article
                    where = sorted(structure.describe(*l) for l in locs)
                    findings.append(Finding(
                        'warning', source, ref.text.strip(),
                        f'The sentence mentions "{topic}", which is the title of {", ".join(where)}, '
                        f'but the reference points to {structure.describe(*target_loc)} '
                        f'("{structure.title_of(*target_loc) or structure.title_of(ref.doc, ref.art)}"). '
                        f'Heuristic — may be intentional.',
                        source_anchor=anchor_for(doc, art, sec),
                        suggestions=where,
                    ))
                    break
    return findings


def link_references(text, source_doc_type, structure=None):
    """Escaped HTML; resolvable references become links to their section anchor."""
    text = text or ''
    refs = find_references(text, source_doc_type)
    if not refs:
        return conditional_escape(text)
    parts, pos = [], 0
    for ref in refs:
        parts.append(conditional_escape(text[pos : ref.start]))
        label = text[ref.start:ref.end]
        if structure is None or structure.exists(ref):
            parts.append(
                f'<a href="#{anchor_for(ref.doc, ref.art, ref.sec)}" '
                f'class="text-primary-600 dark:text-primary-400 underline decoration-dotted hover:decoration-solid">'
                f'{conditional_escape(label)}</a>'
            )
        else:
            parts.append(conditional_escape(label))
        pos = ref.end
    parts.append(conditional_escape(text[pos:]))
    return mark_safe("".join(parts))  # nosec  # B308,B703: every segment passed through conditional_escape above
