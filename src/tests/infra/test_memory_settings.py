"""
v3.45.5 (10-13-26) — the settings that keep Parliament inside 1 GB of RAM.

Mason: "Parliament is idling at about 70-80% memory usage at any time and it
gets up to 90+ a lot ... The server only has 1 gb."

Measured with production settings (Postgres + Redis, Daphne, Celery) on the
same pages and the same burst of 20 members loading 10 pages each:

    peak memory of the app and its Postgres connections   749 MB -> 479 MB
    Postgres connections left open afterwards             62 -> 2
    500s during the run ("too many clients")              137 -> 1 (an unrelated
                                                          404-that-was-a-500, fixed)

Each test pins one of the changes, with the reason in its docstring, so a
later edit cannot quietly undo one.

Run with: python manage.py test src.tests.infra.test_memory_settings
"""
from pathlib import Path

from django.conf import settings
from django.core.checks import Warning as CheckWarning
from django.test import SimpleTestCase, override_settings

ROOT = Path(settings.BASE_DIR)


def unit(name):
    return (ROOT / name).read_text(encoding='utf-8')


def directives(text):
    """Non-comment lines of a unit file, continuation lines joined."""
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith('#')]
    return '\n'.join(lines).replace('\\\n', ' ')


class PersistentConnectionsTests(SimpleTestCase):
    """
    Under Daphne each request runs in its own thread, and a persistent
    connection belongs to its thread: with CONN_MAX_AGE > 0 every request left
    an idle Postgres backend behind (100 after one burst, ~220 MB).
    """

    def test_the_default_is_zero(self):
        source = (ROOT / 'Parliament' / 'settings.py').read_text(encoding='utf-8')
        self.assertIn("'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '0'))", source)

    def test_the_check_warns_when_env_turns_it_back_on(self):
        from src.checks_db_connections import no_persistent_connections_under_asgi as check
        postgres = {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'x'}
        with override_settings(DATABASES={'default': dict(postgres, CONN_MAX_AGE=300)}):
            warnings = check(None)
        self.assertEqual([w.id for w in warnings], ['src.W007'])
        self.assertIsInstance(warnings[0], CheckWarning)
        with override_settings(DATABASES={'default': dict(postgres, CONN_MAX_AGE=None)}):
            self.assertEqual([w.id for w in check(None)], ['src.W007'])
        with override_settings(DATABASES={'default': dict(postgres, CONN_MAX_AGE=0)}):
            self.assertEqual(check(None), [])


class ProcessesTests(SimpleTestCase):
    def test_daphne_is_only_an_app_for_runserver(self):
        """As an app it loaded Twisted and the Daphne server into the worker,
        beat and every management command (~16 MB each)."""
        self.assertNotIn('daphne', settings.INSTALLED_APPS)
        source = (ROOT / 'Parliament' / 'settings.py').read_text(encoding='utf-8')
        self.assertIn("if len(sys.argv) > 1 and sys.argv[1] == 'runserver':\n    INSTALLED_APPS.insert(0, 'daphne')", source)

    def test_celery_skips_the_startup_checks(self):
        """The URL check imported every view into the worker and beat (~30 MB)."""
        source = (ROOT / 'Parliament' / 'celery.py').read_text(encoding='utf-8')
        self.assertIn("os.environ.setdefault('CELERY_SKIP_CHECKS', '1')", source)
        self.assertLess(source.index('CELERY_SKIP_CHECKS'), source.index('app = Celery('))

    def test_beat_runs_inside_the_one_worker(self):
        """A separate beat service was a second full copy of Django (~115 MB)."""
        worker = directives(unit('parliament-worker.service'))
        self.assertIn('worker -B', worker)
        self.assertIn('--scheduler django_celery_beat.schedulers:DatabaseScheduler', worker)
        self.assertIn('--max-memory-per-child=', worker)
        # Both running would run every periodic task twice.
        self.assertIn('Conflicts=parliament-beat.service', worker)
        self.assertFalse((ROOT / 'parliament-beat.service').exists())
        self.assertIn('--concurrency=1', worker)


class ServiceLimitsTests(SimpleTestCase):
    def test_python_services_cap_malloc_arenas(self):
        """A burst grew Daphne 181 -> 296 MB with glibc's default arenas, 181 -> 235 MB with 2."""
        for name in ('parliament-gunicorn.service', 'parliament-worker.service'):
            self.assertIn('Environment="MALLOC_ARENA_MAX=2"', directives(unit(name)), name)

    def test_daphne_has_no_virtual_memory_cap(self):
        """LimitAS=256M stopped the process starting a second thread once
        PyMuPDF was loaded; Daphne needs a thread per request."""
        daphne = directives(unit('parliament-gunicorn.service'))
        self.assertNotIn('LimitAS', daphne)
        self.assertNotIn('MemoryLimit=', daphne)
        self.assertIn('MemoryHigh=', daphne)
        self.assertIn('MemoryMax=', daphne)
