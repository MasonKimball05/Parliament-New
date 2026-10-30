"""
Mobile plan, Phase 3g — the Constitution & Bylaws reader.

The app must show what the website's Document and Resolutions tabs show:
only the documents whose flag is on, the same suspension and protection
notes, cross-references linked only when they resolve, and resolutions
read-only with an edit link only for those who could edit them on the web.

    python manage.py test src.tests.mobile.test_cnb
"""
import datetime

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from src.models import Article, GoverningDocument, Resolution, ResolutionAmendment, Section
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class CnbTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('cnb-1')
        _, self.access, _ = make_device(self.user)
        self.api = Client()
        for name in ('cnb_constitution', 'cnb_bylaws', 'cnb_appendix'):
            flag(name, True)
        flag('cnb_foreword', False)
        self.con = GoverningDocument.objects.create(doc_type='constitution', title='Constitution', display_order=10)
        self.bylaws = GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws', display_order=20)
        GoverningDocument.objects.create(doc_type='foreword', title='Foreword', preamble='Unpassed.')
        art = Article.objects.create(document=self.con, number='I', title='Name', display_order=1)
        Article.objects.create(document=self.con, number='II', title='Purpose', display_order=2)
        self.sec = Section.objects.create(
            article=art, number='1', title='Name', display_order=1,
            content='Αμ chapter. See Article II. Also Article IX, Section 4 of the Bylaws.')
        self.suspended = Section.objects.create(
            article=art, number='2', content='Old rule.', display_order=2, is_active=False,
            deactivation_reason='IFC ruling', amendment_protected=True,
            protected_until=datetime.date(2026, 12, 1), protection_note='Failed in R-3',
            partial_suspensions=[{'ref': '2.a', 'reason': 'Pending review', 'suspended_at': '2026-10-01'}])

    def get(self, path):
        return self.api.get(API + path, **bearer(self.access))

    def test_documents_follow_flags_and_carry_notes(self):
        r = self.get('cnb/')
        self.assertEqual(r.status_code, 200, r.content)
        docs = r.json()['documents']
        self.assertEqual([d['doc_type'] for d in docs], ['constitution', 'bylaws'])   # foreword's flag is off
        self.assertTrue(docs[1]['prose_only'])
        secs = docs[0]['articles'][0]['sections']
        self.assertEqual([s['number'] for s in secs], ['1', '2'])
        s2 = secs[1]
        self.assertFalse(s2['active'])
        self.assertEqual(s2['suspension_reason'], 'IFC ruling')
        self.assertEqual(s2['protected_until'], '2026-12-01')
        self.assertEqual(s2['partial_suspensions'][0]['ref'], '2.a')
        self.assertTrue(s2['history_path'].endswith(f'/section/{self.suspended.pk}/history/'))

    def test_only_resolving_references_are_linked_in_utf16_offsets(self):
        sec = self.get('cnb/').json()['documents'][0]['articles'][0]['sections'][0]
        # "Article II" exists; Bylaws IX § 4 does not, so it stays plain text.
        self.assertEqual(len(sec['refs']), 1)
        ref = sec['refs'][0]
        self.assertEqual(ref['anchor'], 'constitution-art-II')
        text = sec['content']
        # Greek letters are one UTF-16 unit each, so code-point offsets match here;
        # the slice must be the reference itself.
        self.assertEqual(text[ref['start']:ref['end']], 'Article II')

    def test_resolutions_list_and_detail(self):
        other = make_user('cnb-author')
        res = Resolution.objects.create(title='On Names', status='pending', authors='A. Brother',
                                        whereas_clauses='The name is old\n\nIt should change',
                                        resolved_text='The name be changed.', created_by=other)
        ResolutionAmendment.objects.create(resolution=res, section=self.sec, amendment_type='change',
                                           proposed_text='New text.')
        r = self.get('cnb/resolutions/')
        self.assertEqual(r.status_code, 200)
        row = r.json()['resolutions'][0]
        self.assertEqual((row['title'], row['status'], row['amendments']), ('On Names', 'pending', 1))

        d = self.get(f'cnb/resolutions/{res.pk}/').json()
        self.assertEqual(d['whereas'], ['The name is old', 'It should change'])
        self.assertEqual(d['resolved'][0]['prefix'], 'Therefore, be it resolved,')
        a = d['amendment_list'][0]
        self.assertEqual((a['section'], a['type_label'], a['proposed']), ('Constitution Art. I § 1', 'Change', 'New text.'))
        self.assertTrue(a['original'].startswith('Αμ chapter'))
        self.assertFalse(d['can_edit'])                                 # not the author, no CNB role

        res.created_by = self.user
        res.save()
        self.assertTrue(self.get(f'cnb/resolutions/{res.pk}/').json()['can_edit'])
        self.assertEqual(self.get('cnb/resolutions/999999/').status_code, 404)
