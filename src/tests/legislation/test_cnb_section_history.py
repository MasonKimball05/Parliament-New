"""
09-25-26 — C&B section history and "as of a date".

Pins: every way a section's text changes records the outgoing text; the history
page shows it with a diff; `?as_of=` rebuilds the past text in memory without
saving; members can't read the history of a disabled document.

Run with: python manage.py test src.tests.legislation.test_cnb_section_history
"""
import datetime
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment,
                        Section, SectionRevision)


def _sec(doc, art, num):
    return Section.objects.get(article__document__doc_type=doc, article__number=art, number=num)


class SectionHistoryTests(TestCase):
    def setUp(self):
        call_command('seed_cnb_documents', stdout=StringIO())
        self.chair = ParliamentUser.objects.create(user_id='SH-1', username='sh1', name='CNB Chair',
                                                   member_type='Officer', member_status='Active', is_admin=True)
        self.member = ParliamentUser.objects.create(user_id='SH-2', username='sh2', name='Member',
                                                    member_type='Member', member_status='Active')
        self.section = _sec('bylaws', 'IV', '2')
        self.original = self.section.content

    def _c(self, u):
        c = Client(); c.force_login(u); return c

    def test_a_passed_resolution_records_the_outgoing_text(self):
        res = Resolution.objects.create(title='Budget reform', created_by=self.chair)
        ResolutionAmendment.objects.create(resolution=res, section=self.section,
                                           proposed_text='NEW BUDGET TEXT')
        res.apply_amendments(applied_by=self.chair)
        rev = SectionRevision.objects.get(section=self.section)
        self.assertEqual((rev.content, rev.source, rev.resolution), (self.original, 'resolution', res))
        self.section.refresh_from_db()
        self.assertEqual(self.section.content, 'NEW BUDGET TEXT')

    def test_a_direct_edit_records_the_outgoing_text_and_who(self):
        self._c(self.chair).post(reverse('cnb_edit_section', args=[self.section.pk]),
                                 {'content': 'EDITED', 'title': self.section.title})
        rev = SectionRevision.objects.get(section=self.section)
        self.assertEqual((rev.content, rev.source, rev.replaced_by), (self.original, 'direct_edit', self.chair))

    def test_an_unchanged_save_records_nothing(self):
        self._c(self.chair).post(reverse('cnb_edit_section', args=[self.section.pk]),
                                 {'content': self.section.content, 'title': self.section.title})
        self.assertFalse(SectionRevision.objects.exists())

    def test_a_forced_import_records_the_outgoing_text(self):
        self.section.content = 'LOCAL WORDING'
        self.section.save()
        call_command('seed_cnb_documents', '--force', '--only', 'bylaws:IV:2', stdout=StringIO())
        self.assertEqual(SectionRevision.objects.get(section=self.section).content, 'LOCAL WORDING')

    def test_the_history_page_shows_versions_and_an_escaped_diff(self):
        SectionRevision.objects.create(section=self.section, content='old <b>words</b> here',
                                       replaced_at=timezone.now(), source='direct_edit')
        r = self._c(self.member).get(reverse('cnb_section_history', args=[self.section.pk]))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('In force until', html)
        self.assertIn('<del', html)
        self.assertIn('&lt;b&gt;words&lt;/b&gt;', html)
        self.assertNotIn('<b>words</b>', html)

    def test_as_of_shows_the_past_text_without_saving_it(self):
        # Seeded in this test DB "today"; real sections predate tracking (NULL).
        Section.objects.filter(pk=self.section.pk).update(created_at=None)
        SectionRevision.objects.create(section=self.section, content='LAST YEAR TEXT',
                                       replaced_at=timezone.now() - datetime.timedelta(days=30),
                                       source='resolution')
        c = self._c(self.member)
        past = (timezone.localdate() - datetime.timedelta(days=60)).isoformat()
        html = c.get(reverse('constitution_bylaws'), {'as_of': past}).content.decode()
        self.assertIn('LAST YEAR TEXT', html)
        self.assertIn('Historical view', html)
        self.section.refresh_from_db()
        self.assertEqual(self.section.content, self.original)       # nothing saved
        html_now = c.get(reverse('constitution_bylaws')).content.decode()
        self.assertNotIn('LAST YEAR TEXT', html_now)

    def test_as_of_hides_sections_created_later(self):
        sweethearts = _sec('bylaws', 'III', '5')
        past = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        html = self._c(self.member).get(reverse('constitution_bylaws'), {'as_of': past}).content.decode()
        self.assertNotIn(f'id="bylaws-art-III-sec-5"', html)
        self.assertIsNotNone(sweethearts.created_at)

    def test_a_bad_or_future_as_of_is_ignored(self):
        future = (timezone.localdate() + datetime.timedelta(days=5)).isoformat()
        for v in ('not-a-date', future):
            html = self._c(self.member).get(reverse('constitution_bylaws'), {'as_of': v}).content.decode()
            self.assertNotIn('Historical view', html)

    def test_members_cannot_read_history_of_a_disabled_document(self):
        foreword_or_bylaws = self.section
        from src.models import FeatureFlag
        FeatureFlag.objects.update_or_create(name='cnb_bylaws', defaults={'is_enabled': False})
        from django.core.cache import cache
        cache.clear()
        r = self._c(self.member).get(reverse('cnb_section_history', args=[foreword_or_bylaws.pk]))
        self.assertEqual(r.status_code, 404)
        r = self._c(self.chair).get(reverse('cnb_section_history', args=[foreword_or_bylaws.pk]))
        self.assertEqual(r.status_code, 200)
