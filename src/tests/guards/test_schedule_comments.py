"""
09-25-26 — a crontab's comment must agree with its hour/minute.

Why: nine housekeeping entries in `setup_celery_schedules.py` said
"3:xx AM CST" while `'hour': '9'` actually meant 9 AM Central (Celery reads
these crontabs in CELERY_TIMEZONE, not UTC). The digest and weekly entries
had the same bug, fixed 09-19-26. Code and comment disagreeing is the
signature of this mistake, so pin it.

Run with: python manage.py test src.tests.guards.test_schedule_comments
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from src.management.commands.setup_celery_schedules import SCHEDULES

SOURCE = Path(__file__).resolve().parents[2] / 'management' / 'commands' / 'setup_celery_schedules.py'
LINE_RE = re.compile(r"'crontab':\s*\{[^}]*'hour':\s*'(\d+)',\s*'minute':\s*'(\d+)'[^}]*\},?\s*#\s*(.*)$")
TIME_RE = re.compile(r'\b(\d{1,2}):(\d{2})\s*(AM|PM)\b', re.I)


class ScheduleCommentTests(SimpleTestCase):
    def test_every_commented_time_matches_the_crontab(self):
        checked = 0
        for n, line in enumerate(SOURCE.read_text().splitlines(), 1):
            m = LINE_RE.search(line)
            if not m:
                continue
            hour, minute, comment = int(m.group(1)), int(m.group(2)), m.group(3)
            t = TIME_RE.search(comment)
            if not t:
                continue
            self.assertNotIn('UTC', comment, f'line {n}: crontabs run in Central, not UTC')
            h = int(t.group(1)) % 12 + (12 if t.group(3).upper() == 'PM' else 0)
            self.assertEqual((hour, minute), (h, int(t.group(2))),
                             f'line {n}: comment says {t.group(0)} but crontab is {hour}:{minute:02d}')
            checked += 1
        self.assertGreater(checked, 5, 'regex found too few commented crontabs — update the guard')

    def test_housekeeping_runs_before_the_digest(self):
        """The 3:30 AM digest reports on the housekeeping tasks, so they must precede it."""
        by_task = {s['task']: s for s in SCHEDULES if 'crontab' in s}
        digest = next(s for s in SCHEDULES if 'digest' in s['task'] and 'weekly' not in s['task'] and 'crontab' in s)
        d = (int(digest['crontab']['hour']), int(digest['crontab']['minute']))
        for task in ('tasks.cleanup_expired_sessions', 'tasks.prune_expired_login_lockouts',
                     'tasks.expire_stale_ip_blacklist_entries', 'tasks.release_expired_quarantines',
                     'tasks.prune_expired_chat_permissions', 'tasks.notify_expiring_api_tokens'):
            ct = by_task[task]['crontab']
            self.assertLess((int(ct['hour']), int(ct['minute'])), d, task)
