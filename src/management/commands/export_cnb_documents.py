"""
Export the Constitution & Bylaws as it stands in the database, in the JSON
format `seed_cnb_documents --source` imports (multi-chapter phase 2, 09-27-26).

Uses:
  * starting a new chapter: export an existing chapter's structure, replace the
    text with your own, then `seed_cnb_documents --source <file>` on the new
    deployment (or save it as chapter_content/<chapter>/cnb.json);
  * a plain-text copy of the governing documents that is not a database dump.

The output is exactly the database's CURRENT text — amendments applied by
resolutions included — and it round-trips: importing it into an empty
database reproduces the same documents, articles and sections. Deactivated
sections are included with `"is_active": false`.

Usage:
    python manage.py export_cnb_documents                   # JSON to stdout
    python manage.py export_cnb_documents -o cnb.json
"""
import json

from django.core.management.base import BaseCommand, CommandError

from src.chapter_content import validate_cnb_documents
from src.models import GoverningDocument


def export_documents():
    docs = []
    qs = GoverningDocument.objects.prefetch_related('articles__sections').order_by('display_order', 'pk')
    for doc in qs:
        d = {
            'doc_type': doc.doc_type,
            'title': doc.title,
            'display_order': doc.display_order,
            'amendment_protection_weeks': doc.amendment_protection_weeks,
            'preamble': doc.preamble or '',
            'articles': [],
        }
        for article in sorted(doc.articles.all(), key=lambda a: (a.display_order, a.pk)):
            a = {'number': article.number, 'title': article.title, 'sections': []}
            for section in sorted(article.sections.all(), key=lambda s: (s.display_order, s.pk)):
                s = {'number': section.number, 'title': section.title or '', 'content': section.content}
                if not section.is_active:
                    s['is_active'] = False
                a['sections'].append(s)
            d['articles'].append(a)
        docs.append(d)
    return docs


class Command(BaseCommand):
    help = 'Export the Constitution & Bylaws from the database as importable JSON'

    def add_arguments(self, parser):
        parser.add_argument('-o', '--output', default='', help='Write to this file instead of stdout')

    def handle(self, *args, **options):
        docs = export_documents()
        if not docs:
            raise CommandError('No governing documents in the database — nothing to export.')
        # Refuse to write a file the importer would reject (e.g. a section
        # whose content was blanked in the admin).
        validate_cnb_documents(docs, label='database export')
        text = json.dumps({'documents': docs}, ensure_ascii=False, indent=2) + '\n'
        if options['output']:
            with open(options['output'], 'w', encoding='utf-8') as f:
                f.write(text)
            n = sum(len(a['sections']) for d in docs for a in d['articles'])
            self.stderr.write(self.style.SUCCESS(
                f"Wrote {len(docs)} document(s), {n} section(s) to {options['output']}"))
        else:
            self.stdout.write(text, ending='')
