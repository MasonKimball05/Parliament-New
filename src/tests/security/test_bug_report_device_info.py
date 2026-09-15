"""
v3.31.2 — device/browser/OS info on bug reports.

Mason's ask, mid-flight during the v3.31.1 education-dashboard bug fix: "Can
we also update the bug report to include information like device type and
browser so that I know what to look for or where to look?"

The gap: `BugReport.browser_info` (raw `navigator.userAgent`, set client-side)
already existed and was already emailed on submission — but it was never
rendered on `bug_report_detail.html` or `bug_admin.html`, the two pages Mason
actually opens to triage a report, and a raw UA string isn't something you
read at a glance anyway. This adds three parsed fields (`device_type`,
`browser`, `operating_system`) captured server-side at submission via
`UserSession.parse_user_agent()` — the same parser the Active Sessions
feature already uses, reused rather than duplicated a third time alongside
`security_utils.parse_device_info()` — and surfaces them on both pages plus
the admin-notification email, with a migration backfilling existing rows
from their already-stored `browser_info`.

Covers: submission-time parsing and storage, the migration backfill
(including its "leave already-filled rows alone" and "nothing to backfill"
cases), and the bug-admin device-type filter. Template rendering is checked
at the level of "does the value appear on the page" rather than exact HTML,
since the surrounding markup isn't this feature's concern.

Also covers the fix this uncovered: `UserSession.parse_user_agent()` checked
'mac os x' before 'iphone'/'ipad', and every iOS/iPadOS Safari UA contains
the literal substring "like Mac OS X" (Apple's own compatibility token) — so
every phone and tablet was silently reported as "macOS" for as long as the
Active Sessions feature has existed. Order fixed; iphone/ipad now checked
first.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import BugReport
from src.models.users import ParliamentUser, UserSession

IPHONE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
)
WINDOWS_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
)


def make_user(uid, **kwargs):
    defaults = dict(name='Test Member', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('Xk9$mQz2#vLp7&wR')
    user.save()
    return user


class ParseUserAgentOSDetectionTests(TestCase):
    """The iphone-before-mac-os-x ordering fix, in isolation."""

    def test_an_iphone_safari_ua_is_reported_as_ios_not_macos(self):
        device_type, browser, os_name = UserSession.parse_user_agent(IPHONE_UA)
        self.assertEqual(os_name, 'iOS')
        self.assertEqual(device_type, 'mobile')
        self.assertEqual(browser, 'Safari')

    def test_a_windows_chrome_ua_is_unaffected_by_the_reordering(self):
        device_type, browser, os_name = UserSession.parse_user_agent(WINDOWS_CHROME_UA)
        self.assertEqual(os_name, 'Windows 10/11')
        self.assertEqual(device_type, 'desktop')
        self.assertEqual(browser, 'Chrome')

    def test_a_blank_user_agent_falls_through_to_the_safe_defaults(self):
        device_type, browser, os_name = UserSession.parse_user_agent('')
        self.assertEqual((device_type, browser, os_name), ('desktop', 'Unknown', 'Unknown'))


class SubmitBugReportCapturesDeviceInfoTests(TestCase):
    def setUp(self):
        self.user = make_user('bugdevice-1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_submitting_a_report_parses_and_stores_device_info_from_the_real_header(self):
        response = self.client.post(
            reverse('bug_report'),
            {'description': 'Button does nothing', 'issue_type': 'functionality', 'priority': 'medium'},
            HTTP_USER_AGENT=IPHONE_UA,
        )
        self.assertEqual(response.status_code, 302)
        report = BugReport.objects.get(submitted_by=self.user)
        self.assertEqual(report.device_type, 'mobile')
        self.assertEqual(report.browser, 'Safari')
        self.assertEqual(report.operating_system, 'iOS')

    def test_device_info_is_captured_independently_of_the_js_hidden_field(self):
        # browser_info (the JS-set hidden field) deliberately omitted from
        # the POST — server-side parsing must not depend on it having run.
        response = self.client.post(
            reverse('bug_report'),
            {'description': 'No JS ran', 'issue_type': 'other', 'priority': 'low'},
            HTTP_USER_AGENT=WINDOWS_CHROME_UA,
        )
        self.assertEqual(response.status_code, 302)
        report = BugReport.objects.get(submitted_by=self.user)
        self.assertEqual(report.browser, 'Chrome')
        self.assertEqual(report.browser_info, '')


class BackfillMigrationTests(TestCase):
    """
    Exercises the RunPython step in migration 0039 directly (Django doesn't
    re-run past migrations' data steps against a TestCase's already-migrated
    schema, so calling the function is the way to cover it).
    """

    def setUp(self):
        self.user = make_user('bugdevice-2')

    def _backfill(self):
        import importlib
        from django.apps import apps
        mod = importlib.import_module(
            'src.migrations.0039_bugreport_browser_bugreport_device_type_and_more'
        )
        mod.backfill_device_info(apps, None)

    def test_a_row_with_stored_browser_info_and_blank_fields_gets_backfilled(self):
        report = BugReport.objects.create(
            description='pre-release report', submitted_by=self.user, browser_info=IPHONE_UA,
        )
        self._backfill()
        report.refresh_from_db()
        self.assertEqual(report.device_type, 'mobile')
        self.assertEqual(report.browser, 'Safari')
        self.assertEqual(report.operating_system, 'iOS')

    def test_a_row_with_no_browser_info_is_left_blank(self):
        report = BugReport.objects.create(
            description='no UA ever captured', submitted_by=self.user, browser_info='',
        )
        self._backfill()
        report.refresh_from_db()
        self.assertEqual((report.device_type, report.browser, report.operating_system), ('', '', ''))

    def test_a_row_already_filled_is_left_untouched_not_re_parsed(self):
        # Simulates a post-release row: fields already correctly populated at
        # submission time. Give it a browser_info that would parse
        # differently, to prove the backfill skips it rather than overwriting.
        report = BugReport.objects.create(
            description='already filled', submitted_by=self.user,
            browser_info=WINDOWS_CHROME_UA,
            device_type='mobile', browser='Safari', operating_system='iOS',
        )
        self._backfill()
        report.refresh_from_db()
        self.assertEqual(report.device_type, 'mobile')
        self.assertEqual(report.browser, 'Safari')
        self.assertEqual(report.operating_system, 'iOS')

    def test_backfill_is_idempotent(self):
        report = BugReport.objects.create(
            description='run twice', submitted_by=self.user, browser_info=IPHONE_UA,
        )
        self._backfill()
        self._backfill()
        report.refresh_from_db()
        self.assertEqual(report.operating_system, 'iOS')


class BugReportDetailPageDisplaysDeviceInfoTests(TestCase):
    def setUp(self):
        self.user = make_user('bugdevice-3')
        self.client = Client()
        self.client.force_login(self.user)
        self.report = BugReport.objects.create(
            description='detail page test', submitted_by=self.user,
            browser_info=IPHONE_UA, device_type='mobile', browser='Safari', operating_system='iOS',
        )

    def test_the_detail_page_renders_the_parsed_device_and_browser(self):
        html = self.client.get(reverse('bug_report_detail', args=[self.report.id])).content.decode()
        self.assertIn('Safari', html)
        self.assertIn('iOS', html)
        self.assertIn('Mobile', html)

    def test_a_report_with_no_device_info_does_not_render_a_blank_device_row(self):
        blank_report = BugReport.objects.create(description='no UA', submitted_by=self.user)
        html = self.client.get(reverse('bug_report_detail', args=[blank_report.id])).content.decode()
        self.assertNotIn('Device &amp; Browser', html)


class BugAdminDeviceFilterTests(TestCase):
    def setUp(self):
        self.admin = make_user('73')  # bug_admin_required hardcodes user_id == '73'
        self.other = make_user('bugdevice-4')
        self.client = Client()
        self.client.force_login(self.admin)
        BugReport.objects.create(
            description='mobile report', submitted_by=self.other,
            device_type='mobile', browser='Safari', operating_system='iOS',
        )
        BugReport.objects.create(
            description='desktop report', submitted_by=self.other,
            device_type='desktop', browser='Chrome', operating_system='Windows 10/11',
        )

    def test_the_admin_page_lists_both_reports_unfiltered(self):
        html = self.client.get(reverse('bug_admin')).content.decode()
        self.assertIn('mobile report', html)
        self.assertIn('desktop report', html)

    def test_filtering_by_device_type_narrows_the_list(self):
        html = self.client.get(reverse('bug_admin'), {'device_type': 'mobile'}).content.decode()
        self.assertIn('mobile report', html)
        self.assertNotIn('desktop report', html)

    def test_the_device_filter_dropdown_offers_only_values_actually_on_file(self):
        html = self.client.get(reverse('bug_admin')).content.decode()
        self.assertIn('value="mobile"', html)
        self.assertIn('value="desktop"', html)
        self.assertNotIn('value="tablet"', html)
