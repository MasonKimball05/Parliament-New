"""
Database connection-pressure monitor.

Added 09-16-26 after a live incident: a chapter meeting onboarding a batch of
pledges drove enough concurrent logins to exhaust Postgres's max_connections
("remaining connection slots are reserved for roles with the SUPERUSER
attribute"), which 500'd nearly every page — home, login, csp-report, the
maintenance middleware's own flag check — because none of them could get a
connection. `DB_CONN_MAX_AGE=300` (Parliament/settings.py) means a connection
sits open for 5 minutes after the request that opened it finishes, so a burst
of traffic leaves a large idle backlog behind it rather than releasing
connections as soon as the requests are done.

This task doesn't fix that (pgbouncer is the real fix — see the pgbouncer
setup notes, not yet built as of this writing). It exists so the NEXT time
usage climbs toward the ceiling, an officer gets a warning while there is
still room to react (restart services, bump max_connections) instead of
finding out from a room full of pledges who can't log in.

Split into its own module rather than added to security_audit.py — this is
operational/infra pressure, not a security event, even though it reuses the
security-alert delivery pipeline for convenience (SecurityNotificationLog is
already the "something needs an officer's attention" channel with its own
admin-v2 view, cooldown-safe email gating, and existing daily-digest
visibility).
"""
from celery import shared_task
from django.db import connection
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

#: Fraction of max_connections in use before this fires a warning. Chosen
#: from the 09-16-26 incident: connections were sitting at 70/100 (70%)
#: BEFORE the burst that exhausted the remaining 30 — so warning meaningfully
#: below that observed steady-state (rather than just below 100%) is what
#: would have given advance notice that night.
WARNING_THRESHOLD = 0.60

#: Suppress repeat alerts for this many seconds once one has fired, so a
#: sustained high-pressure period (the whole length of a meeting, say)
#: produces one alert rather than one every 5 minutes for its duration.
#: Matches the cooldown pattern in security_notifications.send_watch_flag_alert,
#: just longer — this condition is expected to persist for a while once
#: crossed, unlike a single login event.
ALERT_COOLDOWN_SECONDS = 60 * 60  # 1 hour


def get_connection_pressure():
    """
    Return the current database's connection pressure as a dict:
    `{'current': int, 'max_connections': int, 'ratio': float, 'by_state': [(state, count), ...]}`,
    or `None` on sqlite (dev/test — this condition is Postgres-specific) or if
    the query itself fails (e.g. Postgres is already refusing connections,
    which is exactly the condition this exists to detect in advance).

    Pulled out of `monitor_db_connection_pressure` so `send_daily_digest`
    (src/tasks/notifications.py) can show the same number in its
    infrastructure-health section without a second copy of this query —
    two readers of "how close to the ceiling are we" drifting apart is
    exactly the kind of thing that's easy to not notice until they disagree.

    Deliberately scoped to `current_database()` in the query itself rather
    than passing DB_NAME as a Python string — the same mistake is easy to
    make by hand (an unquoted `datname=parliament_db` in psql is read as a
    column reference, not a literal, and errors) and current_database() also
    means this can never drift from whatever database Django is actually
    pointed at.
    """
    if connection.vendor != 'postgresql':
        return None

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            )
            (current,) = cursor.fetchone()

            cursor.execute(
                "SELECT setting::int FROM pg_settings WHERE name = 'max_connections'"
            )
            (max_connections,) = cursor.fetchone()

            cursor.execute(
                """
                SELECT state, count(*) FROM pg_stat_activity
                WHERE datname = current_database()
                GROUP BY state
                ORDER BY count(*) DESC
                """
            )
            by_state = cursor.fetchall()
    except Exception as exc:
        logger.error(f"[tasks] get_connection_pressure: could not query pg_stat_activity: {exc}")
        return None

    if not max_connections:
        return None

    return {
        'current': current,
        'max_connections': max_connections,
        'ratio': current / max_connections,
        'by_state': by_state,
    }


@shared_task(name='tasks.monitor_db_connection_pressure')
def monitor_db_connection_pressure():
    """
    Poll pg_stat_activity for the current database's connection count against
    max_connections, and file a security alert if usage crosses
    WARNING_THRESHOLD. No-ops entirely on sqlite (dev/test) — this condition
    is Postgres-specific (sqlite has no connection ceiling of this kind).

    Wrapped defensively throughout via `get_connection_pressure()`: if
    Postgres is already refusing connections (the exact failure this task
    exists to give advance warning of), that call returns `None` rather than
    raising — a monitor that crashes the same way the outage does tells you
    nothing you didn't already know from the 500s.
    """
    pressure = get_connection_pressure()
    if pressure is None:
        return

    current = pressure['current']
    max_connections = pressure['max_connections']
    ratio = pressure['ratio']
    by_state = pressure['by_state']

    logger.info(
        f"[tasks] monitor_db_connection_pressure: {current}/{max_connections} "
        f"({ratio:.0%}) — " + ', '.join(f'{state or "unknown"}={n}' for state, n in by_state)
    )

    if ratio < WARNING_THRESHOLD:
        return

    cooldown_key = 'db_connection_pressure_alerted'
    if cache.get(cooldown_key):
        return
    cache.set(cooldown_key, True, ALERT_COOLDOWN_SECONDS)

    state_breakdown = '\n'.join(f'  {state or "unknown"}: {n}' for state, n in by_state)
    details = (
        f"Postgres connection usage is at {current}/{max_connections} ({ratio:.0%}), "
        f"at or above the {WARNING_THRESHOLD:.0%} warning threshold.\n\n"
        f"Breakdown by state:\n{state_breakdown}\n\n"
        f"A high 'idle' count usually means connections are being held open "
        f"(DB_CONN_MAX_AGE) rather than actively used — check for a recent "
        f"traffic burst (e.g. many concurrent logins). If this keeps "
        f"climbing, restart parliament-gunicorn/worker/beat to release held "
        f"connections, or increase max_connections in postgresql.conf "
        f"(requires a Postgres restart)."
    )

    from src.tasks.email import send_security_alert_task
    send_security_alert_task.delay(
        event_type='db_connection_pressure',
        severity='high',
        details=details,
        force_send=True,
    )
