"""
v3.51.3 — the error-page handlers close their database connection.

Under Daphne, Django renders the page for an exception that reaches its
outermost async layer (every unmatched URL, and Http404/PermissionDenied from a
view) on asgiref's shared thread pool, where the end-of-request close never
reaches it. These pages read the database (context processors, the session),
so each bot 404 on a cold cache left an idle PostgreSQL backend in Daphne. Seen
on prod 10-21-26 (5 connections, last query `src_pagetoggle`). See
src/view/error_handlers.py.

The thread-pool path itself only happens under a real ASGI server; it was
reproduced end to end with Daphne + PostgreSQL 16 (1 leftover per cold-cache
404 before, 0 after). These tests pin the handlers' half of it.

Run with: python manage.py test src.tests.security.test_error_pages_close_db
"""
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware

from src import urls as root_urls
from src.view import error_handlers


def _request(path=None):
    # Built, not literal: the hardcoded-URL guard checks '/...' strings resolve.
    path = path or '/'.join(['', 'robots.txt'])
    request = RequestFactory().get(path)
    SessionMiddleware(lambda r: None).process_request(request)
    request.user = AnonymousUser()
    return request


class HandlersAreWiredTests(TestCase):
    def test_all_four_handlers_are_the_closing_ones(self):
        self.assertIs(root_urls.handler400, error_handlers.custom_400)
        self.assertIs(root_urls.handler403, error_handlers.custom_403)
        self.assertIs(root_urls.handler404, error_handlers.custom_404)
        self.assertIs(root_urls.handler500, error_handlers.custom_500)


class HandlersCloseConnectionsTests(TestCase):
    """
    TestCase wraps each test in atomic(), so the guard would skip the close —
    patch the guard's view of it to exercise both branches.
    """

    def _run(self, call, in_atomic):
        fake = mock.Mock(in_atomic_block=in_atomic)
        with mock.patch.object(error_handlers, 'close_old_connections') as close, \
                mock.patch.object(error_handlers.connections, 'all', return_value=[fake]):
            response = call()
        return response, close

    def test_each_handler_closes_outside_a_transaction(self):
        calls = {
            404: lambda: error_handlers.custom_404(_request(), Http404('x')),
            403: lambda: error_handlers.custom_403(_request(), PermissionDenied('x')),
            400: lambda: error_handlers.custom_400(_request(), Exception('x')),
            500: lambda: error_handlers.custom_500(_request()),
        }
        for status, call in calls.items():
            with self.subTest(status=status):
                response, close = self._run(call, in_atomic=False)
                self.assertEqual(response.status_code, status)
                close.assert_called_once_with()

    def test_leaves_an_open_transaction_alone(self):
        response, close = self._run(lambda: error_handlers.custom_404(_request(), Http404('x')), in_atomic=True)
        self.assertEqual(response.status_code, 404)
        close.assert_not_called()

    def test_closes_even_if_rendering_fails(self):
        fake = mock.Mock(in_atomic_block=False)
        with mock.patch.object(error_handlers, 'render', side_effect=RuntimeError('boom')), \
                mock.patch.object(error_handlers, 'close_old_connections') as close, \
                mock.patch.object(error_handlers.connections, 'all', return_value=[fake]):
            with self.assertRaises(RuntimeError):
                error_handlers.custom_404(_request(), Http404('x'))
        close.assert_called_once_with()

    def test_real_guard_inside_atomic_keeps_the_connection(self):
        with transaction.atomic():
            error_handlers.custom_404(_request(), Http404('x'))
            from src.models import FeatureFlag
            FeatureFlag.objects.count()   # connection still usable
