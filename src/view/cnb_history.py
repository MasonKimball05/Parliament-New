"""
C&B section history and "as of a date" (09-25-26).

  * `section_history` — every version of one section, newest first, each with
    what replaced it (a resolution, a direct edit, an import) and a word diff.
  * `apply_as_of(documents, date)` — used by `cnb_viewer` for `?as_of=YYYY-MM-DD`:
    rewrites the prefetched sections IN MEMORY (never saved) to the text in
    force at the end of that day, and hides sections created after it.
    `documents_as_of` loads and applies it, for the page and the app.

The app (src/mobile/views_cnb.py) uses the same helpers: `history_section_or_404`,
`section_versions` (the diff as `word_diff_segments`, which the page turns
into HTML with `segments_html`) and `documents_as_of`.

Readable by any member for documents members can see (`GoverningDocument.
enabled()`); CNB holders can also see disabled documents' history, matching
the manager. History of governing text is chapter record, not confidential.
"""
import datetime
import difflib
import re

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from src.models import GoverningDocument, Section

_TOKEN = re.compile(r'(\s+)')


def word_diff_segments(old, new):
    """
    The word diff as ``[(op, text)]``, op one of ``'equal'``, ``'delete'``,
    ``'insert'`` (a replace is its delete then its insert), never an empty
    text. Shared by the page's HTML (``word_diff``) and the app.
    """
    a, b = _TOKEN.split(old or ''), _TOKEN.split(new or '')
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            text = ''.join(a[i1:i2])
            if text:
                out.append(('equal', text))
            continue
        # Test the joined text, not the token count: `_TOKEN.split('')` is [''],
        # and a leading/trailing space adds a '' edge token, so a replace can
        # carry a zero-length side that would render as an empty <del>/<ins>.
        removed, added = ''.join(a[i1:i2]), ''.join(b[j1:j2])
        if removed:
            out.append(('delete', removed))
        if added:
            out.append(('insert', added))
    return out


def word_diff(old, new):
    """Escaped HTML: removed words in <del>, added words in <ins>."""
    return segments_html(word_diff_segments(old, new))


def segments_html(segments):
    """``word_diff_segments`` as the page's escaped HTML."""
    out = []
    for op, text in segments:
        if op == 'equal':
            out.append(conditional_escape(text))
        elif op == 'delete':
            out.append('<del class="bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300 line-through">'
                       f'{conditional_escape(text)}</del>')
        else:
            out.append('<ins class="bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300 no-underline">'
                       f'{conditional_escape(text)}</ins>')
    return mark_safe(''.join(out))  # nosec  # B308,B703: every text segment passes through conditional_escape


def parse_as_of(value):
    """'YYYY-MM-DD' → aware datetime at the END of that day (local), or None."""
    try:
        d = datetime.date.fromisoformat((value or '').strip())
    except ValueError:
        return None
    if d > timezone.localdate():
        return None
    return timezone.make_aware(datetime.datetime.combine(d, datetime.time.max))


def apply_as_of(documents, as_of):
    """
    Mutate prefetched documents (articles__sections__revisions) to their state
    at `as_of`. The earliest revision replaced AFTER `as_of` holds the text that
    was in force then; with none, the current text was.
    """
    for doc in documents:
        for article in doc.articles.all():
            for section in article.sections.all():
                if section.created_at and section.created_at > as_of:
                    section.as_of_hidden = True
                    continue
                # v3.44.0 — struck by a resolution: gone if that had already
                # happened by `as_of`, otherwise shown under the number it had.
                if section.removed_at:
                    if section.removed_at <= as_of:
                        section.as_of_hidden = True
                        continue
                    section.number = section.former_number
                later = [r for r in section.revisions.all() if r.replaced_at > as_of]
                if later:
                    r = min(later, key=lambda r: (r.replaced_at, r.pk))
                    section.content, section.title, section.is_active = r.content, r.title, r.was_active
                    section.as_of_changed = True


def documents_as_of(as_of=None):
    """
    The member-facing documents (``GoverningDocument.enabled()``) with their
    articles and sections prefetched, as ``cnb_viewer`` shows them; with
    ``as_of``, rewritten by ``apply_as_of`` (sections a later resolution struck
    included, so they can show under the date they were still in force).
    Shared with the app (src/mobile/views_cnb.py).
    """
    if as_of:
        # v3.44.0 — `all_objects`: a section a later resolution struck was
        # still part of the document on an earlier date (`apply_as_of` hides
        # it again when it was already gone by then).
        prefetch = Prefetch('articles__sections',
                            queryset=Section.all_objects.prefetch_related('revisions').order_by('article', 'display_order'))
    else:
        prefetch = 'articles__sections'
    documents = list(GoverningDocument.enabled().prefetch_related(prefetch))
    if as_of:
        apply_as_of(documents, as_of)
    return documents


def history_section_or_404(section_id, user):
    """The section (removed ones too) if ``user`` may read its history, else Http404."""
    section = get_object_or_404(
        Section.all_objects.select_related('article__document'), pk=section_id,   # v3.44.0: incl. removed
    )
    doc = section.article.document
    if not user.has_cnb_permission and not GoverningDocument.enabled().filter(pk=doc.pk).exists():
        raise Http404
    return section


def section_versions(section):
    """
    Every past version of ``section``, newest first, each paired with the text
    that replaced it: ``[{'rev': SectionRevision, 'segments': word_diff_segments(...)}]``.
    """
    revisions = list(section.revisions.select_related('resolution', 'replaced_by'))
    versions = []
    newer_content = section.content
    for r in revisions:                       # newest first
        versions.append({'rev': r, 'segments': word_diff_segments(r.content, newer_content)})
        newer_content = r.content
    return versions


def section_anchor(section):
    return f'{section.article.document.doc_type}-art-{section.article.number}-sec-{section.number}'


@login_required
def section_history(request, section_id):
    section = history_section_or_404(section_id, request.user)
    versions = [{'rev': v['rev'], 'diff': segments_html(v['segments'])} for v in section_versions(section)]
    return render(request, 'cnb/section_history.html', {
        'section': section,
        'document': section.article.document,
        'versions': versions,
        'anchor': section_anchor(section),
    })
