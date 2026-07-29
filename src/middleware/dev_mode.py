"""
Dev-mode middleware — captures SQL (with stacks) and injects the panel.

WHY execute_wrapper AND NOT force_debug_cursor
----------------------------------------------
The first version read `connection.queries_log`, which needs
`force_debug_cursor` in production and gives you only SQL and duration. That was
enough to say "6× the same query" and not enough to say *where from* — so two
duplicate groups could render identically with no way to tell them apart, which
is precisely what sent this back for a second pass (07-28-26).

`connection.execute_wrapper` is a documented Django hook that sees every query
regardless of DEBUG, and lets us time it, read `cursor.rowcount`, and capture a
stack at the moment of execution. No global connection state is mutated.

MIDDLEWARE ORDER
----------------
Register LAST in MIDDLEWARE. Request phase runs last, so `request.user` and
`request.csp_nonce` exist. Response phase runs first, so the panel is in the
body before InputSanitizationMiddleware computes the CSP header, and the
panel's nonce-bearing script is not blocked in production.

CACHING
-------
Dev responses are `private, no-store`: they are user-specific and carry SQL.
Cloudflare has served cached responses across users before (07-18 seal bug).
"""

import time

from django.db import connection

from src.dev_mode import (
    analyse_shapes,
    capture_stack,
    current_template_frames,
    dev_mode_enabled_for,
    find_duplicate_queries,
    get_recorder,
    install_template_instrumentation,
    install_template_node_instrumentation,
    start_recording,
    stop_recording,
)

# Wrapping the template backend has to happen once, before any render. Doing it
# at middleware import is the earliest reliable point that doesn't require an
# AppConfig.ready() hook. No-op when dev mode is off.
install_template_instrumentation()

# v3.17.3: per-node wrapping, so a query fired during rendering can name the
# template expression that caused it instead of pointing at the view's
# `render()` call. See the long note in src/dev_mode.py.
install_template_node_instrumentation()


class DevModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)

        try:
            active = dev_mode_enabled_for(user)
        except Exception:
            # Dev mode must never be able to 500 a page for anyone.
            active = False

        request.dev_mode_active = active
        if not active:
            return self.get_response(request)

        recorder = start_recording()
        started = time.monotonic()

        try:
            with connection.execute_wrapper(self._record_query):
                response = self.get_response(request)
        finally:
            recorder.total_ms = round((time.monotonic() - started) * 1000, 1)
            try:
                recorder.duplicates = find_duplicate_queries(recorder.queries)
                recorder.shapes = analyse_shapes(recorder.queries)
            except Exception:
                recorder.duplicates = []
                recorder.shapes = []

        try:
            self._attach_rows(recorder)
            self._record_capabilities(request, recorder)
            self._record_request_info(request, response, recorder)
            self._inject_panel(request, response, recorder)
            response['Cache-Control'] = 'private, no-store, max-age=0'
            response['X-Parliament-Dev-Mode'] = '1'
        except Exception:
            # A broken panel must not take the page down with it.
            pass
        finally:
            stop_recording()

        return response

    # ------------------------------------------------------------------
    @staticmethod
    def _record_query(execute, sql, params, many, context):
        """
        execute_wrapper hook. Must always call through, even on failure, or the
        query is swallowed and the page breaks.
        """
        recorder = get_recorder()
        if recorder is None:
            return execute(sql, params, many, context)

        started = time.monotonic()
        try:
            return execute(sql, params, many, context)
        finally:
            try:
                duration = (time.monotonic() - started) * 1000
                cursor = context.get('cursor')
                rows = getattr(cursor, 'rowcount', None) if cursor is not None else None
                recorder.record_query(
                    sql=sql,
                    params=_short_params(params),
                    ms=duration,
                    rows=rows,
                    stack=capture_stack(),
                    template=current_template_frames(),
                    raw_params=None if many else params,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    @staticmethod
    def _attach_rows(recorder):
        """
        Re-run each SELECT to show what it returned, subject to redaction.

        Done here — after the view, before the panel renders — rather than at
        capture time, because the original cursor is consumed by Django and
        there is no way to read it twice. See src/dev_mode_rows.py for the
        redaction policy; the short version is that it fails closed and that
        judicial, ballot and credential tables are withheld whole.

        Bounded on purpose: only the first N distinct queries get rows, so a
        page with 200 queries does not silently double its own workload just
        because someone left dev mode on.
        """
        from src.dev_mode_rows import fetch_rows

        budget = 40
        for query in recorder.queries:
            if budget <= 0:
                query['rows_note'] = 'not inspected — per-request budget reached'
                continue
            budget -= 1
            try:
                columns, rows, note = fetch_rows(query['sql'], query.get('raw_params'))
            except Exception:                      # noqa: BLE001
                columns, rows, note = None, None, 'inspector error'
            query['row_columns'] = columns
            query['row_values'] = rows
            query['rows_note'] = note

    @staticmethod
    def _record_capabilities(request, recorder):
        """
        A standing summary of what this user *is*.

        Decorators only record when they fire. Plenty of authorization in this
        codebase is an inline `if request.user.is_admin:` inside a view — there
        are ~31 of those — and instrumenting every one would be churn for little
        gain. Showing the user's capability flags answers the same question
        ("why can/can't I see this?") for all of them at once, and costs a
        couple of queries on a dev-only request.
        """
        user = getattr(request, 'user', None)
        if user is None or not getattr(user, 'is_authenticated', False):
            recorder.capabilities = [{'label': 'user', 'value': 'anonymous'}]
            return

        from src.dev_mode import DEV_USER_IDS

        def safe(fn, default='?'):
            try:
                return fn()
            except Exception:
                return default

        try:
            from src.view.admin_v2 import ALLOWED_USER_IDS
        except Exception:
            ALLOWED_USER_IDS = set()

        caps = [
            ('user_id', getattr(user, 'user_id', '—')),
            ('member_type', getattr(user, 'member_type', '—')),
            ('member_status', getattr(user, 'member_status', '—')),
            ('is_admin', getattr(user, 'is_admin', False)),
            ('is_officer', safe(lambda: user.is_officer)),
            ('is_pledge', safe(lambda: user.is_pledge)),
            ('can_view_officer_pages', safe(lambda: user.can_view_officer_pages)),
            ('has_cnb_permission', safe(lambda: user.has_cnb_permission)),
            ('roles', safe(lambda: ', '.join(user.roles.values_list('code', flat=True)) or 'none')),
            ('django is_staff', getattr(user, 'is_staff', False)),
            ('django is_superuser', getattr(user, 'is_superuser', False)),
            ('admin-v2 allowlisted', str(getattr(user, 'user_id', '')) in ALLOWED_USER_IDS),
            ('admin-v2 session', bool(request.session.get('admin_v2_authenticated'))
             if hasattr(request, 'session') else '—'),
            ('dev-mode allowlisted', str(getattr(user, 'user_id', '')) in DEV_USER_IDS),
        ]
        recorder.capabilities = [{'label': k, 'value': v} for k, v in caps]

    @staticmethod
    def _record_request_info(request, response, recorder):
        match = getattr(request, 'resolver_match', None)
        view = ''
        if match is not None:
            view = f'{getattr(match.func, "__module__", "")}.{getattr(match.func, "__name__", "")}'

        db_ms = round(sum(q['ms'] for q in recorder.queries), 1)
        total = recorder.total_ms or 0.0

        recorder.request_info = {
            'method': request.method,
            'path': request.path,
            'url_name': getattr(match, 'url_name', '') or '(unresolved)',
            'namespace': getattr(match, 'namespace', '') or '—',
            'view': view or '(unknown)',
            'args': repr(getattr(match, 'args', ())) if match else '()',
            'kwargs': repr(getattr(match, 'kwargs', {})) if match else '{}',
            'status': getattr(response, 'status_code', '?'),
            'user': getattr(getattr(request, 'user', None), 'user_id', '—'),
            'db_ms': db_ms,
            'python_ms': round(max(total - db_ms, 0.0), 1),
            'total_ms': total,
        }

    # ------------------------------------------------------------------
    def _inject_panel(self, request, response, recorder):
        """Splice the panel in before </body>, if this is a normal HTML page."""
        if getattr(response, 'streaming', False):
            return
        if 'text/html' not in response.get('Content-Type', ''):
            return
        if not getattr(response, 'content', None):
            return

        from django.template.loader import render_to_string

        body = response.content.decode(response.charset or 'utf-8', errors='replace')
        marker = '</body>'
        if marker not in body:
            return

        panel = render_to_string(
            'dev/panel.html',
            {
                'dev': recorder,
                'dev_queries': recorder.queries,
                'dev_duplicates': recorder.duplicates,
                'dev_shapes': recorder.shapes,
                'dev_request': recorder.request_info,
                'request': request,
            },
            request=request,
        )

        index = body.rfind(marker)
        response.content = (body[:index] + panel + body[index:]).encode(
            response.charset or 'utf-8'
        )
        if response.has_header('Content-Length'):
            response['Content-Length'] = str(len(response.content))


def _short_params(params):
    """
    Params for display. Truncated — they can be large, and they are not the point.

    WHY PARAMS ARE SHOWN WHEN ROW VALUES ARE NOT
    --------------------------------------------
    This is the one place the panel carries *values* rather than metadata, and
    that is a deliberate exception to the rule stated at the top of
    `src/dev_mode.py` and enforced on the Shapes tab, which deliberately shows
    row counts and never row contents. Recorded here because the exception is
    not obvious and a future reader will otherwise assume the rule is
    panel-wide (07-29-26 auto-run, finding 4).

    The reasoning: the Shapes tab would have to *re-run* a captured SELECT to
    show rows, which turns the developer allowlist into a read-anything key
    that bypasses every app-level gate — the bypass class v3.16.0–v3.16.3 spent
    four releases closing. Params are different in kind. They are the values the
    *current request* supplied: filter values the developer already holds, and
    on a write, the payload the developer is themselves submitting. They reveal
    nothing the developer could not read by looking at the form they just
    filled in.

    That is a narrower claim than "params are safe", so keep it true. Before
    instrumenting a new query path, ask whether its params could contain data
    the developer is not cleared for — a list of PKs gathered under someone
    else's permission, an identity resolved server-side, a token. Anonymous
    poll responses are the near-miss worth knowing about: the respondent-id
    lookup at `announcement_polls.py` takes a list of user PKs as a param, and
    it is only outside this exception because the view already refuses to build
    that list below the reveal threshold. If a case like that ever falls the
    other way, redact here rather than arguing the exception wider.
    """
    try:
        text = repr(params)
    except Exception:
        return '<unrepresentable>'
    return text[:200] + ('…' if len(text) > 200 else '')
