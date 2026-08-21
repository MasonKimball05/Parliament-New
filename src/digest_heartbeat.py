"""
Where the daily-digest heartbeat file lives — one definition (v3.21.5).

⚠️ WHY THIS MODULE EXISTS. The path was written out twice, in
`src/tasks/notifications.py` (the writer) and
`src/management/commands/check_digest_freshness.py` (the reader), as the same
two lines. A watchdog whose reader and writer each compute a path independently
is a watchdog that can be wrong in the quietest possible way: if the two ever
disagreed it would report "the digest has not sent since this watchdog was
installed" forever, on a system where the digest was sending fine — and the
obvious reading of that alert is "Celery is down", which would send somebody
looking at the wrong thing.

They did not disagree. This is here so they cannot start to, which is the same
argument `src/impersonation.py` and `src/utils/security_utils.get_client_ip`
were extracted on.

⚠️ AND THE ENVIRONMENT READ IS THE SUBTLE PART. `LOG_DIR` is read from the
process environment at call time, and `os.path.join` discards everything to the
left of an absolute component — so with `LOG_DIR=/tmp` the path is `/tmp/...`
and `BASE_DIR` is silently irrelevant. That is correct behaviour for an
absolute setting and it is a trap for a test: `src/test_digest_watchdog.py`
overrode `BASE_DIR` to a temporary directory and was defeated by CI's
`LOG_DIR: /tmp`, so three tests failed in CI and nowhere else. The tests now
pin the variable as well as the setting; see
`src/test_environment_independence.py` for the general form.
"""
import os

from django.conf import settings

#: File name written by the digest task on a successful send, and read by
#: `manage.py check_digest_freshness`.
HEARTBEAT_FILENAME = 'last_digest_sent'


def digest_heartbeat_path():
    """
    Absolute path to the digest heartbeat file.

    `LOG_DIR` may be relative (joined onto `BASE_DIR`, the normal case) or
    absolute (used as-is, which is what CI does).
    """
    log_dir = os.path.join(str(settings.BASE_DIR), os.getenv('LOG_DIR', 'logs'))
    return os.path.join(log_dir, HEARTBEAT_FILENAME)
