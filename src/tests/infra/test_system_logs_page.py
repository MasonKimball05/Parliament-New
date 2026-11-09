"""
System Logs page, 11-09-26 rework (src/system_logs.py, src/view/officer/view_logs.py).

* Tracebacks belong to the entry they follow, in file order (the reader gives
  them newest first, so they used to show above the error they explain).
* Level, source and search filters; 200 / 500 / 1,000 lines; oldest-first.
* The plain-text download and the auto-refresh fragment carry the same
  redacted entries as the page (no Kai reporter name).
* The console look only for an Admin v2 session; officers keep the site look.
* The Django admin's own "View logs" copy now opens this page.
"""
import importlib
import os
import tempfile
from unittest import mock

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from src import system_logs
from src.models import ParliamentUser
from src.view import admin_v2

# The package re-exports the view function under the module's name.
view_module = importlib.import_module('src.view.officer.view_logs')

LOG = """\
2026-11-09 10:00:00,000 [INFO] django.server: "GET / HTTP/1.1" 200
2026-11-09 10:00:01,000 [ERROR] django.request: Internal Server Error: /boom/
Traceback (most recent call last):
  File "views.py", line 3, in boom
ZeroDivisionError: division by zero
2026-11-09 10:00:02,000 [WARNING] src.security: Slow request /slow/
2026-11-09 10:00:03,000 [DEBUG] src.cache: cache miss for home
2026-11-09 10:00:04,000 [INFO] src.function_calls: User reporter_guy called submit_kai_report with arguments: (), {}
2026-11-09 10:00:05,000 [INFO] src.function_calls: User nosy_officer called event_attendance_list with arguments: (), {}
"""


def raw_from(text):
    """What read_log_entries returns for `text` (newest first), without Kai
    redaction: enough for the pure functions."""
    out = []
    for line in reversed(text.splitlines()):
        m = view_module.LOG_PATTERN.match(line)
        if m:
            ts, level, logger_name, message = m.groups()
            out.append({'timestamp': ts, 'logger': logger_name, 'level': level,
                        'message': f'[{level}] {message}', 'text': message})
        elif line.strip():
            out.append({'timestamp': '', 'logger': '', 'level': '', 'message': line, 'text': None})
    return out


class GroupingAndFilterTests(SimpleTestCase):

    def entries(self, **params):
        filters = system_logs.parse_filters(params)
        return system_logs.apply_filters(system_logs.group_entries(raw_from(LOG)), filters)

    def test_traceback_folds_into_its_error_in_file_order(self):
        entries = system_logs.group_entries(raw_from(LOG))
        self.assertEqual(len(entries), 6)
        error = next(e for e in entries if e['level'] == 'ERROR')
        self.assertEqual(error['detail'], [
            'Traceback (most recent call last):',
            '  File "views.py", line 3, in boom',
            'ZeroDivisionError: division by zero',
        ])
        self.assertEqual(error['level_key'], 'error')
        self.assertEqual((error['date'], error['time']), ('2026-11-09', '10:00:01'))
        # Newest first, and no stray traceback rows.
        self.assertEqual(entries[0]['logger'], 'src.function_calls')
        self.assertTrue(all(e['timestamp'] for e in entries))

    def test_lines_before_the_first_entry_are_kept(self):
        entries = system_logs.group_entries(raw_from('  orphan one\n  orphan two\n' + LOG))
        self.assertEqual(entries[-1]['text'], '  orphan one')
        self.assertEqual(entries[-1]['detail'], ['  orphan two'])

    def test_level_is_the_parsed_level_not_words_in_the_text(self):
        entries = system_logs.group_entries(raw_from(
            '2026-11-09 10:00:00,000 [DEBUG] x: mentions ERROR and INFO\n'))
        self.assertEqual(entries[0]['level_key'], 'debug')

    def test_filters(self):
        self.assertEqual([e['level'] for e in self.entries(level='error')], ['ERROR'])
        self.assertEqual(sorted(e['level'] for e in self.entries(level='warning')), ['ERROR', 'WARNING'])
        self.assertNotIn('DEBUG', [e['level'] for e in self.entries(level='info')])
        self.assertEqual([e['logger'] for e in self.entries(source='src.security')], ['src.security'])
        # Search reaches into the traceback.
        self.assertEqual([e['level'] for e in self.entries(q='zerodivision')], ['ERROR'])
        oldest = self.entries(order='oldest')
        self.assertEqual(oldest[0]['logger'], 'django.server')

    def test_junk_parameters_fall_back(self):
        f = system_logs.parse_filters({'level': 'nope', 'lines': '99999', 'order': 'x', 'q': ' a ' * 200})
        self.assertEqual((f['level'], f['lines'], f['order']), ('all', 200, 'newest'))
        self.assertLessEqual(len(f['q']), 200)
        self.assertEqual(system_logs.parse_filters({'lines': '1000'})['lines'], 1000)

    def test_summary_and_text(self):
        entries = system_logs.group_entries(raw_from(LOG))
        s = system_logs.summarize(entries)
        self.assertEqual((s['errors'], s['warnings'], s['info'], s['debug']), (1, 1, 3, 1))
        self.assertEqual(s['sources'][0], ('src.function_calls', 2))
        self.assertEqual((s['oldest'], s['newest']), ('2026-11-09 10:00:00,000', '2026-11-09 10:00:05,000'))
        text = system_logs.as_text([e for e in entries if e['level'] == 'ERROR'])
        self.assertEqual(text.splitlines()[0],
                         '2026-11-09 10:00:01,000 [ERROR] django.request: Internal Server Error: /boom/')
        self.assertIn('ZeroDivisionError: division by zero', text)


class PageTests(TestCase):

    def setUp(self):
        cache.clear()
        fd, self.path = tempfile.mkstemp(suffix='.log')
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(LOG)
        patcher = mock.patch.object(view_module, 'LOG_FILE_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(os.remove, self.path)
        ParliamentUser.objects.create_user(user_id='SL-REP', password='pw-sl-tests-1', name='Rep',
                                           username='reporter_guy', member_type='Member')
        self.officer = ParliamentUser.objects.create_user(
            user_id='SL-OFF', password='pw-sl-tests-1', name='Officer', username='nosy_officer',
            member_type='Officer')
        self.client = Client()
        self.client.force_login(self.officer)

    def get(self, **params):
        return self.client.get(reverse('view_logs'), params)

    def test_classic_page(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, 'admin/system_logs.html')
        page = r.content.decode()
        self.assertIn('css/system-logs.css', page)
        self.assertIn('js/system-logs.js', page)
        self.assertIn('ZeroDivisionError', page)
        self.assertIn('3 more lines', page)
        self.assertIn('event_attendance_list', page)
        self.assertIn('nosy_officer', page)          # control: actors are still named
        self.assertNotIn('reporter_guy', page)        # Kai reporter is not
        self.assertNotIn('<script>', page.replace('<script src', ''))

    def test_filters_on_the_page(self):
        page = self.get(level='error').content.decode()
        self.assertIn('Internal Server Error', page)
        self.assertNotIn('Slow request', page)
        page = self.get(q='nothing-matches-this').content.decode()
        self.assertIn('Nothing matches these filters', page)

    def test_download_is_the_filtered_redacted_text(self):
        r = self.get(format='txt')
        self.assertEqual(r['Content-Type'], 'text/plain; charset=utf-8')
        self.assertIn('attachment; filename="system-log-', r['Content-Disposition'])
        body = r.content.decode()
        self.assertIn('submit_kai_report', body)
        self.assertNotIn('reporter_guy', body)
        self.assertIn('[ERROR] django.request', body)
        only_errors = self.get(format='txt', level='error').content.decode()
        self.assertNotIn('Slow request', only_errors)

    def test_partial_is_just_the_list(self):
        r = self.get(partial='1', level='warning')
        self.assertTemplateUsed(r, 'admin/_system_log_entries.html')
        body = r.content.decode()
        self.assertTrue(body.lstrip().startswith('<div id="sl-entries"'))
        self.assertNotIn('<html', body)
        self.assertIn('Slow request', body)
        self.assertNotIn('reporter_guy', body)

    def test_missing_file(self):
        with mock.patch.object(view_module, 'LOG_FILE_PATH', self.path + '.gone'):
            page = self.get().content.decode()
        self.assertIn("log file wasn't found", page)

    def test_more_lines(self):
        with open(self.path, 'w', encoding='utf-8') as fh:
            for i in range(700):
                fh.write(f'2026-11-09 10:00:00,000 [INFO] src.x: line {i}\n')
        self.assertEqual(self.get().context['total'], 200)
        self.assertEqual(self.get(lines='500').context['total'], 500)
        self.assertEqual(self.get(lines='1000').context['total'], 700)

    def _admin_v2_session(self, user):
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {user.user_id})
        patcher.start()
        self.addCleanup(patcher.stop)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session.save()

    def test_console_look_only_in_an_admin_v2_session(self):
        from src.models_feature_flags import FeatureFlag
        FeatureFlag.objects.update_or_create(
            name='admin_console_ui', defaults={'display_name': 'x', 'description': 'x',
                                               'category': 'admin', 'is_enabled': True})
        cache.clear()
        self.assertTemplateUsed(self.get(), 'admin/system_logs.html')
        self._admin_v2_session(self.officer)
        r = self.get()
        self.assertTemplateUsed(r, 'admin_v2/console/system_logs.html')
        self.assertNotIn('reporter_guy', r.content.decode())

    def test_django_admin_copy_redirects_here(self):
        admin = ParliamentUser.objects.create_user(user_id='SL-ADM', password='pw-sl-tests-1', name='Adm',
                                                   username='sl_admin', member_type='Officer', is_admin=True)
        c = Client()
        c.force_login(admin)
        r = c.get(reverse('admin:view_logs'))
        self.assertRedirects(r, reverse('view_logs'), fetch_redirect_response=False)
