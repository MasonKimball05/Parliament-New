"""
Performance monitoring middleware for tracking request times and server health.

Uses Django's cache backend (Redis on prod) so metrics are shared across all
Gunicorn workers — process-local dicts would always show 0 on multi-worker setups.

Entries are stored as a Python list of tuples via pickle; no JSON layer.
"""
import logging
import pickle
import random
import time
from datetime import timedelta
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = 'perf_requests'
MAX_STORED = 500
CACHE_TTL = 60 * 60 * 25  # 25 hours


#: Requests slower than this are ALWAYS stored, never sampled away. This is the
#: data the dashboard and every N+1 hunt actually use.
ALWAYS_STORE_ABOVE_MS = 1000

#: One in N of everything else. See `_append_metric` for why sampling at all.
SAMPLE_ONE_IN = 20

#: Exact request/sample counters, so sampling does not make the totals lies.
COUNT_KEY = 'perf_request_count'
SAMPLED_KEY = 'perf_sampled_count'
COUNTER_TTL = CACHE_TTL

#: ⚠️ v3.19.5 — HOW MANY BYTES PER ENTRY IS TOO MANY, and why it is a per-entry
#: number rather than a total.
#:
#: `memory_report` warns when this buffer costs too much memory. That check has
#: now been wrong three releases running, in both directions:
#:
#:   * `total_requests > MAX_STORED * 0.8` — always true once the counter went
#:     unbounded (v3.19.3 caught this).
#:   * `stored_samples > MAX_STORED * 0.8` — always true, because a full ring
#:     buffer is the steady state of a ring buffer (v3.19.4 caught this, and
#:     recorded the right rule: *before writing a threshold, ask what the world
#:     looks like when it is NOT crossed*).
#:   * `buffer_bytes > 512 * 1024` — **never** true, at ~38x a full buffer.
#:     The rule above has a mirror nobody wrote down: **also ask what the world
#:     looks like when it IS crossed.** If the answer is "no world", the check is
#:     decoration, and a decorative check is worse than none because it reads as
#:     coverage.
#:
#: ⚠️ AND THE NUMBER ALL THREE WERE REASONED FROM IS ITSELF WRONG — PICKLE
#: MEMOISES. `_append_metric` records "500 entries of the real tuple shape →
#: 13,693 bytes", i.e. ~27 B/entry, and that has been quoted as the size of this
#: buffer ever since. It is not. Re-measured 08-09-26 on the same tuple shape:
#:
#:     500 entries, one shared timestamp + one shared path      13,577 B   27 B
#:     500 entries, realistic mix of ~10 distinct app routes    22,201 B   44 B
#:     500 entries, every path distinct (`/legislation/<id>/`)  33,428 B   67 B
#:     500 entries, every path distinct, 200 chars             125,937 B  252 B
#:     500 entries, every path distinct, 2 KB (scanner sweep) 1,029,673 B 2059 B
#:
#: The first line reproduces 13,693 to within 116 bytes, which is what it was:
#: a synthetic buffer whose entries were all *the same objects*, so `pickle`
#: wrote the timestamp and the path once and back-referenced them 499 times. A
#: real buffer holds 500 different requests. **The true steady-state cost is
#: 22–33 KB, not 13.7 KB** — a 1.6-2.5x understatement that has been load-bearing
#: for three releases of threshold arithmetic. The general form is worth keeping:
#: **a fixture whose rows are identical does not measure serialisation, because
#: every serialiser worth using deduplicates.**
#:
#: So: a budget per entry, multiplied by the bound that actually governs the
#: buffer. 192 B/entry sits ~3-4x above realistic traffic (44-67 B) and below a
#: buffer of 200-character paths (252 B) — so it is false in the steady state,
#: true when paths grow enough to matter, and **it keeps meaning the same thing
#: after someone changes `MAX_STORED`.** A fixed total would not; that is how the
#: 512 KB came to be stale before it was even written.
#:
#: `path` is what varies (the rest of the tuple is three numbers and a
#: timestamp), so a crossing means paths grew, not that traffic did.
BYTES_PER_ENTRY_BUDGET = 192


def _bump(key):
    """
    `cache.incr`, creating the key if it is missing.

    `incr` raises ValueError on a missing key rather than starting at zero, and
    the key expires on `COUNTER_TTL` — so the miss is normal, not exceptional,
    and has to be handled rather than logged. The set-after-miss races with
    other workers and can lose a count at the moment of expiry; that is
    acceptable for a counter whose purpose is "roughly how much traffic",
    and it is strictly better than the read-modify-write it replaced.
    """
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, COUNTER_TTL)
        return 1


def _append_metric(entry: tuple):
    """
    Record one (timestamp, duration_ms, path, db_queries, db_time_ms) sample.

    ⚠️ v3.19.3 — THIS IS SAMPLED NOW, AND THE REASON IS THAT IT USED TO BE THE
    MOST EXPENSIVE THING IN THE MIDDLEWARE CHAIN.

    It was a read-modify-write of the WHOLE history on EVERY request:
    `cache.get` (unpickle + zlib-decompress a 500-entry list), append one tuple,
    `cache.set` (re-pickle + re-compress all 500). `PerformanceMiddleware` is
    second in `MIDDLEWARE` and exempts only `/static/` and `/health/`, so every
    page, every `/media/` download, every 404 and every scanner probe paid it.

    Measured on the configured serialisation path (pickle protocol 4 +
    django-redis' ZlibCompressor), 500 entries of the real tuple shape:

        pickled            13,693 bytes
        after zlib          9,582 bytes
        CPU round trip      ~0.25 ms per request
        Redis traffic       ~19 KB per request, in 2 serialised round trips

    ⚠️ v3.19.5 — THOSE FIGURES ARE AN UNDERSTATEMENT AND THE REASON IS PICKLE
    MEMOISATION. Re-measured 08-09-26: 13,577 bytes is what you get from 500
    entries that all share one timestamp and one path string, which is what the
    fixture must have been — `pickle` writes each repeated object once and
    back-references it. A real buffer holds 500 *different* requests and costs
    **22-33 KB** (44-67 B/entry) depending on how many distinct paths are in it.
    The full table is in `BYTES_PER_ENTRY_BUDGET`'s comment.

    **This does not weaken the case for sampling — it strengthens it**, because
    every per-request cost above was measured on the same too-small object. Do
    not re-derive a threshold from the 13,693 figure; use
    `BYTES_PER_ENTRY_BUDGET`, and if you re-measure, vary the path.

    That is plausibly more wall-clock than any single query v3.18.7 removed — and
    v3.18.7 treated each of those as worth fixing. **The instrument built in that
    same release to find this class of problem could not see it**:
    `MiddlewareChainQueryBudgetTests` counts QUERIES, and this is a CACHE cost.
    Same blind spot one resource over, which is why `test_middleware_hot_path`
    now asserts on cache traffic too.

    WHAT SAMPLING COSTS, STATED PLAINLY so nobody is surprised by a number.

    ⚠️ v3.19.4 — THREE CLAIMS IN THIS LIST WERE WRONG WHEN v3.19.3 SHIPPED, and
    they were wrong in a specific way worth naming: they described the design
    that was intended rather than the code that was written. Nobody re-read them
    against the finished function. Corrected below; see `get_performance_summary`.

    * `slow_requests` and `get_slow_requests()` are **not sampled at write
      time** — anything over `ALWAYS_STORE_ABOVE_MS` is stored unconditionally.
      They are NOT "complete": a slow entry lands in the same `MAX_STORED` ring
      buffer as the samples and is evicted FIFO like anything else. Sampling
      made this much better (500 slots now span ~10,000 requests rather than
      500) without making it absolute, and the earlier word for it was too
      strong.
    * `total_requests` is **exact**, because it comes from a `cache.incr`
      counter rather than from `len(entries)`. That is a real improvement: the
      500-entry buffer meant `total_requests` silently saturated at 500.
      `sampled_requests` is exact for the same reason. **`samples_last_hour` is
      NOT** — it is a `len()` over the retained buffer, has never come from a
      counter, and cannot, because a counter has no timestamps to filter on.
    * `avg_response_time_ms` / `avg_db_queries` / `avg_db_time_ms` become
      **estimates over a 1-in-20 sample, biased upward** because slow requests
      are kept preferentially. They were never precise — the buffer overwrote
      itself continuously — but the bias is new and is why `get_performance_summary`
      labels them and why every reader must render that label.
    """
    duration_ms = entry[1]
    _bump(COUNT_KEY)

    if duration_ms <= ALWAYS_STORE_ABOVE_MS:
        # `random` and not a counter: a modulo counter aliases badly against
        # periodic traffic (a beat task every 60 s hitting the same slot), and
        # nothing here needs the sample to be reproducible.
        if random.randrange(SAMPLE_ONE_IN):
            return

    _bump(SAMPLED_KEY)

    entries = cache.get(CACHE_KEY)
    if not isinstance(entries, list):
        entries = []

    entries.append(entry)

    if len(entries) > MAX_STORED:
        entries = entries[-MAX_STORED:]

    cache.set(CACHE_KEY, entries, CACHE_TTL)


def _get_entries() -> list:
    """Return stored entries; always returns a list, never raises."""
    entries = cache.get(CACHE_KEY)
    if not isinstance(entries, list):
        return []
    return entries


def get_performance_metrics():
    """Return raw entries (kept for backwards compat)."""
    return {'requests': _get_entries()}


def buffer_size_bytes(entries=None):
    """
    Uncompressed pickled size of the metrics buffer, in bytes.

    v3.19.5 — lives here rather than in `memory_report` because the number it is
    judged against (`BYTES_PER_ENTRY_BUDGET`) and the measurement that number
    came from (`_append_metric`'s docstring) are both in this module. A threshold
    and the evidence for it belong in the same file; splitting them is how the
    512 KB constant came to be written thirty-eight times too large in a file
    that imports from the one recording the real size.

    `entries` is accepted so a caller that already holds the buffer does not pay
    a second `cache.get` for it — `memory_report` reads it twice per run and
    would otherwise pickle it twice as well.

    **Uncompressed on purpose.** django-redis applies zlib on the way out
    (~9.6 KB for the 13.7 KB measured above), so the wire cost is lower — but
    the number a *memory* report is asked about is what the list costs when it is
    live in a worker's heap, and that is closer to the uncompressed figure. The
    caller says "uncompressed" when it prints it.
    """
    if entries is None:
        entries = _get_entries()
    if not entries:
        return 0
    return len(pickle.dumps(entries, protocol=pickle.HIGHEST_PROTOCOL))


def buffer_is_over_budget(entries=None):
    """
    True when the buffer costs more than `MAX_STORED * BYTES_PER_ENTRY_BUDGET`.

    Returns `(over, buffer_bytes)` so the caller can report the measurement
    whether or not it crossed — a recommendation that cannot say how big the
    thing is is the kind that gets ignored.
    """
    buffer_bytes = buffer_size_bytes(entries)
    return buffer_bytes > MAX_STORED * BYTES_PER_ENTRY_BUDGET, buffer_bytes


def get_performance_summary():
    """
    Return aggregated performance stats for the dashboard.

    ⚠️ THREE KINDS OF NUMBER LIVE IN THIS DICT AND THEY ARE NOT EQUALLY
    TRUSTWORTHY. `_append_metric` samples (see its docstring for why), so —
    **and the key name now says which kind it is, which is the v3.19.4 change**:

    * **Exact counters** (`cache.incr`, unbounded, survive buffer eviction):
      `total_requests` — every request the middleware saw;
      `sampled_requests` — every request it decided to store.
      Their ratio is the *effective* sample rate, which is not `sample_rate`:
      slow requests are stored unconditionally, so the realised ratio is always
      a little richer than 1-in-N and drifts with how slow the site is.
    * **Buffer facts** (bounded by `MAX_STORED`, describe what is retained
      *right now*): `stored_samples`, `samples_last_hour`, `samples_last_5min`.
      ⚠️ **These are not request counts and must never be rendered as traffic.**
      v3.19.3 called the last two `requests_last_hour`/`requests_last_5min` and
      documented them as counter-derived and as "scaled to an estimate of real
      traffic"; they were neither, and no scaling was ever performed. They are
      renamed rather than scaled, because inventing a multiplier would be a
      second wrong number wearing the first one's name. If you want estimated
      traffic in a window, the honest source is `total_requests` sampled at two
      points in time, not this buffer.
    * **Estimates over the retained sample**, biased upward because requests
      above `ALWAYS_STORE_ABOVE_MS` are kept preferentially:
      `avg_response_time_ms`, `avg_db_queries`, `avg_db_time_ms`,
      `max_response_time_ms`, `slow_requests`. `sampled` and `sample_rate` are
      in the dict so **every** reader can label them — `memory_report`,
      `debug_performance_metrics` and the admin-v2 dashboard card all do as of
      v3.19.4. The dashboard was the one that did not, and it is the only one a
      person actually looks at.
    """
    entries = _get_entries()
    total = cache.get(COUNT_KEY) or 0
    # v3.19.4 — this counter has existed since v3.19.3 and nothing read it.
    # `stored_samples` was `len(entries)`, i.e. a saturating count capped at
    # MAX_STORED — the exact failure the same release correctly diagnosed and
    # fixed for `total_requests`, reintroduced sixty lines below the fix, while
    # the counter that answers it was being incremented and thrown away.
    sampled = cache.get(SAMPLED_KEY) or 0

    empty = {
        'total_requests': total,
        'sampled_requests': sampled,
        'avg_response_time_ms': 0,
        'max_response_time_ms': 0,
        'slow_requests': 0,
        'avg_db_queries': 0,
        'avg_db_time_ms': 0,
        'samples_last_hour': 0,
        'samples_last_5min': 0,
        'stored_samples': len(entries),
        'sampled': True,
        'sample_rate': SAMPLE_ONE_IN,
    }

    if not entries:
        return empty

    now = timezone.now()
    hour_ago = now - timedelta(hours=1)
    five_min_ago = now - timedelta(minutes=5)

    recent = [r for r in entries if r[0] >= hour_ago]
    very_recent = [r for r in entries if r[0] >= five_min_ago]

    if not recent:
        return empty

    durations = [r[1] for r in recent]
    db_queries = [r[3] for r in recent]
    db_times = [r[4] for r in recent]

    return {
        'total_requests': total,
        'sampled_requests': sampled,
        'avg_response_time_ms': round(sum(durations) / len(durations), 1),
        'max_response_time_ms': round(max(durations), 1),
        'slow_requests': sum(1 for d in durations if d > ALWAYS_STORE_ABOVE_MS),
        'avg_db_queries': round(sum(db_queries) / len(db_queries), 1),
        'avg_db_time_ms': round(sum(db_times) / len(db_times), 1),
        # v3.19.4 — RAW BUFFER COUNTS, AND THE NAME NOW SAYS SO. Not scaled, not
        # counter-derived, not traffic. See the docstring; the old names claimed
        # all three.
        'samples_last_hour': len(recent),
        'samples_last_5min': len(very_recent),
        'stored_samples': len(entries),
        'sampled': True,
        'sample_rate': SAMPLE_ONE_IN,
    }


def get_slow_requests(threshold_ms=1000, limit=10):
    """Return the N slowest requests tracked so far."""
    entries = _get_entries()
    slow = sorted((r for r in entries if r[1] > threshold_ms), key=lambda x: x[1], reverse=True)
    return slow[:limit]


def clear_old_metrics():
    """Drop entries older than 24 hours."""
    entries = _get_entries()
    cutoff = timezone.now() - timedelta(hours=24)
    kept = [r for r in entries if r[0] >= cutoff]
    cache.set(CACHE_KEY, kept, CACHE_TTL)


class _QueryCounter:
    """
    Counts and times queries via `connection.execute_wrapper`.

    ⚠️ v3.18.7 — WHY NOT `len(connection.queries)`, WHICH IS WHAT THIS REPLACED.
    `connection.queries` is populated only when `connection.queries_logged` is
    true, and that is `force_debug_cursor or settings.DEBUG`
    (django/db/backends/base/base.py:170). Production runs DEBUG=False, so the
    deque is never appended to and the before/after delta was **always 0** —
    for every request, forever. Two consequences, and the second is the one
    that cost something:

      * `avg_db_queries` / `avg_db_time_ms` in `get_performance_summary()` were
        permanently 0;
      * the slow-request alarm below logged `(0 queries, 0ms DB time)` on every
        slow page. In a codebase whose last six weeks are almost entirely N+1
        hunts, an alert that fires on a slow page and reports no database work
        points the investigator away from the answer roughly every time.

    The tell that this was an oversight and not a decision: `db_time_ms` was
    already wrapped in `if settings.DEBUG`, one statement below the unguarded
    count. The author knew DEBUG gates the query log, guarded the timing line,
    and missed the counting line directly above it.

    `execute_wrapper` is independent of DEBUG — it is the same mechanism
    `dev_mode.py:78` uses, which is why prod dev mode could find the N+1s in
    v3.18.3 and v3.18.6 that this middleware could not. Cost is one function
    call and two `perf_counter()` reads per query, which is noise beside the
    query itself.

    Single-database only: `execute_wrapper` is per-connection and this wraps
    `default`. Parliament has one database; if that ever changes, this counts
    a subset rather than reporting zero, which is the better failure.
    """

    __slots__ = ('count', 'total_ms')

    def __init__(self):
        self.count = 0
        self.total_ms = 0.0

    def __call__(self, execute, sql, params, many, context):
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            # In `finally` so a failing query is still counted — a request that
            # is slow *because* queries are erroring is exactly when the number
            # matters.
            self.count += 1
            self.total_ms += (time.perf_counter() - start) * 1000


class PerformanceMiddleware:
    """
    Middleware to track request performance metrics.
    Stores timing data in the shared cache so all Gunicorn workers contribute.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/static/') or request.path == '/health/':
            return self.get_response(request)

        counter = _QueryCounter()
        start_time = time.perf_counter()

        with connection.execute_wrapper(counter):
            response = self.get_response(request)

        duration_ms = (time.perf_counter() - start_time) * 1000

        db_query_count = counter.count
        db_time_ms = counter.total_ms

        try:
            _append_metric((
                timezone.now(),
                duration_ms,
                request.path,
                db_query_count,
                db_time_ms,
            ))
        except Exception:
            pass  # Never let metric collection crash a request

        if duration_ms > 2000:
            logger.warning(
                f"Slow request: {request.path} took {duration_ms:.0f}ms "
                f"({db_query_count} queries, {db_time_ms:.0f}ms DB time)"
            )

        response['Server-Timing'] = f'total;dur={duration_ms:.1f}, db;dur={db_time_ms:.1f}'
        return response
