"""
v4.4.x — legislation drafts in the app (src/mobile/views_legislation_drafts.py).

The app must keep the website's promises about a draft: only its author can
see, edit or delete it (another member's draft is a 404, not a 403); any member
can draft but only officers and chairs publish; publishing goes through the
same ``publish_draft`` as the web view and refuses a draft below the publish
floor; and the app, which sends text fields only, can never replace or clear
the document a draft was given on the website.

    python manage.py test src.tests.mobile.test_legislation_drafts
"""
import os
import shutil
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import Legislation, LegislationDraft
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict

API = '/api/mobile/v1/'
LONG = 'A description comfortably over the twenty character floor.'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        # The author is a PLAIN MEMBER and the officer is someone else, so
        # "can draft" and "can publish" are never the same person by accident
        # (the web tests' fixture note).
        self.author = make_user('drm-author')
        self.other = make_user('drm-other')
        self.officer = make_user('drm-officer', member_type='Officer')
        _, self.access, _ = make_device(self.author)
        _, self.other_access, _ = make_device(self.other)
        _, self.officer_access, _ = make_device(self.officer)
        self.api = Client()
        self.draft = LegislationDraft.objects.create(
            author=self.author, title='Dues restructuring', description=LONG,
            planned_available_at=timezone.now() + timedelta(days=14), notes='private scratch',
        )

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, body or {}, content_type='application/json',
                             **bearer(access or self.access))


class FormAndCreateTests(_Base):

    def test_form_lists_the_choices_and_who_can_publish(self):
        r = self.get('legislation/drafts/form/')
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual([m['value'] for m in data['vote_modes']], ['percentage', 'piecewise', 'plurality'])
        self.assertIn({'value': '100', 'label': 'Unanimous'}, data['thresholds'])
        self.assertEqual(data['defaults']['required_percentage'], '51')
        self.assertFalse(data['can_publish'])
        self.assertNotIn('document', data['help'])
        self.assertTrue(self.get('legislation/drafts/form/', self.officer_access).json()['can_publish'])

    def test_any_member_can_create_a_draft(self):
        r = self.post('legislation/drafts/', {
            'title': 'Parking reform', 'description': 'short', 'notes': 'n',
            'planned_available_at': '2026-12-01T19:00:00.000Z', 'vote_mode': 'percentage',
            'required_percentage': '67', 'anonymous_vote': True, 'allow_abstain': False,
        })
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()['draft']
        draft = LegislationDraft.objects.get(pk=d['id'])
        self.assertEqual(draft.author, self.author)
        self.assertEqual(draft.required_percentage, '67')
        self.assertTrue(draft.anonymous_vote)
        self.assertFalse(draft.allow_abstain)
        self.assertEqual(draft.planned_available_at.isoformat(), '2026-12-01T19:00:00+00:00')
        # Short description, no document: saved, but not ready.
        self.assertFalse(d['ready_to_publish'])
        self.assertIn('20 characters', d['not_ready_reason'])
        self.assertIsNone(d['document'])

    def test_validation_errors_are_invalid_input(self):
        for body in ({'title': ''}, {'title': 'x' * 201}, {'title': 'Ok', 'vote_mode': 'ranked'},
                     {'title': 'Ok', 'required_percentage': '99'},
                     {'title': 'Ok', 'planned_available_at': 'next tuesday'}):
            r = self.post('legislation/drafts/', body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), body)
        self.assertEqual(LegislationDraft.objects.count(), 1)

    def test_a_pledge_restriction_on_the_web_page_applies(self):
        pledge = make_user('drm-pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('create_legislation_draft')
        r = self.post('legislation/drafts/', {'title': 'Nope'}, access=access)
        self.assertEqual(r.status_code, 403, r.content)


class OwnershipTests(_Base):

    def test_the_author_reads_their_draft(self):
        r = self.get(f'legislation/drafts/{self.draft.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['notes'], 'private scratch')
        self.assertTrue(d['ready_to_publish'])
        self.assertFalse(d['can_publish'])

    def test_another_members_draft_is_404_everywhere(self):
        pk = self.draft.id
        self.assertEqual(self.get(f'legislation/drafts/{pk}/', self.other_access).status_code, 404)
        self.assertEqual(self.post(f'legislation/drafts/{pk}/', {'title': 'Hijacked'},
                                   self.other_access).status_code, 404)
        self.assertEqual(self.post(f'legislation/drafts/{pk}/delete/', access=self.other_access).status_code, 404)
        # An officer publishes their OWN drafts, not somebody else's.
        self.assertEqual(self.post(f'legislation/drafts/{pk}/publish/', access=self.officer_access).status_code,
                         404)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, 'Dues restructuring')
        self.assertFalse(self.draft.is_published)

    def test_edit_saves_and_a_missing_field_keeps_its_value(self):
        r = self.post(f'legislation/drafts/{self.draft.id}/', {'title': 'Dues restructuring v2'})
        self.assertEqual(r.status_code, 200, r.content)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, 'Dues restructuring v2')
        self.assertEqual(self.draft.description, LONG)
        self.assertEqual(self.draft.notes, 'private scratch')
        self.assertTrue(self.draft.allow_abstain)
        self.assertIsNotNone(self.draft.planned_available_at)

    def test_clearing_the_date_with_null(self):
        r = self.post(f'legislation/drafts/{self.draft.id}/', {'planned_available_at': None})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['draft']['not_ready_reason'], 'Set a date for when this becomes available.')

    def test_edit_validation_is_invalid_input(self):
        r = self.post(f'legislation/drafts/{self.draft.id}/', {'title': '  '})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_delete(self):
        r = self.post(f'legislation/drafts/{self.draft.id}/delete/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(LegislationDraft.objects.exists())


class DocumentTests(_Base):
    """The document is a website thing: the app links it and never changes it."""

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp(prefix='parliament-mobile-draft-test-')
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def test_editing_from_the_app_keeps_the_document(self):
        with override_settings(MEDIA_ROOT=self.media):
            self.draft.document.save('Dues Amendment.pdf', ContentFile(b'%PDF-1.4 pretend'), save=False)
            self.draft.document_original_name = 'Dues Amendment.pdf'
            self.draft.description = ''
            self.draft.save()
            name = self.draft.document.name

            d = self.get(f'legislation/drafts/{self.draft.id}/').json()
            self.assertEqual(d['document']['name'], 'Dues Amendment.pdf')
            self.assertEqual(d['document']['path'], f'/legislation/drafts/{self.draft.id}/document/')
            # The document counts toward the publish floor.
            self.assertTrue(d['ready_to_publish'])

            with self.captureOnCommitCallbacks(execute=True):
                r = self.post(f'legislation/drafts/{self.draft.id}/', {
                    'title': 'Renamed', 'description': '', 'document': None, 'document-clear': 'on',
                })
            self.assertEqual(r.status_code, 200, r.content)
            self.draft.refresh_from_db()
            self.assertEqual(self.draft.title, 'Renamed')
            self.assertEqual(self.draft.document.name, name)
            self.assertEqual(self.draft.document_original_name, 'Dues Amendment.pdf')
            self.assertTrue(os.path.isfile(os.path.join(self.media, name)))
            self.assertIsNotNone(r.json()['draft']['document'])


class PublishTests(_Base):

    def officer_draft(self, **kw):
        fields = dict(author=self.officer, title='Officer bill', description=LONG, notes='officer scratch',
                      planned_available_at=timezone.now() + timedelta(days=7), required_percentage='67')
        fields.update(kw)
        return LegislationDraft.objects.create(**fields)

    def test_a_member_cannot_publish_their_own_draft(self):
        r = self.post(f'legislation/drafts/{self.draft.id}/publish/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertFalse(Legislation.objects.exists())

    def test_an_officer_publishes_their_own_draft(self):
        draft = self.officer_draft()
        r = self.post(f'legislation/drafts/{draft.id}/publish/', access=self.officer_access)
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        bill = Legislation.objects.get()
        self.assertEqual(data['bill'], {'id': bill.id, 'title': 'Officer bill'})
        self.assertFalse(data['announced'])   # dated a week out
        self.assertIn('published', data['message'])
        self.assertTrue(data['draft']['published'])
        self.assertIsNone(data['draft']['links']['edit'])
        # The web publish's rules: posted by the publisher, voting manual-open,
        # notes never copied.
        self.assertEqual(bill.posted_by, self.officer)
        self.assertTrue(bill.voting_manual_open)
        self.assertEqual(bill.required_percentage, '67')
        self.assertNotIn('officer scratch', bill.description)
        draft.refresh_from_db()
        self.assertEqual(draft.published_legislation, bill)

    def test_not_ready_is_refused(self):
        thin = self.officer_draft(description='too short')
        r = self.post(f'legislation/drafts/{thin.id}/publish/', access=self.officer_access)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'not_ready'))
        self.assertIn('20 characters', r.json()['detail'])
        undated = self.officer_draft(planned_available_at=None)
        r = self.post(f'legislation/drafts/{undated.id}/publish/', access=self.officer_access)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'not_ready'))
        self.assertFalse(Legislation.objects.exists())

    def test_a_published_draft_is_read_only(self):
        draft = self.officer_draft()
        self.post(f'legislation/drafts/{draft.id}/publish/', access=self.officer_access)
        r = self.post(f'legislation/drafts/{draft.id}/publish/', access=self.officer_access)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'not_ready'))
        r = self.post(f'legislation/drafts/{draft.id}/', {'title': 'Changed'}, access=self.officer_access)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'already_published'))
        self.assertEqual(Legislation.objects.count(), 1)
        # Still readable (the notes stay the author's).
        d = self.get(f'legislation/drafts/{draft.id}/', self.officer_access).json()
        self.assertEqual(d['notes'], 'officer scratch')
        self.assertIsNotNone(d['links']['bill'])
