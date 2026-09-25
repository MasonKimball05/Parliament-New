"""
09-25-26 — unhandled 500s email a human, scrubbed and rate-limited.

Why it exists: the email-login 500 was live for three months and found only
by reading the server log. See src/error_alerts.py.

Run with: python manage.py test src.tests.infra.test_error_alerts
"""
import logging
import sys
from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from src.error_alerts import ErrorAlertHandler, MAX_ALERTS_PER_HOUR


def _record(path='/login/', method='POST', data=None, exc=None, line_hint=0):
    request = RequestFactory().post(path, data or {'username': 'x', 'password': 'HUNTER2-SECRET'})
    request.user = mock.Mock(is_authenticated=True, pk='73')
    try:
        if exc:
            raise exc
        raise UnboundLocalError("cannot access local variable 'User'")
    except Exception:
        exc_info = sys.exc_info()
    rec = logging.LogRecord('django.request', logging.ERROR, __file__, 1 + line_hint,
                            'Internal Server Error: %s', (path,), exc_info)
    rec.request = request
    rec.status_code = 500
    return rec


@override_settings(ERROR_ALERT_EMAIL='maintainer@example.com')
class ErrorAlertHandlerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.handler = ErrorAlertHandler()
        patcher = mock.patch('src.tasks.email.send_email.delay')
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def _body(self):
        return self.send.call_args[0][1]

    def test_a_500_sends_one_alert_with_the_essentials(self):
        self.handler.emit(_record())
        self.send.assert_called_once()
        subject, body, _from, to, _silent = self.send.call_args[0]
        self.assertIn('UnboundLocalError', subject)
        self.assertIn('/login/', subject)
        self.assertEqual(to, ['maintainer@example.com'])
        self.assertIn('User pk:   73', body)
        self.assertIn('Traceback', body)

    def test_no_post_data_cookies_or_locals_reach_the_email(self):
        self.handler.emit(_record())
        body = self._body()
        self.assertNotIn('HUNTER2-SECRET', body)
        self.assertNotIn('password', body.lower().replace('password_', ''))
        self.assertNotIn('csrftoken', body)

    def test_kai_paths_drop_the_exception_message(self):
        self.handler.emit(_record(path='/kai/reports/12/', exc=ValueError('accused: John Doe')))
        body = self._body()
        self.assertNotIn('John Doe', body)
        self.assertIn('[redacted — Kai path]', body)

    def test_the_same_error_is_sent_once_per_window(self):
        for _ in range(5):
            self.handler.emit(_record())
        self.assertEqual(self.send.call_count, 1)

    def test_a_global_cap_stops_a_storm_of_distinct_errors(self):
        for i in range(MAX_ALERTS_PER_HOUR + 5):
            # distinct exception types → distinct signatures
            exc = type(f'DistinctError{i}', (Exception,), {})('boom')
            self.handler.emit(_record(exc=exc))
        self.assertEqual(self.send.call_count, MAX_ALERTS_PER_HOUR)

    def test_records_without_an_exception_are_ignored(self):
        rec = logging.LogRecord('django.request', logging.ERROR, __file__, 1, 'x', (), None)
        self.handler.emit(rec)
        self.send.assert_not_called()

    def test_a_failure_while_alerting_never_raises(self):
        self.send.side_effect = RuntimeError('broker down')
        with mock.patch.object(self.handler, 'handleError') as he:
            self.handler.emit(_record())   # must not raise
            he.assert_called_once()
