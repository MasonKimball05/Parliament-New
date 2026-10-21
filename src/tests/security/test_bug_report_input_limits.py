"""
v3.51.6 — bug report submission checks its input.

`submit_bug_report` built the model straight from `request.POST` and assigned
`request.FILES['screenshot']` with no form, so:

- `page_url` > 500, `feature` > 200 or `browser_info` > 500 characters raised
  `DataError` on PostgreSQL (a 500 for any signed-in member);
- `issue_type` / `page` / `priority` were saved whatever they said;
- the `screenshot` ImageField was never checked (a model ImageField only
  validates inside a form), so any file of any size was stored as one.

Run with: python manage.py test src.tests.security.test_bug_report_input_limits
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from src.models import BugReport
from src.models.users import ParliamentUser

_MEDIA = tempfile.mkdtemp(prefix='bugreport-test-')


def _png(name='shot.png'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=_MEDIA)
class BugReportInputLimitsTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = ParliamentUser.objects.create(user_id='BR-1', username='br1', name='Bug Reporter',
                                                  member_type='Member', member_status='Active')
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse('bug_report')

    def _post(self, **extra):
        data = {'description': 'Something broke', 'issue_type': 'ui', 'priority': 'medium'}
        data.update(extra)
        return self.client.post(self.url, data)

    def test_overlong_text_fields_are_trimmed_not_a_500(self):
        r = self._post(page_url='https://x/' + 'a' * 900, feature='f' * 400, browser_info='b' * 900)
        self.assertEqual(r.status_code, 302)
        report = BugReport.objects.get(submitted_by=self.user)
        self.assertEqual(len(report.page_url), 500)
        self.assertEqual(len(report.feature), 200)
        self.assertEqual(len(report.browser_info), 500)

    def test_unknown_choices_fall_back_to_defaults(self):
        r = self._post(issue_type='x' * 50, priority='urgent!!', page='nowhere')
        self.assertEqual(r.status_code, 302)
        report = BugReport.objects.get(submitted_by=self.user)
        self.assertEqual(report.issue_type, 'other')
        self.assertEqual(report.priority, 'medium')
        self.assertEqual(report.page, '')

    def test_known_choices_are_kept(self):
        self._post(issue_type='ui', priority='high', page='home')
        report = BugReport.objects.get(submitted_by=self.user)
        self.assertEqual((report.issue_type, report.priority, report.page), ('ui', 'high', 'home'))

    def test_a_real_png_is_attached(self):
        self._post(screenshot=_png())
        self.assertTrue(BugReport.objects.get(submitted_by=self.user).screenshot)

    def test_a_non_image_is_dropped_and_the_report_still_saved(self):
        fake = SimpleUploadedFile('shot.png', b'not an image at all', content_type='image/png')
        r = self._post(screenshot=fake)
        self.assertEqual(r.status_code, 302)
        report = BugReport.objects.get(submitted_by=self.user)
        self.assertFalse(report.screenshot)
        msgs = [str(m) for m in r.wsgi_request._messages]
        self.assertTrue(any('without the screenshot' in m for m in msgs), msgs)

    def test_a_wrong_extension_is_dropped(self):
        self._post(screenshot=_png('shot.tiff'))
        self.assertFalse(BugReport.objects.get(submitted_by=self.user).screenshot)

    def test_an_oversized_file_is_dropped(self):
        from src.view import bug_report as view
        old = view.SCREENSHOT_MAX_BYTES
        view.SCREENSHOT_MAX_BYTES = 10
        try:
            self._post(screenshot=_png())
        finally:
            view.SCREENSHOT_MAX_BYTES = old
        self.assertFalse(BugReport.objects.get(submitted_by=self.user).screenshot)
