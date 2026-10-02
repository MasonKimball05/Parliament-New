"""
Structural changes to the Constitution & Bylaws: new articles and sections,
with renumbering, and renames (v3.43.0, 10-02-26).

Mason: "if someone wants to put in a new article 4 it moves the existing
article 4 to 5, 5 to 6, etc." and "a way to edit the name of articles +
sections too."

A resolution proposes these as `ResolutionStructureChange` rows. This module:

  * says what a proposal WILL do, in words, before anything changes
    (`describe`) — shown on the edit page, the resolution page and the print
    preview, so the chapter votes on "new Article IV; current IV–X become
    V–XI", not on a surprise;
  * lists the cross-references the renumbering will leave pointing at the
    wrong place (`references_to_check`). It does NOT rewrite them: changing
    governing text is the chapter's job (same stance as `src/cnb_crossrefs`);
  * applies everything when the resolution passes (`apply_structure_changes`).

Numbering rules. Articles are Roman numerals, sections are whole numbers.
Inserting takes the number of the item it goes in front of, and every later
item in that document / article moves up one. An item whose number is not in
that form (a section "1a") cannot be shifted, so an insert in front of one is
refused when it is drafted, not discovered when the resolution passes.
Switched-off (deleted) articles and sections keep their place in the
sequence and are renumbered with the rest.
"""
from django.db import transaction

_ROMAN = (
    (1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),
    (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'),
)


class StructureError(Exception):
    """A proposal that cannot be made or applied. The message is for the user."""


def int_to_roman(n):
    out = ''
    for value, numeral in _ROMAN:
        while n >= value:
            out += numeral
            n -= value
    return out


def roman_to_int(text):
    """'IV' → 4. None if `text` is not a well-formed Roman numeral."""
    text = (text or '').strip().upper()
    if not text:
        return None
    total, i = 0, 0
    for value, numeral in _ROMAN:
        while text[i:i + len(numeral)] == numeral:
            total += value
            i += len(numeral)
    return total if i == len(text) and int_to_roman(total) == text else None


def section_to_int(text):
    text = (text or '').strip()
    return int(text) if text.isdigit() else None


def _ordered_articles(document):
    return list(document.articles.order_by('display_order', 'pk'))


def _ordered_sections(article):
    return list(article.sections.order_by('display_order', 'pk'))


def _doc_label(document):
    return document.get_doc_type_display()


# ── Planning (no writes) ─────────────────────────────────────────────────────

def plan_article_insert(document, before_article):
    """
    (new_number, [(article, old_number, new_number), ...]) for putting a new
    article in front of `before_article` (None = at the end).
    """
    articles = _ordered_articles(document)
    if before_article is None:
        numbers = [roman_to_int(a.number) for a in articles]
        if any(n is None for n in numbers):
            raise StructureError(
                f'The {_doc_label(document)} has an article whose number is not a Roman numeral, '
                'so the next number cannot be worked out.')
        return int_to_roman(max(numbers, default=0) + 1), []
    if before_article.document_id != document.pk:
        raise StructureError('That article is in a different document.')
    start = next(i for i, a in enumerate(articles) if a.pk == before_article.pk)
    shifts = []
    for article in articles[start:]:
        n = roman_to_int(article.number)
        if n is None:
            raise StructureError(
                f'Article "{article.number}" of the {_doc_label(document)} is not a Roman numeral and '
                'cannot be renumbered automatically. Add the new article after it, or renumber by hand first.')
        shifts.append((article, article.number, int_to_roman(n + 1)))
    # From the fresh row, not `before_article`: when one resolution inserts two
    # articles, the caller's copy of the second target predates the first shift.
    return articles[start].number, shifts


def plan_section_insert(article, before_section):
    """Same as `plan_article_insert`, for a section inside `article`."""
    sections = _ordered_sections(article)
    if before_section is None:
        numbers = [section_to_int(s.number) for s in sections]
        if any(n is None for n in numbers):
            raise StructureError(
                f'Article {article.number} has a section whose number is not a whole number, '
                'so the next number cannot be worked out.')
        return str(max(numbers, default=0) + 1), []
    if before_section.article_id != article.pk:
        raise StructureError('That section is in a different article.')
    start = next(i for i, s in enumerate(sections) if s.pk == before_section.pk)
    shifts = []
    for section in sections[start:]:
        n = section_to_int(section.number)
        if n is None:
            raise StructureError(
                f'§ {section.number} of Article {article.number} is not a whole number and cannot be '
                'renumbered automatically. Add the new section after it, or renumber by hand first.')
        shifts.append((section, section.number, str(n + 1)))
    return sections[start].number, shifts


def _range_text(shifts, prefix):
    if not shifts:
        return ''
    first_old, last_old = shifts[0][1], shifts[-1][1]
    first_new, last_new = shifts[0][2], shifts[-1][2]
    if len(shifts) == 1:
        return f'Current {prefix} {first_old} becomes {prefix} {first_new}.'
    plural = {'Article': 'Articles', '§': '§§'}[prefix]
    return f'Current {plural} {first_old}–{last_old} become {first_new}–{last_new}.'


def describe(change):
    """
    A dict the templates render: `heading`, `detail`, `renumbering`, `error`.
    For an applied change it reports what happened (`result_label`); for a
    draft, what will happen against the document as it is right now.
    """
    kind = change.kind
    out = {'heading': '', 'detail': '', 'renumbering': '', 'error': ''}
    try:
        if kind == 'new_article':
            doc = _doc_label(change.document)
            if change.applied:
                out['heading'] = f'New article: {change.result_label} — {change.title}'
            else:
                number, shifts = plan_article_insert(change.document, change.before_article)
                out['heading'] = f'New article: {doc} Article {number} — {change.title}'
                out['renumbering'] = _range_text(shifts, 'Article')
                out['detail'] = 'Added at the end.' if change.before_article is None else ''
        elif kind == 'new_section':
            if change.applied:
                out['heading'] = f'New section: {change.result_label}' + (f' — {change.title}' if change.title else '')
            elif change.parent_id:
                parent = describe(change.parent)
                out['heading'] = 'New section' + (f' — {change.title}' if change.title else '')
                out['detail'] = f'In the new article above ({parent["heading"].split(": ", 1)[-1]}).'
            else:
                article = change.article
                number, shifts = plan_section_insert(article, change.before_section)
                doc = _doc_label(article.document)
                # The article's title too: its NUMBER may be about to change in
                # this same resolution (a new article in front of it), and
                # "Art. III" next to "New article: Article III" misleads.
                where = f'{doc} Art. {article.number}' + (f' ({article.title})' if article.title else '')
                out['heading'] = (f'New section: {where} § {number}'
                                  + (f' — {change.title}' if change.title else ''))
                out['renumbering'] = _range_text(shifts, '§')
                out['detail'] = 'Added at the end of the article.' if change.before_section is None else ''
        elif kind == 'rename_article':
            article = change.article
            label = change.result_label or f'{_doc_label(article.document)} Article {article.number}'
            out['heading'] = f'Rename {label}'
            out['detail'] = f'“{change.old_title}” becomes “{change.title}”.'
            now = f'{_doc_label(article.document)} Article {article.number}'
            if change.applied and now != label:
                out['detail'] += f' It is now {now}.'
        elif kind == 'rename_section':
            label = change.result_label or change.section.full_identifier
            out['heading'] = f'Rename {label}'
            old = f'“{change.old_title}”' if change.old_title else 'no title'
            out['detail'] = f'{old} becomes “{change.title}”.'
            if change.applied and change.section.full_identifier != label:
                out['detail'] += f' It is now {change.section.full_identifier}.'
    except StructureError as e:
        out['heading'] = out['heading'] or change.get_kind_display()
        out['error'] = str(e)
    return out


def references_to_check(change):
    """
    Cross-references in the live text that this change's renumbering will
    leave pointing at the wrong place: [{'source', 'text'}, ...]. Empty for a
    rename, an append, or an applied change (the cross-reference checker on
    the C&B manager covers the document as it now is).
    """
    from src.cnb_crossrefs import find_references
    from src.models import Section

    if change.applied or change.kind not in ('new_article', 'new_section'):
        return []
    try:
        if change.kind == 'new_article':
            if change.before_article is None:
                return []
            doc_type = change.document.doc_type
            floor = roman_to_int(change.before_article.number)
            art_number = None
        else:
            if change.parent_id or change.before_section is None:
                return []
            doc_type = change.article.document.doc_type
            art_number = change.article.number
            floor = section_to_int(change.before_section.number)
    except AttributeError:
        return []
    if floor is None:
        return []

    found = []
    sections = (Section.objects.filter(is_active=True)
                .select_related('article__document').order_by('article__document__display_order',
                                                               'article__display_order', 'display_order'))
    for section in sections:
        for ref in find_references(section.content, section.article.document.doc_type):
            if ref.doc != doc_type:
                continue
            if change.kind == 'new_article':
                n = roman_to_int(ref.art)
                hit = n is not None and n >= floor
            else:
                n = section_to_int(ref.sec)
                hit = ref.art == art_number and n is not None and n >= floor
            if hit:
                found.append({'source': section.full_identifier, 'text': ref.text or section.content[ref.start:ref.end]})
    return found


# ── Applying (the resolution passed) ─────────────────────────────────────────

def _insert_article(document, before_article, title):
    from src.models import Article
    number, shifts = plan_article_insert(document, before_article)
    articles = _ordered_articles(document)
    position = len(articles) if before_article is None else next(
        i for i, a in enumerate(articles) if a.pk == before_article.pk)
    # Highest first: (document, number) is unique, so IV→V must wait for V→VI.
    for article, _old, new in reversed(shifts):
        article.number = new
        article.save(update_fields=['number'])
    new_article = Article.objects.create(document=document, number=number, title=title, display_order=0)
    articles.insert(position, new_article)
    for order, article in enumerate(articles, start=1):
        if article.display_order != order:
            article.display_order = order
            article.save(update_fields=['display_order'])
    return new_article


def _insert_section(article, before_section, title, content):
    from src.models import Section
    number, shifts = plan_section_insert(article, before_section)
    sections = _ordered_sections(article)
    position = len(sections) if before_section is None else next(
        i for i, s in enumerate(sections) if s.pk == before_section.pk)
    for section, _old, new in reversed(shifts):
        section.number = new
        section.save(update_fields=['number'])
    new_section = Section.objects.create(article=article, number=number, title=title, content=content, display_order=0)
    sections.insert(position, new_section)
    for order, section in enumerate(sections, start=1):
        if section.display_order != order:
            section.display_order = order
            section.save(update_fields=['display_order'])
    return new_section


def apply_structure_changes(resolution, applied_by):
    """
    Apply every unapplied structural change of a resolution that has just
    passed. Call inside the same transaction as `apply_amendments`, AFTER it:
    amendments record the section identifiers the resolution was written
    with (`identifier_snapshot`) before anything here renumbers them.

    Order: renames, then new articles, then new sections (a new section may
    belong to an article created a moment ago). Returns the number applied.
    """
    changes = list(resolution.structure_changes.filter(applied=False)
                   .select_related('document', 'article__document', 'section__article__document',
                                   'before_article', 'before_section', 'parent'))
    by_kind = {k: [c for c in changes if c.kind == k]
               for k in ('rename_article', 'rename_section', 'new_article', 'new_section')}
    made = {}   # change.pk -> the Article it created

    with transaction.atomic():
        for change in by_kind['rename_article']:
            article = change.article
            change.result_label = f'{_doc_label(article.document)} Article {article.number}'
            article.title = change.title
            article.save(update_fields=['title'])
            change.result_article = article

        for change in by_kind['rename_section']:
            section = change.section
            change.result_label = section.full_identifier
            # A title is part of the governing text: keep what it replaced.
            section.record_revision('resolution', by=applied_by, resolution=resolution,
                                    note=f'Retitled: "{section.title}" → "{change.title}"')
            section.title = change.title
            section.save(update_fields=['title'])
            change.result_section = section

        for change in by_kind['new_article']:
            article = _insert_article(change.document, change.before_article, change.title)
            made[change.pk] = article
            change.result_article = article
            change.result_label = f'{_doc_label(change.document)} Article {article.number}'

        for change in by_kind['new_section']:
            article = made.get(change.parent_id) if change.parent_id else change.article
            if article is None:
                raise StructureError('A new section points at a new article that was not created.')
            article.refresh_from_db()
            section = _insert_section(article, change.before_section, change.title, change.content)
            change.result_section = section
            change.result_article = article
            change.result_label = section.full_identifier

        # A later insert can move an earlier result (two new articles in one
        # resolution), so labels are read back once everything is in place.
        for change in changes:
            if change.kind == 'new_article':
                change.result_article.refresh_from_db()
                change.result_label = (f'{_doc_label(change.document)} '
                                       f'Article {change.result_article.number}')
            elif change.kind == 'new_section':
                change.result_section.refresh_from_db()
                change.result_section.article.refresh_from_db()
                change.result_label = change.result_section.full_identifier
            change.applied = True
            change.save(update_fields=['applied', 'result_label', 'result_article', 'result_section'])
    return len(changes)


# ── Removing a section (v3.44.0) ─────────────────────────────────────────────

def plan_removal(section):
    """
    [(section, old_number, new_number), ...] for the sections that move up when
    `section` is struck. None if the gap cannot be closed (a number in the way
    is not a whole number) — the section is then only switched off, as before.
    """
    sections = _ordered_sections(section.article)
    start = next((i for i, s in enumerate(sections) if s.pk == section.pk), None)
    if start is None or section_to_int(section.number) is None:
        return None
    shifts = []
    for later in sections[start + 1:]:
        n = section_to_int(later.number)
        if n is None:
            return None
        shifts.append((later, later.number, str(n - 1)))
    return shifts


def describe_removal(section):
    shifts = plan_removal(section)
    if shifts is None:
        return 'The sections after it keep their numbers (one of them is not a whole number), so a gap is left.'
    if not shifts:
        return 'It is the last section of its article, so nothing is renumbered.'
    return _range_text(shifts, '§').replace(' becomes ', ' moves up to ').replace(' become ', ' move up to ')


def close_gap(section):
    """
    Take a struck section out of the document and move the later sections of
    its article up one. Called by `Resolution.apply_amendments` after the
    section has been cleared and switched off. If the numbers after it cannot
    be shifted, nothing here happens and the section stays as a switched-off
    entry (the pre-v3.44.0 behaviour).
    """
    from django.utils import timezone
    shifts = plan_removal(section)
    if shifts is None:
        return False
    section.former_number = section.number
    section.number = f'x{section.pk}'          # frees the number; never shown
    section.removed_at = timezone.now()
    section.save(update_fields=['former_number', 'number', 'removed_at'])
    for later, _old, new in shifts:            # lowest first: 4→3 before 5→4
        later.number = new
        later.save(update_fields=['number'])
    return True


def structure_payload(documents):
    """
    Documents → articles → sections for the maker's dropdowns, as plain data
    (rendered with `json_script`). `documents` must have `articles__sections`
    prefetched.
    """
    payload = []
    for document in documents:
        articles = []
        for article in sorted(document.articles.all(), key=lambda a: (a.display_order, a.pk)):
            articles.append({
                'id': article.pk, 'number': article.number, 'title': article.title,
                'sections': [
                    {'id': s.pk, 'number': s.number, 'title': s.title}
                    for s in sorted(article.sections.all(), key=lambda s: (s.display_order, s.pk))
                ],
            })
        payload.append({'id': document.pk, 'label': _doc_label(document), 'articles': articles})
    return payload


__all__ = [
    'StructureError', 'apply_structure_changes', 'close_gap', 'describe', 'describe_removal', 'int_to_roman', 'plan_article_insert',
    'plan_section_insert', 'references_to_check', 'roman_to_int', 'structure_payload',
]
