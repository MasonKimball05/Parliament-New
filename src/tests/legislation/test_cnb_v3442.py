"""
v3.44.2 (10-03-26) — three things the 10-03-26 auto-run review found in the
v3.43.0 / v3.44.0 resolution work:

  1. a proposed new section placed "in front of" a section that is then
     struck crashed every page that shows the proposal (StopIteration), and a
     resolution that did both could not be passed;
  2. a resolution's status could be posted from any status, so "passed" sent
     again re-applied old text over newer text;
  3. a text amendment to a section another resolution had struck was written
     into the hidden row and reported as applied.

Run with: python manage.py test src.tests.legislation.test_cnb_v3442
"""
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from src import cnb_structure
from src.models import Resolution, ResolutionAmendment, ResolutionStructureChange, Section
from src.tests.legislation.test_cnb_v344 import _Docs

PAGES = ('cnb_resolution_detail', 'cnb_edit_resolution', 'cnb_resolution_print',
         'cnb_resolution_document_preview')


class _Base(_Docs, TestCase):
    def setUp(self):
        self.build_docs()
        self.client.force_login(self.chair)

    def set_status(self, resolution, status):
        return self.client.post(reverse('cnb_set_status', args=[resolution.pk]), {'status': status})

    def pass_(self, resolution):
        Resolution.objects.filter(pk=resolution.pk).update(status='pending')
        return self.set_status(resolution, 'passed')

    def new_section_before(self, resolution, section, title='New'):
        return ResolutionStructureChange.objects.create(
            resolution=resolution, kind='new_section', article=section.article,
            before_section=section, title=title, content='New text.', added_by=self.chair)

    def live(self):
        return list(Section.objects.filter(article=self.arts[1]).order_by('display_order', 'pk')
                    .values_list('number', 'title'))


class NewSectionInFrontOfAStruckSectionTests(_Base):
    def test_strike_and_insert_in_the_same_resolution_passes(self):
        """Replace § 2: strike it and put a new section where it was."""
        self.strike(self.secs[1])
        self.new_section_before(self.resolution, self.secs[1], title='Replacement')
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name, args=[self.resolution.pk])).status_code, 200, name)
        self.pass_(self.resolution)
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.status, 'passed')
        self.assertEqual(self.live(), [('1', 'Sec 1'), ('2', 'Replacement'), ('3', 'Sec 3'), ('4', 'Sec 4')])

    def test_another_resolutions_proposal_follows_the_section_that_took_its_place(self):
        other = Resolution.objects.create(title='Other', created_by=self.chair)
        change = self.new_section_before(other, self.secs[2])
        self.strike(self.secs[2])
        self.pass_(self.resolution)
        change.refresh_from_db()
        self.assertEqual(change.before_section_id, self.secs[3].pk)
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name, args=[other.pk])).status_code, 200, name)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('cnb_resolution_detail', args=[other.pk])).status_code, 200)

    def test_striking_the_last_section_moves_the_proposal_to_the_end(self):
        other = Resolution.objects.create(title='Other', created_by=self.chair)
        change = self.new_section_before(other, self.secs[3])
        self.strike(self.secs[3])
        self.pass_(self.resolution)
        change.refresh_from_db()
        self.assertIsNone(change.before_section_id)
        self.pass_(other)
        self.assertEqual(self.live()[-1], ('4', 'New'))

    def test_a_row_already_stranded_is_an_error_message_not_a_crash(self):
        """A proposal left pointing at a removed section by v3.44.0/v3.44.1."""
        other = Resolution.objects.create(title='Other', created_by=self.chair)
        change = self.new_section_before(other, self.secs[2])
        self.strike(self.secs[2])
        self.pass_(self.resolution)
        ResolutionStructureChange.objects.filter(pk=change.pk).update(before_section=self.secs[2])
        change.refresh_from_db()
        self.assertIn('has been removed', cnb_structure.describe(change)['error'])
        for name in PAGES:
            self.assertEqual(self.client.get(reverse(name, args=[other.pk])).status_code, 200, name)
        self.pass_(other)
        other.refresh_from_db()
        self.assertEqual(other.status, 'pending')          # refused, nothing applied
        self.assertEqual(len(self.live()), 3)

    def test_the_preview_does_not_keep_the_re_pointing(self):
        change = self.new_section_before(self.resolution, self.secs[1])
        self.strike(self.secs[1])
        self.client.get(reverse('cnb_resolution_document_preview', args=[self.resolution.pk]))
        change.refresh_from_db()
        self.assertEqual(change.before_section_id, self.secs[1].pk)


class StatusTransitionTests(_Base):
    def amend(self, resolution, section, text):
        return ResolutionAmendment.objects.create(
            resolution=resolution, section=section, proposed_text=text,
            original_text_snapshot=section.content, amendment_type='change')

    def test_passed_again_does_not_reapply_old_text(self):
        section = self.secs[0]
        self.amend(self.resolution, section, 'First.')
        self.pass_(self.resolution)
        later = Resolution.objects.create(title='Later', created_by=self.chair)
        self.amend(later, section, 'Second.')
        self.pass_(later)
        response = self.set_status(self.resolution, 'passed')       # a stale tab
        section.refresh_from_db()
        self.assertEqual(section.content, 'Second.')
        self.assertTrue(any('Nothing was changed' in str(m) for m in get_messages(response.wsgi_request)))

    def test_final_statuses_are_final(self):
        self.amend(self.resolution, self.secs[0], 'First.')
        self.pass_(self.resolution)
        for status in ('failed', 'draft', 'pending', 'withdrawn'):
            self.set_status(self.resolution, status)
            self.resolution.refresh_from_db()
            self.assertEqual(self.resolution.status, 'passed', status)
        self.secs[0].refresh_from_db()
        self.assertFalse(self.secs[0].amendment_protected)

    def test_a_draft_cannot_be_passed_without_a_vote(self):
        self.set_status(self.resolution, 'passed')
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.status, 'draft')

    def test_the_offered_transitions_still_work(self):
        for path in (['pending', 'draft', 'pending', 'failed'], ['withdrawn'], ['pending', 'withdrawn'],
                     ['pending', 'passed']):
            resolution = Resolution.objects.create(title='R', created_by=self.chair)
            for status in path:
                self.set_status(resolution, status)
                resolution.refresh_from_db()
                self.assertEqual(resolution.status, status, path)


class AmendmentToAStruckSectionTests(_Base):
    def test_the_pass_is_refused_and_nothing_is_written(self):
        section = self.secs[2]
        other = Resolution.objects.create(title='Other', created_by=self.chair)
        ResolutionAmendment.objects.create(
            resolution=other, section=section, proposed_text='Changed.',
            original_text_snapshot=section.content, amendment_type='change')
        self.strike(section)
        self.pass_(self.resolution)
        response = self.pass_(other)
        other.refresh_from_db()
        self.assertEqual(other.status, 'pending')
        self.assertEqual(Section.all_objects.get(pk=section.pk).content, '')
        self.assertFalse(other.amendments.get().applied)
        self.assertTrue(any('no longer in the document' in str(m) for m in get_messages(response.wsgi_request)))
        self.assertEqual(self.client.get(reverse('cnb_resolution_detail', args=[other.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('cnb_resolution_document_preview', args=[other.pk])).status_code, 200)
