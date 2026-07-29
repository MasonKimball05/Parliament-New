"""
Developer mode — an information-dense debug overlay for the site.

WHAT IT IS
----------
A per-request recorder plus a floating panel that answers "why did this page do
that?" without a shell: every SQL query with duplicate/N+1 detection, every
feature flag that was consulted and what it returned, every permission gate that
was evaluated, and hover metadata on any value a template opts into with
``{% dev_value %}``.

THE TWO-FACTOR GATE
-------------------
Dev mode is active only when BOTH hold:

  1. ``request.user.user_id`` is in ``ADMIN_V2_USER_IDS`` (the same env var that
     gates Admin v2 — deliberately reused so there is one list of developer
     accounts to maintain), and
  2. the user has explicitly switched it on in their preferences.

(2) exists so that merely being on the allowlist doesn't put debug chrome on
every page for the rest of time. It also means a stolen session on a dev account
doesn't automatically surface the panel — the attacker has to take a second,
audited action first.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
**Dev mode never widens what data you can see.** It shows *metadata* — SQL,
timings, flag results, permission outcomes, model/PK/field names — and record
*content* only through whatever gate already governs it. `{% dev_value %}` on a
Kai allegation body still renders nothing for a reviewer without
``can_view_report_details``; it will tell you the field is gated, and which
check gated it, which is the useful part anyway.

This is a deliberate choice, not an oversight. Per CLAUDE.md's standing rule,
being an operator is not a grant of judicial, deliberative or ballot-level
access; the dev allowlist is an operational role and will be handed to a
successor. If dev mode bypassed the gates it would quietly become the master key
to Kai reports, anonymous ballots and slating notes — the exact bypass class
v3.16.0–v3.16.3 spent four releases closing. If you ever need to read the
underlying record, use the app's own permission system and leave a trail.
"""

import os
import re
import traceback
from contextvars import ContextVar
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------
#
# Parsed from the SAME env vars as src/view/admin_v2.py. It is duplicated rather
# than imported because admin_v2 pulls in a large view module and importing it
# from middleware/templatetags creates an import cycle. `test_dev_mode.py`
# asserts the two sets are identical, so the duplication cannot silently drift.
_raw_dev_ids = os.environ.get('ADMIN_V2_USER_IDS', os.environ.get('ADMIN_V2_USER_ID', ''))
DEV_USER_IDS = {uid.strip() for uid in _raw_dev_ids.split(',') if uid.strip()}

# Where the opt-in lives inside UserPreferences.prefs (a JSONField, so this
# needs no migration — see the note on the model).
PREF_SECTION = 'dev'
PREF_KEY = 'enabled'


def user_may_use_dev_mode(user) -> bool:
    """Factor 1: is this account on the developer allowlist?"""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    user_id = getattr(user, 'user_id', None)
    return bool(user_id) and str(user_id) in DEV_USER_IDS


def dev_mode_enabled_for(user) -> bool:
    """
    Both factors: on the allowlist AND switched on in preferences.

    Reads through the same cache key the ``user_preferences`` context processor
    uses, so this costs no extra query on a normal request.
    """
    if not user_may_use_dev_mode(user):
        return False

    from django.core.cache import cache
    from src.models import UserPreferences

    cache_key = f'user_prefs_{user.pk}'
    preferences = cache.get(cache_key)
    if preferences is None:
        preferences, _ = UserPreferences.objects.get_or_create(user=user)
        cache.set(cache_key, preferences, 300)

    return bool((preferences.prefs or {}).get(PREF_SECTION, {}).get(PREF_KEY, False))


def set_dev_mode(user, enabled: bool):
    """
    Flip the preference. Callers MUST have checked ``user_may_use_dev_mode``
    first — this function does not gate, it only writes.
    """
    from django.core.cache import cache
    from src.models import UserPreferences

    preferences, _ = UserPreferences.objects.get_or_create(user=user)
    prefs = dict(preferences.prefs or {})
    section = dict(prefs.get(PREF_SECTION, {}))
    section[PREF_KEY] = bool(enabled)
    prefs[PREF_SECTION] = section
    preferences.prefs = prefs
    preferences.save(update_fields=['prefs', 'updated_at'])
    cache.delete(f'user_prefs_{user.pk}')
    return preferences


# --------------------------------------------------------------------------
# Per-request recorder
# --------------------------------------------------------------------------
#
# ContextVar, not threading.local: this app is served by Daphne (ASGI), where a
# single thread interleaves many requests. A thread-local would leak one
# request's SQL into another's panel.
_recorder: ContextVar = ContextVar('parliament_dev_recorder', default=None)


class DevRecorder:
    """Collects everything the panel shows for one request."""

    def __init__(self):
        self.flags = []          # (name, result, source)
        self.permissions = []    # (label, result, detail)
        self.notes = []          # (label, value)
        self.objects = []        # dicts from the {% dev_value %} tag
        self.queries = []        # dicts: sql, params, ms, stack, rows, tables
        self.duplicates = []     # [(shape, count, ms, sample, stacks)]
        self.shapes = []         # per-shape analysis for the Shapes tab
        self.templates = []      # (name, context_keys)
        self.request_info = {}   # url name, view, args, middleware, status
        self.capabilities = []   # standing capability flags for this user
        self.total_ms = None

    # -- collection API (safe to call from anywhere; no-ops when inactive) --
    def record_flag(self, name, result, source=''):
        """
        Deduped on (name, source, result): a nav template can consult the same
        flag a dozen times in one render, and twelve identical rows tell you
        nothing. `count` keeps the information without the noise.
        """
        key = (name, source, bool(result))
        for existing in self.flags:
            if (existing['name'], existing['source'], bool(existing['result'])) == key:
                existing['count'] += 1
                return
        self.flags.append(
            {'name': name, 'result': result, 'source': source, 'count': 1}
        )

    def record_permission(self, label, result, detail=''):
        self.permissions.append({'label': label, 'result': result, 'detail': detail})

    def record_note(self, label, value):
        self.notes.append({'label': label, 'value': value})

    def record_object(self, info: dict):
        self.objects.append(info)

    def record_template(self, name, context_keys):
        self.templates.append({'name': name, 'keys': context_keys})

    def record_query(self, sql, params, ms, rows, stack, template=None,
                     raw_params=None):
        self.queries.append({
            'sql': sql,
            'params': params,
            # The real parameter values, kept only so the row inspector can
            # re-run the statement (src/dev_mode_rows.py). Never rendered —
            # the panel shows the truncated `params` repr above.
            'raw_params': raw_params,
            'ms': ms,
            'rows': rows,
            'stack': stack,
            # v3.17.3: the template frames rendering when this query fired.
            # Empty for queries the view issued itself — which is exactly the
            # distinction you want, because a query with template frames is by
            # definition a lazy load the view failed to prefetch.
            'template': template or [],
            'tables': extract_tables(sql),
        })

    # -- derived --
    @property
    def query_count(self):
        return len(self.queries)

    @property
    def query_ms(self):
        return round(sum(q['ms'] for q in self.queries), 1)

    @property
    def n_plus_one_count(self):
        return len(self.duplicates)


def get_recorder():
    """The active recorder, or None when dev mode is off. Never raises."""
    return _recorder.get()


# --------------------------------------------------------------------------
# Template attribution
# --------------------------------------------------------------------------
#
# THE PROBLEM THIS SOLVES (v3.17.3)
# ---------------------------------
# A query fired lazily during template rendering had a useless stack. Django's
# own frames are stripped (rightly — "came from QuerySet._fetch_all" is never
# the answer), which for a template-triggered query leaves exactly one project
# frame: the view's `return render(...)` line. So the panel would show six
# identical member fetches, all attributed to `home.py:280`, and the actual
# cause — `{{ announcement.posted_by.get_display_name }}` on line 317 of
# home_modern.html — was nowhere on screen. You could tell there was an N+1 and
# not which template expression caused it, which is most of the work.
#
# HOW
# ---
# `Node.render_annotated` is the one method every template node passes through:
# `NodeList.render` calls it for each child, and unlike `render` it is never
# overridden by node subclasses. Wrapping it lets us keep a stack of
# (template, line, source text) for whatever is rendering right now, and stamp
# each query with the innermost few frames.
#
# Every node carries `origin.template_name` and `token.lineno`, and
# `token.contents` is the literal source — `announcement.posted_by.get_display_name`
# — so the panel can name the exact expression rather than just a line number.
#
# COST
# ----
# One ContextVar read per node when dev mode is off, which is why the guard is
# the first thing in the wrapper. When it is on, a push/pop per non-text node —
# a few hundred per page, against a request that is already doing real work.
_template_stack: ContextVar = ContextVar('parliament_dev_template_stack', default=None)

#: How many enclosing template frames to record per query. The innermost is
#: almost always the answer; the ones above it tell you which include or block
#: it sits in, which is what you need when the culprit is in a component.
MAX_TEMPLATE_FRAMES = 4


def install_template_node_instrumentation():
    """Patch Node.render_annotated once. No-op when already wrapped."""
    from django.template.base import Node

    if getattr(Node.render_annotated, '_parliament_dev_wrapped', False):
        return

    original = Node.render_annotated

    def render_annotated(self, context):
        stack = _template_stack.get()
        if stack is None:                      # dev mode off — straight through
            return original(self, context)

        token = getattr(self, 'token', None)
        if token is None:                      # TextNode and friends: no source
            return original(self, context)

        stack.append((
            getattr(getattr(self, 'origin', None), 'template_name', None) or '(inline)',
            getattr(token, 'lineno', None),
            (getattr(token, 'contents', '') or '').strip()[:120],
        ))
        try:
            return original(self, context)
        finally:
            stack.pop()

    render_annotated._parliament_dev_wrapped = True
    Node.render_annotated = render_annotated


def current_template_frames():
    """
    The innermost few template frames, outermost first, as display dicts.

    Empty when nothing is rendering — which is the honest answer for a query
    the view issued itself, and is how the panel tells the two apart.
    """
    stack = _template_stack.get()
    if not stack:
        return []
    return [
        {'template': name, 'line': lineno, 'source': source}
        for name, lineno, source in stack[-MAX_TEMPLATE_FRAMES:]
    ]


def install_template_instrumentation():
    """
    Patch the Django template backend once so renders can be recorded.

    Django caches compiled templates and offers no per-render signal outside the
    test runner, so there is no hook to attach to — this single wrap at import
    time is the accepted approach (django-debug-toolbar does the same).

    The wrapper is a no-op when dev mode is off: one ContextVar lookup, then
    straight through to the original. Idempotent — safe if imported twice.
    """
    from django.template.backends.django import Template as DjangoTemplate

    if getattr(DjangoTemplate.render, '_parliament_dev_wrapped', False):
        return

    original = DjangoTemplate.render

    def render(self, context=None, request=None):
        recorder = _recorder.get()
        if recorder is not None:
            try:
                recorder.record_template(
                    getattr(self.template, 'name', None) or '(inline)',
                    sorted(k for k in (context or {}) if not k.startswith('_')),
                )
            except Exception:
                pass
        return original(self, context, request)

    render._parliament_dev_wrapped = True
    DjangoTemplate.render = render


def start_recording():
    recorder = DevRecorder()
    _recorder.set(recorder)
    # A live list is also the "dev mode is on" signal for the node wrapper, so
    # it has to be set here and cleared in stop_recording().
    _template_stack.set([])
    return recorder


def stop_recording():
    _recorder.set(None)
    _template_stack.set(None)


# -- convenience wrappers used by instrumented code -------------------------
# These are deliberately cheap and exception-proof: instrumentation must never
# be able to break a page. A single attribute lookup when dev mode is off.

def record_flag(name, result, source=''):
    recorder = _recorder.get()
    if recorder is not None:
        try:
            recorder.record_flag(name, result, source)
        except Exception:
            pass


def record_permission(label, result, detail=''):
    recorder = _recorder.get()
    if recorder is not None:
        try:
            recorder.record_permission(label, result, detail)
        except Exception:
            pass


def record_note(label, value):
    recorder = _recorder.get()
    if recorder is not None:
        try:
            recorder.record_note(label, value)
        except Exception:
            pass


def record_template(name, context_keys):
    recorder = _recorder.get()
    if recorder is not None:
        try:
            recorder.record_template(name, context_keys)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Stack capture
# --------------------------------------------------------------------------
#
# The single most useful thing the SQL panel shows. "6× the same query" tells
# you there's an N+1; the stack tells you which loop. It is also the only way to
# tell two identical-looking duplicate groups apart — which is exactly what sent
# this feature back for a second pass (07-28-26).
_SKIP_PATH_FRAGMENTS = (
    'site-packages',
    'dist-packages',
    '/src/dev_mode.py',
    '/src/middleware/dev_mode.py',
    'lib/python',
)

MAX_STACK_FRAMES = 6


def capture_stack():
    """
    Project-only stack frames, innermost last, as ('file:line', 'func', 'code').

    Django's own frames are stripped: knowing a query came from
    `QuerySet._fetch_all` is never the answer. What you want is the line in
    Parliament that triggered it.
    """
    frames = []
    for frame in traceback.extract_stack()[:-1]:
        filename = frame.filename
        if any(fragment in filename for fragment in _SKIP_PATH_FRAGMENTS):
            continue
        try:
            short = str(Path(filename).relative_to(BASE_DIR))
        except ValueError:
            continue  # outside the project entirely
        frames.append({
            'where': f'{short}:{frame.lineno}',
            'func': frame.name,
            'code': (frame.line or '').strip()[:160],
        })
    return frames[-MAX_STACK_FRAMES:]


# --------------------------------------------------------------------------
# SQL normalisation / duplicate detection
# --------------------------------------------------------------------------
_LITERAL_PATTERNS = [
    (re.compile(r"'[^']*'"), "'?'"),        # string literals
    (re.compile(r'\b\d+\b'), '?'),          # numbers
    (re.compile(r'\s+'), ' '),              # collapse whitespace
]

# How many identical-shape queries before we call it an N+1. Three is noise
# (a page legitimately fetching three of the same thing); four upward is a loop.
N_PLUS_ONE_THRESHOLD = 4


def normalize_sql(sql: str) -> str:
    """Strip literals so `WHERE id = 1` and `WHERE id = 2` group together."""
    out = sql
    for pattern, replacement in _LITERAL_PATTERNS:
        out = pattern.sub(replacement, out)
    return out.strip()


def find_duplicate_queries(queries):
    """
    Group queries by shape and return the groups that look like an N+1,
    worst first. Each entry: (shape, count, total_ms, sample_sql, stacks).

    `stacks` is what makes this actionable — and what distinguishes two
    duplicate groups whose SQL renders identically on screen. Before 07-28-26
    the panel showed two indistinguishable "6× the same query" blocks with no
    way to tell which loop produced which.
    """
    groups = {}
    for query in queries:
        key = normalize_sql(query['sql'])
        entry = groups.setdefault(
            key,
            {'count': 0, 'ms': 0.0, 'sample': query['sql'], 'stacks': [],
             'templates': []},
        )
        entry['count'] += 1
        entry['ms'] += query['ms']
        origin = query.get('stack') or []
        if origin:
            caller = origin[-1]['where']
            if caller not in [s['where'] for s in entry['stacks']]:
                entry['stacks'].append(origin[-1])

        # v3.17.3: the template expression that fired it, if any. For a lazy
        # load this is the whole answer, and the Python stack is not — every
        # query in the group will share the view's `render()` line, so grouping
        # by Python caller alone made six member fetches look like one
        # unexplained blob.
        frames = query.get('template') or []
        if frames:
            innermost = frames[-1]
            label = f"{innermost['template']}:{innermost['line']}"
            if label not in [t['where'] for t in entry['templates']]:
                entry['templates'].append({
                    'where': label,
                    'source': innermost['source'],
                    'via': ' → '.join(
                        f"{f['template']}:{f['line']}" for f in frames[:-1]
                    ),
                })

    duplicates = [
        (key, entry['count'], round(entry['ms'], 1), entry['sample'],
         entry['stacks'], entry['templates'])
        for key, entry in groups.items()
        if entry['count'] >= N_PLUS_ONE_THRESHOLD
    ]
    duplicates.sort(key=lambda row: row[1], reverse=True)
    return duplicates


# --------------------------------------------------------------------------
# Shape analysis — metadata only, deliberately no row values
# --------------------------------------------------------------------------
#
# Per CLAUDE.md's confidentiality boundary and the dev-mode rule in this
# module's docstring, this tab reports the SHAPE of what a query did, never the
# data it returned. Re-running captured SELECTs to display rows would make the
# developer allowlist a read-anything key to Kai reports, ballots and slating
# notes, bypassing every app-level gate. Row counts, tables and access pattern
# are what you need to optimize a query; the values are not.

_TABLE_RE = re.compile(r'(?:FROM|JOIN|UPDATE|INTO)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', re.I)


def extract_tables(sql):
    """Table names touched, in order of first appearance."""
    seen = []
    for name in _TABLE_RE.findall(sql or ''):
        if name.upper() in ('SELECT', 'VALUES') or name in seen:
            continue
        seen.append(name)
    return seen


def classify_query(sql):
    """
    A one-line verdict on the access pattern, derived from the SQL text alone.

    Cheap and approximate on purpose — it runs on every dev request and must not
    itself hit the database. EXPLAIN would be exact but costs a round trip per
    query and, on Postgres, needs care not to execute anything.
    """
    text = ' '.join((sql or '').split())
    upper = text.upper()

    if upper.startswith(('INSERT', 'UPDATE', 'DELETE')):
        verb = upper.split()[0]
        return f'{verb} — a write. On a GET request this is worth questioning.'

    if not upper.startswith('SELECT'):
        return 'non-SELECT statement'

    parts = []
    if ' JOIN ' in upper:
        parts.append(f"{upper.count(' JOIN ')} join(s)")
    if 'COUNT(' in upper:
        parts.append('aggregate COUNT')
    if ' WHERE ' not in upper:
        parts.append('NO WHERE — full table scan')
    elif re.search(r'WHERE\s+"?\w+"?\."?id"?\s*=|WHERE\s+"?\w+"?\."?\w*_id"?\s*=', text, re.I):
        parts.append('single-row lookup by key')
    if ' LIMIT 21' in upper:
        parts.append('.get() — LIMIT 21 is Django probing for MultipleObjectsReturned')
    if ' ORDER BY ' in upper and ' LIMIT ' not in upper:
        parts.append('ORDER BY with no LIMIT — sorts the whole result set')

    return '; '.join(parts) if parts else 'simple filtered SELECT'


def analyse_shapes(queries):
    """
    Collapse queries to unique shapes with counts, timings, tables, rows and a
    verdict. This is the Shapes tab — the safe substitute for a raw results
    viewer.
    """
    groups = {}
    for query in queries:
        key = normalize_sql(query['sql'])
        entry = groups.setdefault(key, {
            'sample': query['sql'],
            'count': 0,
            'ms': 0.0,
            'rows': 0,
            'rows_known': False,
            'tables': query.get('tables') or [],
            'origins': [],
        })
        entry['count'] += 1
        entry['ms'] += query['ms']
        if query.get('rows') is not None and query['rows'] >= 0:
            entry['rows'] += query['rows']
            entry['rows_known'] = True
        stack = query.get('stack') or []
        if stack:
            where = stack[-1]['where']
            if where not in entry['origins']:
                entry['origins'].append(where)

    shapes = []
    for key, entry in groups.items():
        shapes.append({
            'sample': entry['sample'],
            'count': entry['count'],
            'ms': round(entry['ms'], 1),
            'rows': entry['rows'] if entry['rows_known'] else None,
            'tables': entry['tables'],
            'origins': entry['origins'][:4],
            'verdict': classify_query(entry['sample']),
        })
    shapes.sort(key=lambda s: (s['count'], s['ms']), reverse=True)
    return shapes
