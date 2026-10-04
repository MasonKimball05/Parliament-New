"""
Appendix Section 3, "Definition of Common Terms" (v3.44.1, 10-02-26).

Mason: "section 3 of the appendix is missing." It was on the original static
C&B page but never made it into `cnb_data.py`. A plain re-run of the seed on a
database that already has the Appendix must add it and leave the rest alone.

Run with: python manage.py test src.tests.legislation.test_cnb_appendix_terms
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from src.models import Article, GoverningDocument, Section


class AppendixSectionThreeTests(TestCase):

    def _seed(self):
        call_command('seed_cnb_documents', stdout=StringIO(), stderr=StringIO())

    def test_seed_creates_it(self):
        self._seed()
        appendix = GoverningDocument.objects.get(doc_type='appendix')
        self.assertEqual(list(appendix.articles.order_by('display_order', 'pk').values_list('number', flat=True)),
                         ['1', '2', '3'])
        section = Section.objects.get(article__document=appendix, article__number='3')
        self.assertEqual(section.article.title, 'Definition of Common Terms')
        for term in ('Good Standing', 'Quorum', 'Supermajority', 'PNM: Potential New Member'):
            self.assertIn(term, section.content)

    def test_rerun_adds_it_to_an_existing_appendix_without_touching_edits(self):
        self._seed()
        appendix = GoverningDocument.objects.get(doc_type='appendix')
        Article.objects.filter(document=appendix, number='3').delete()
        edited = Section.objects.get(article__document=appendix, article__number='2')
        edited.content = 'Edited on the site.'
        edited.save()

        self._seed()

        self.assertTrue(Article.objects.filter(document=appendix, number='3').exists())
        edited.refresh_from_db()
        self.assertEqual(edited.content, 'Edited on the site.')
