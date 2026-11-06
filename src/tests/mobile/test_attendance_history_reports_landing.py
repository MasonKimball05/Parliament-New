"""
Three officer pages in the app, each through the web view's own code: a
committee's attendance history (``committee_attendance_history``), Upload
Report (``upload_report``) and Edit Landing Page (``edit_landing_page``). Each
class checks who may, what's refused, a success, and that the website page
still works through the shared helper.

    python manage.py test src.tests.mobile.test_attendance_history_reports_landing
"""
import json
from datetime import date, timedelta

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Attendance, Committee, CommitteeDocument, LandingPageContactTopic, LandingPageContent, LandingPageFormLink,
    LandingPagePhoto, LandingPageSocialLink,
)
from src.tests.mobile.sess_helpers import bearer, make_user
from src.tests.mobile.test_file_uploads import API, _Base, _html, _pdf, _png
from src.tests.mobile.test_home_and_notifications import toggle


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ── Attendance history ──────────────────────────────────────────────────────

class CommitteeAttendanceHistoryTests(_Base):

    def setUp(self):
        super().setUp()
        self.officer = make_user('ah-off', member_type='Officer')
        self.chair = make_user('ah-chair', member_type='Chair')
        self.member = make_user('ah-mem')
        self.c = Committee.objects.create(name='Finance', code='FIN', is_active=True)
        self.c.chairs.add(self.chair)
        self.c.members.add(self.chair, self.member)
        today = timezone.localdate()
        self.earlier = today - timedelta(days=7)
        for user, day, status in [(self.member, today, 'present'), (self.chair, today, 'present'),
                                  (self.member, self.earlier, 'present'), (self.chair, self.earlier, 'excused')]:
            Attendance.objects.create(committee=self.c, user=user, date=day, attendance_type='committee',
                                      status=status, marked_by=self.chair, marked_at=timezone.now())
        self.path = 'committees/FIN/attendance/history/'

    def test_history(self):
        r = self.get(self.officer, self.path)
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual(b['committee'], {'code': 'FIN', 'name': 'Finance'})
        self.assertEqual([s['date'] for s in b['sessions']],
                         [timezone.localdate().isoformat(), self.earlier.isoformat()])
        latest, older = b['sessions']
        self.assertEqual(latest['counts'], {'present': 2, 'absent': 0, 'excused': 0, 'late': 0})
        self.assertEqual(older['counts']['excused'], 1)
        row = next(r for r in older['records'] if r['user_id'] == self.chair.user_id)
        self.assertEqual((row['status'], row['status_label'], row['marked_by']),
                         ('excused', 'Excused', self.chair.name))
        totals = {m['user_id']: m for m in b['members']}
        self.assertEqual((totals[self.member.user_id]['present'], totals[self.member.user_id]['recorded']), (2, 2))
        self.assertEqual((totals[self.chair.user_id]['present'], totals[self.chair.user_id]['excused']), (1, 1))
        self.assertTrue(b['can_mark'])

    def test_who(self):
        # officer_required: officers and any chair; not members, not advisors.
        self.assertEqual(self.get(self.chair, self.path).status_code, 200)
        self.assertEqual(self.get(self.member, self.path).status_code, 403)
        self.assertEqual(self.get(make_user('ah-adv', member_type='Advisor'), self.path).status_code, 403)
        self.assertEqual(self.get(self.officer, 'committees/NOPE/attendance/history/').status_code, 404)
        # Today's attendance says whether the history opens.
        self.assertTrue(self.get(self.chair, 'committees/FIN/attendance/').json()['can_view_history'])

    def test_website(self):
        resp = self.web(self.officer).get(reverse('committee_attendance_history', args=['FIN']))
        self.assertEqual(resp.status_code, 200)
        days = resp.context['attendance_days']
        self.assertEqual([d['date'] for d in days], [timezone.localdate(), self.earlier])
        # 11-06-26: "Total Present" counts present records only (the earlier day has one excused).
        self.assertEqual([d['present_n'] for d in days], [2, 1])
        self.assertContains(resp, 'Excused')
        self.assertEqual(self.web(self.member).get(reverse('committee_attendance_history', args=['FIN'])).status_code,
                         403)


# ── Upload Report ───────────────────────────────────────────────────────────

class ReportUploadTests(_Base):

    def setUp(self):
        super().setUp()
        self.officer = make_user('ru-off', member_type='Officer')
        self.advisor = make_user('ru-adv', member_type='Advisor')
        self.c = Committee.objects.create(name='Finance', code='FIN', is_active=True)
        self.path = 'officer/reports/upload/'

    def test_form(self):
        r = self.get(self.officer, self.path)
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertIn({'id': self.c.id, 'name': 'Finance'}, b['committees'])
        self.assertIn({'value': 'report', 'label': 'Report'}, b['types'])
        self.assertIn('.xlsx', b['extensions'])
        self.assertEqual(b['max_bytes'], 20 * 1024 * 1024)

    def test_who(self):
        # officer_required: advisors and members can't.
        self.assertEqual(self.get(self.advisor, self.path).status_code, 403)
        self.assertEqual(self.upload(make_user('ru-mem'), self.path, {'file': _pdf(), 'title': 'X'}).status_code, 403)

    def test_upload(self):
        r = self.upload(self.officer, self.path, {
            'file': _pdf(), 'title': 'Treasurer report', 'committee': self.c.id, 'document_type': 'report',
            'description': 'Q3', 'meeting_date': '2026-10-30', 'published_to_chapter': 'true'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'], 'Document "Treasurer report" has been uploaded successfully!')
        doc = CommitteeDocument.objects.get(pk=r.json()['document']['id'])
        self.assertEqual((doc.committee, doc.uploaded_by, doc.document_type, str(doc.meeting_date),
                          doc.published_to_chapter),
                         (self.c, self.officer, 'report', '2026-10-30', True))
        # No committee is allowed (the model's committee is optional); a text file.
        notes = SimpleUploadedFile('notes.txt', b'Plain notes for the chapter.\n', content_type='text/plain')
        r = self.upload(self.officer, self.path, {'file': notes, 'title': 'General'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(CommitteeDocument.objects.get(pk=r.json()['document']['id']).committee)
        self.assertFalse(r.json()['document']['published'])

    def test_refused(self):
        bad = [
            ({'title': 'No file'}, 'Choose a file'),
            ({'file': _html(), 'title': 'Spoofed'}, ''),
            ({'file': _png(), 'title': 'A photo'}, 'Only these file types'),
            ({'file': _pdf(), 'title': ''}, 'Document Title'),
            ({'file': _pdf(), 'title': 'Bad type', 'document_type': 'memo'}, 'Document Type'),
            ({'file': _pdf(), 'title': 'Bad date', 'meeting_date': 'soon'}, 'Meeting Date'),
            ({'file': _pdf(), 'title': 'x' * 201}, 'Document Title'),
        ]
        for body, words in bad:
            r = self.upload(self.officer, self.path, body)
            self.assertEqual(r.status_code, 400, (body.get('title'), r.content))
            self.assertEqual(r.json()['error'], 'invalid_input', r.content)
            self.assertIn(words, r.content.decode())
        self.assertFalse(CommitteeDocument.objects.exists())

    def test_website(self):
        web = self.web(self.officer)
        self.assertEqual(web.get(reverse('upload_report')).status_code, 200)
        resp = web.post(reverse('upload_report'), {'document': _pdf(), 'title': 'Web report',
                                                   'committee': self.c.id, 'document_type': 'report'})
        self.assertRedirects(resp, reverse('officer_home'), fetch_redirect_response=False)
        self.assertTrue(CommitteeDocument.objects.filter(title='Web report', uploaded_by=self.officer).exists())
        resp = web.post(reverse('upload_report'), {'document': _html(), 'title': 'Bad', 'document_type': 'report'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any(m.startswith('File upload error:') for m in _messages(resp)))
        resp = web.post(reverse('upload_report'), {'document': _pdf(), 'title': '', 'document_type': 'report'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        self.assertEqual(self.web(self.advisor).get(reverse('upload_report')).status_code, 403)


# ── Edit Landing Page ───────────────────────────────────────────────────────

class LandingPageTests(_Base):

    def setUp(self):
        super().setUp()
        self.officer = make_user('lp-off', member_type='Officer')
        self.advisor = make_user('lp-adv', member_type='Advisor')
        self.member = make_user('lp-mem')
        self.path = 'officer/landing-page/'

    def post(self, user, data, multipart=False):
        if multipart:
            return self.upload(user, self.path, data)
        return self.api.post(API + self.path, json.dumps(data), content_type='application/json',
                             **bearer(self.token(user)))

    def test_who(self):
        self.assertEqual(self.get(self.officer, self.path).status_code, 200)
        self.assertEqual(self.get(self.advisor, self.path).status_code, 200)   # officer_or_advisor_required
        self.assertEqual(self.get(self.member, self.path).status_code, 403)
        self.assertEqual(self.post(self.member, {'action': 'add_contact_topic', 'topic_label': 'X'}).status_code, 403)
        toggle('officer_home', False)
        r = self.get(self.officer, self.path)
        self.assertEqual(r.status_code, 403)
        self.assertIn('page_disabled', r.content.decode())

    def test_save_content(self):
        r = self.post(self.officer, {
            'action': 'save_content', 'tagline': '  Welcome home ', 'who_we_are_html': '<p>Hi</p><script>x</script>',
            'recruitment_banner_active': True, 'recruitment_banner_message': 'Rush is open',
            'recruitment_banner_end': '2026-12-01', 'show_parliament_info': False, 'contact_phone': '555'})
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual(b['message'], 'Landing page content saved.')
        self.assertEqual(b['content']['tagline'], 'Welcome home')
        self.assertNotIn('<script>', b['content']['who_we_are_html'])
        content = LandingPageContent.objects.get()
        self.assertEqual((content.recruitment_banner_active, content.recruitment_banner_end, content.show_parliament_info,
                          content.updated_by),
                         (True, date(2026, 12, 1), False, self.officer))
        # A field left out keeps its value (the page always sends every field).
        self.assertTrue(content.show_contact_section)
        self.post(self.officer, {'action': 'save_content', 'contact_address': '1 Main'})
        content.refresh_from_db()
        self.assertEqual((content.tagline, content.recruitment_banner_message, content.contact_address,
                          content.recruitment_banner_end), ('Welcome home', 'Rush is open', '1 Main', date(2026, 12, 1)))
        # The page's maxlength.
        r = self.post(self.officer, {'action': 'save_content', 'tagline': 'x' * 301})
        self.assertEqual(r.status_code, 400)
        self.assertIn('300 characters', r.content.decode())

    def test_photos_and_og_image(self):
        r = self.post(self.officer, {'action': 'upload_photo', 'image': _png(), 'caption': 'Fall 2026'}, multipart=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Photo uploaded.')
        photo = r.json()['photos'][0]
        self.assertEqual(photo['caption'], 'Fall 2026')
        img = self.get(self.officer, photo['image'])
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img['Content-Type'], 'image/png')
        # Not a photo; no photo.
        r = self.post(self.officer, {'action': 'upload_photo', 'image': _pdf()}, multipart=True)
        self.assertEqual(r.status_code, 400)
        r = self.post(self.officer, {'action': 'upload_photo'})
        self.assertEqual((r.status_code, 'No image file provided.' in r.content.decode()), (400, True))
        # The link-preview image comes with save_content.
        r = self.post(self.officer, {'action': 'save_content', 'og_image': _png('og.png')}, multipart=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.get(self.officer, r.json()['content']['og_image']).status_code, 200)
        r = self.post(self.officer, {'action': 'delete_photo', 'photo_id': photo['id']})
        self.assertEqual((r.status_code, r.json()['message'], r.json()['photos']), (200, 'Photo deleted.', []))
        self.assertEqual(self.post(self.officer, {'action': 'delete_photo', 'photo_id': photo['id']}).status_code, 404)
        self.assertEqual(self.get(self.officer, f"officer/landing-page/photos/{photo['id']}/image/").status_code, 404)

    def test_links_and_topics(self):
        r = self.post(self.officer, {'action': 'add_social_link', 'social_label': 'Instagram',
                                     'social_url': 'https://instagram.com/x'})
        self.assertEqual((r.status_code, r.json()['message']), (200, 'Link "Instagram" added.'))
        link = r.json()['social_links'][0]
        r = self.post(self.officer, {'action': 'add_social_link', 'social_label': 'Bad', 'social_url': 'instagram'})
        self.assertEqual(r.status_code, 400)
        r = self.post(self.officer, {'action': 'add_social_link', 'social_label': '', 'social_url': ''})
        self.assertIn('Label and URL are required.', r.content.decode())
        self.post(self.officer, {'action': 'delete_social_link', 'link_id': link['id']})
        self.assertFalse(LandingPageSocialLink.objects.exists())

        r = self.post(self.officer, {'action': 'add_contact_topic', 'topic_label': 'Recruitment',
                                     'topic_role_code': 'VPR'})
        topic = r.json()['contact_topics'][0]
        self.assertEqual((topic['label'], topic['role_code'], topic['is_active']), ('Recruitment', 'VPR', True))
        r = self.post(self.officer, {'action': 'toggle_contact_topic', 'topic_id': topic['id']})
        self.assertEqual((r.json()['message'], r.json()['contact_topics'][0]['is_active']), (None, False))
        self.post(self.officer, {'action': 'delete_contact_topic', 'topic_id': topic['id']})
        self.assertFalse(LandingPageContactTopic.objects.exists())

        r = self.post(self.advisor, {'action': 'add_form_link', 'form_title': 'Interest form',
                                     'form_url': 'https://forms.example.com/a', 'form_button_text': ''})
        self.assertEqual(r.status_code, 200, r.content)
        form = r.json()['form_links'][0]
        self.assertEqual(form['button_text'], 'Apply Now')
        self.post(self.officer, {'action': 'toggle_form_link', 'link_id': form['id']})
        self.assertFalse(LandingPageFormLink.objects.get().is_active)
        self.assertEqual(self.post(self.officer, {'action': 'nope'}).status_code, 400)
        self.assertEqual(self.get(self.officer, self.path).json()['preview_path'], reverse('landing') + '?preview=1')

    def test_website(self):
        web = self.web(self.officer)
        url = reverse('edit_landing_page')
        self.assertEqual(web.get(url).status_code, 200)
        resp = web.post(url, {'action': 'save_content', 'tagline': 'From the web', 'show_contact_section': 'on'})
        self.assertRedirects(resp, url, fetch_redirect_response=False)
        self.assertIn('Landing page content saved.', _messages(resp))
        content = LandingPageContent.objects.get()
        self.assertEqual((content.tagline, content.show_contact_section, content.show_parliament_info),
                         ('From the web', True, False))
        resp = web.post(url, {'action': 'upload_photo'})
        self.assertIn('No image file provided.', _messages(resp))
        web.post(url, {'action': 'upload_photo', 'image': _png(), 'caption': 'Web'})
        self.assertEqual(LandingPagePhoto.objects.get().caption, 'Web')
        resp = web.post(url, {'action': 'delete_photo', 'photo_id': 999999})
        self.assertIn('Photo not found.', _messages(resp))
        resp = web.post(url, {'action': 'add_form_link', 'form_title': 'T', 'form_url': 'https://e.com'})
        self.assertIn('Form link "T" added.', _messages(resp))
        self.assertEqual(self.web(self.member).get(url).status_code, 403)

    def test_website_refuses_a_file_that_isnt_a_photo(self):
        """11-06-26: the website had only the browser's accept="image/*"."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        web = self.web(self.officer)
        url = reverse('edit_landing_page')
        for upload in (SimpleUploadedFile('notes.txt', b'hello', content_type='text/plain'),
                       SimpleUploadedFile('fake.png', b'#!/bin/sh\necho hi\n', content_type='image/png')):
            resp = web.post(url, {'action': 'upload_photo', 'image': upload})
            self.assertTrue(_messages(resp), upload.name)
            resp = web.post(url, {'action': 'save_content', 'tagline': 'x', 'og_image': upload})
        self.assertFalse(LandingPagePhoto.objects.exists())
        self.assertFalse(LandingPageContent.objects.filter(tagline='x').exists())
