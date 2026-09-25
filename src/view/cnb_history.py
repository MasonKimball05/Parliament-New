"""
C&B section history and "as of a date" (09-25-26).

  * `section_history` — every version of one section, newest first, each with
    what replaced it (a resolution, a direct edit, an import) and a word diff.
  * `apply_as_of(documents, date)` — used by `cnb_viewer` for `?as_of=YYYY-MM-DD`:
    rewrites the prefetched sections IN MEMORY (never saved) to the text in
    force at the end of that day, and hides sections created after it.

Readable by any member for documents members can see (`GoverningDocument.
enabled()`); CNB holders can also see disabled documents' history, matching
the manager. History of governing text is chapter record, not confidential.
"""
import datetime
import difflib
import re

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from src.models import GoverningDocument, Section

_TOKEN = re.compile(r'(\s+)')


def word_diff(old, new):
    """Escaped HTML: removed words in <del>, added words in <ins>."""
    a, b = _TOKEN.split(old or ''), _TOKEN.split(new or '')
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            out.append(conditional_escape(''.join(a[i1:i2])))
            continue
        if i2 > i1:
            out.append('<del class="bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300 line-through">'
                       f'{conditional_escape("".join(a[i1:i2]))}</del>')
        if j2 > j1:
            out.append('<ins class="bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300 no-underline">'
                       f'{conditional_escape("".join(b[j1:j2]))}</ins>')
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
                later = [r for r in section.revisions.all() if r.replaced_at > as_of]
                if later:
                    r = min(later, key=lambda r: (r.replaced_at, r.pk))
                    section.content, section.title, section.is_active = r.content, r.title, r.was_active
                    section.as_of_changed = True


@login_required
def section_history(request, section_id):
    section = get_object_or_404(
        Section.objects.select_related('article__document'), pk=section_id,
    )
    doc = section.article.document
    if not request.user.has_cnb_permission and not GoverningDocument.enabled().filter(pk=doc.pk).exists():
        raise Http404
    revisions = list(section.revisions.select_related('resolution', 'replaced_by'))
    # Pair each past version with the one that replaced it (newer).
    versions = []
    newer_content = section.content
    for r in revisions:                       # newest first
        versions.append({'rev': r, 'diff': word_diff(r.content, newer_content)})
        newer_content = r.content
    return render(request, 'cnb/section_history.html', {
        'section': section,
        'document': doc,
        'versions': versions,
        'anchor': f'{doc.doc_type}-art-{section.article.number}-sec-{section.number}',
    })
