"""
Management command to seed the Constitution & Bylaws document structure.

Creates GoverningDocument, Article, and Section records from the data file at
src/management/data/cnb_data.py. Sections are seeded with PLACEHOLDER text —
after running, edit each section via the C&B Manager (/officers/cnb/) or Django
admin to enter the actual document text.

Usage:
    python manage.py seed_cnb_documents

Options:
    --update   Update existing records (title, display_order, preamble,
               protection_weeks) instead of skipping them. Never overwrites
               content that has already been edited — that means section content
               which is no longer the PLACEHOLDER string, and (v3.19.1) the
               preamble of a PROSE-ONLY document, whose preamble IS its content.
    --force    Overwrite content even if it has been edited. Use with caution.

v3.19.1 — the Foreword is the first prose-only document: no articles, whole text
in `preamble`. It is seeded ahead of the chapter vote and stays invisible to
members until the `cnb_foreword` feature flag is enabled. Seeding it does NOT
publish it; run `seed_feature_flags` too, then toggle the flag when it passes.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from src.management.data.cnb_data import DOCUMENTS
from src.models import GoverningDocument, Article, Section

PLACEHOLDER_PREFIX = 'PLACEHOLDER'


class Command(BaseCommand):
    help = 'Seed the Constitution & Bylaws document structure from cnb_data.py'

    def add_arguments(self, parser):
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update document/article metadata on existing records (safe — never overwrites edited section text)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite ALL section content, including sections that have been edited beyond the placeholder',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        update = options['update']
        force = options['force']

        self.stdout.write(self.style.MIGRATE_HEADING('Seeding Constitution & Bylaws documents...\n'))

        total_docs = total_articles = total_sections = 0
        skipped_docs = skipped_articles = skipped_sections = 0

        for doc_data in DOCUMENTS:
            doc_type = doc_data['doc_type']
            # v3.19.1: a document with no articles keeps its entire text in
            # `preamble` (the Foreword). That makes the preamble CONTENT for
            # such a document, not metadata — see the guard in the update
            # branch below.
            is_prose_only = not doc_data.get('articles')

            doc, created = GoverningDocument.objects.get_or_create(
                doc_type=doc_type,
                defaults={
                    'title': doc_data['title'],
                    'preamble': doc_data.get('preamble', ''),
                    'display_order': doc_data.get('display_order', 0),
                    'amendment_protection_weeks': doc_data.get('amendment_protection_weeks', 15),
                },
            )

            if created:
                total_docs += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ Created document: {doc.get_doc_type_display()}')
                )
            elif update:
                doc.title = doc_data['title']
                doc.display_order = doc_data.get('display_order', doc.display_order)
                doc.amendment_protection_weeks = doc_data.get(
                    'amendment_protection_weeks', doc.amendment_protection_weeks
                )

                # ⚠️ v3.19.1 — `--update` MUST NOT CLOBBER A PROSE-ONLY DOCUMENT.
                # This command's contract is that `--update` refreshes metadata
                # and never overwrites content someone has edited; `--force` is
                # the escape hatch. That contract was written when content only
                # ever lived in Section.content, and it silently stopped holding
                # the moment a document existed whose text is its preamble.
                # Editing the Foreword in the C&B manager and then running
                # `--update` — a command documented as safe — would have thrown
                # the edit away with no warning.
                if is_prose_only and not force:
                    if doc.preamble != doc_data.get('preamble', ''):
                        self.stdout.write(
                            self.style.WARNING(
                                f'    ! Kept edited text for {doc.get_doc_type_display()} '
                                f'(prose-only document; use --force to overwrite it)'
                            )
                        )
                else:
                    doc.preamble = doc_data.get('preamble', doc.preamble)

                doc.save()
                total_docs += 1
                self.stdout.write(
                    self.style.WARNING(f'  ~ Updated document: {doc.get_doc_type_display()}')
                )
            else:
                skipped_docs += 1
                self.stdout.write(
                    f'  - Skipped existing document: {doc.get_doc_type_display()} (use --update to refresh metadata)'
                )

            for order, article_data in enumerate(doc_data.get('articles', []), start=1):
                article, created = Article.objects.get_or_create(
                    document=doc,
                    number=article_data['number'],
                    defaults={
                        'title': article_data['title'],
                        'display_order': order,
                    },
                )

                if created:
                    total_articles += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'    ✓ Created Article {article.number}: {article.title}'
                        )
                    )
                elif update:
                    article.title = article_data['title']
                    article.display_order = order
                    article.save()
                    total_articles += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f'    ~ Updated Article {article.number}: {article.title}'
                        )
                    )
                else:
                    skipped_articles += 1

                for sec_order, section_data in enumerate(article_data.get('sections', []), start=1):
                    section, created = Section.objects.get_or_create(
                        article=article,
                        number=section_data['number'],
                        defaults={
                            'title': section_data.get('title', ''),
                            'content': section_data['content'],
                            'display_order': sec_order,
                        },
                    )

                    if created:
                        total_sections += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'      ✓ Created Section {section.number}: {section.title or "(no title)"}'
                            )
                        )
                    else:
                        content_is_placeholder = section.content.startswith(PLACEHOLDER_PREFIX)
                        if force or content_is_placeholder:
                            section.title = section_data.get('title', section.title)
                            section.display_order = sec_order
                            if force or content_is_placeholder:
                                section.content = section_data['content']
                            section.save()
                            total_sections += 1
                            marker = '(forced)' if (force and not content_is_placeholder) else ''
                            self.stdout.write(
                                self.style.WARNING(
                                    f'      ~ Updated Section {section.number}: {section.title or "(no title)"} {marker}'
                                )
                            )
                        else:
                            skipped_sections += 1

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(f'  Documents  — created/updated: {total_docs}, skipped: {skipped_docs}')
        self.stdout.write(f'  Articles   — created/updated: {total_articles}, skipped: {skipped_articles}')
        self.stdout.write(f'  Sections   — created/updated: {total_sections}, skipped: {skipped_sections}')
        self.stdout.write('')

        placeholder_count = Section.objects.filter(
            content__startswith=PLACEHOLDER_PREFIX
        ).count()

        if placeholder_count:
            self.stdout.write(
                self.style.WARNING(
                    f'  ⚠  {placeholder_count} section(s) still have PLACEHOLDER content.\n'
                    f'     Edit them at /officers/cnb/ or via Django admin.'
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS('  ✓ All sections have real content.'))
