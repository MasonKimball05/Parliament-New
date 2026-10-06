"""
"How the document would read": a resolution's effect on the Constitution &
Bylaws, shown before it is voted on (v3.44.0, 10-02-26).

HOW, and why it is done this way. The preview does not re-implement what
passing a resolution does. It RUNS it — `Resolution.apply_amendments` and
`cnb_structure.apply_structure_changes`, the same two calls
`set_resolution_status` makes — inside a transaction, photographs the
documents before and after, and rolls the transaction back. Nothing is
saved. So the preview cannot drift from the real thing: a new kind of change
shows up here the day it is applied there.

The cost is that a GET takes row locks for a few milliseconds and writes that
are thrown away. At chapter size that is nothing; it would matter on a busy
table, and this is not one.
"""
from django.db import transaction
from django.utils.html import conditional_escape

from src.view.cnb_history import word_diff


def _lf(text):
    return (text or '').replace('\r\n', '\n').replace('\r', '\n')


def _snapshot(document_ids):
    from src.models import Article, Section
    articles = {}
    for a in Article.objects.filter(document_id__in=document_ids).order_by('document_id', 'display_order', 'pk'):
        articles[a.pk] = {
            'pk': a.pk, 'document_id': a.document_id, 'number': a.number, 'title': a.title,
            'order': a.display_order, 'is_active': a.is_active, 'sections': {},
        }
    for s in Section.all_objects.filter(article_id__in=list(articles)).order_by('display_order', 'pk'):
        articles[s.article_id]['sections'][s.pk] = {
            'pk': s.pk, 'number': s.former_number if s.removed_at else s.number, 'title': s.title,
            'content': _lf(s.content), 'order': s.display_order, 'is_active': s.is_active,
            'gone': bool(s.removed_at),
        }
    return articles


def affected_document_ids(resolution):
    ids = set(resolution.amendments.values_list('section__article__document_id', flat=True))
    for c in resolution.structure_changes.select_related('article', 'section__article', 'parent'):
        if c.document_id:
            ids.add(c.document_id)
        if c.article_id:
            ids.add(c.article.document_id)
        if c.section_id:
            ids.add(c.section.article.document_id)
        if c.parent_id and c.parent.document_id:
            ids.add(c.parent.document_id)
    return ids


def project(resolution, user, allowed_document_ids=None):
    """
    {'error': str, 'documents': [...]}: each document as it would read, with
    every article and section marked new / removed / changed / renumbered /
    retitled / unchanged. `allowed_document_ids` limits what is shown (members
    do not see switched-off documents).
    """
    from src.cnb_structure import StructureError, apply_structure_changes
    from src.models import GoverningDocument

    doc_ids = affected_document_ids(resolution)
    if allowed_document_ids is not None:
        doc_ids &= set(allowed_document_ids)
    error = ''
    with transaction.atomic():
        before = _snapshot(doc_ids)
        try:
            with transaction.atomic():
                resolution.apply_amendments(applied_by=user)
                apply_structure_changes(resolution, user)
        except StructureError as e:
            error = str(e)
        after = _snapshot(doc_ids)
        transaction.set_rollback(True)      # nothing above is kept

    documents = []
    for document in GoverningDocument.objects.filter(pk__in=doc_ids):
        articles = []
        for a in sorted((x for x in after.values() if x['document_id'] == document.pk), key=lambda x: (x['order'], x['pk'])):
            old = before.get(a['pk'])
            sections, touched = [], False
            for s in sorted(a['sections'].values(), key=lambda x: (x['order'], x['pk'])):
                was = old['sections'].get(s['pk']) if old else None
                row = {'number': s['number'], 'title': s['title'], 'old_number': '', 'old_title': None,
                       'html': conditional_escape(s['content']), 'status': 'unchanged'}
                struck = s['gone'] or (was and was['is_active'] and not s['is_active'] and not s['content'])
                if was is None:
                    row['status'] = 'new'
                elif struck and not was['gone']:
                    row.update(status='removed', number=was['number'], title=was['title'],
                               html=conditional_escape(was['content']))
                elif s['gone']:
                    continue                 # removed by an earlier resolution: not part of this story
                else:
                    if was['content'] != s['content']:
                        row.update(status='changed', html=word_diff(was['content'], s['content']))
                    if was['number'] != s['number']:
                        row['old_number'] = was['number']
                    if was['title'] != s['title']:
                        row['old_title'] = was['title']
                    if row['status'] == 'unchanged' and (row['old_number'] or row['old_title'] is not None):
                        row['status'] = 'moved'
                touched = touched or row['status'] != 'unchanged'
                sections.append(row)
            article = {
                'number': a['number'], 'title': a['title'], 'sections': sections, 'is_active': a['is_active'],
                'is_new': old is None,
                'old_number': old['number'] if old and old['number'] != a['number'] else '',
                'old_title': old['title'] if old and old['title'] != a['title'] else None,
            }
            article['touched'] = touched or article['is_new'] or bool(article['old_number']) or article['old_title'] is not None
            articles.append(article)
        documents.append({
            'label': document.get_doc_type_display(), 'title': document.title, 'articles': articles,
            'touched_count': sum(1 for x in articles if x['touched']),
        })
    return {'error': error, 'documents': documents}
