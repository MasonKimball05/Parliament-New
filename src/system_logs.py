"""
System Logs page (11-09-26 rework): what the page shows, built on top of
``src.view.officer.view_logs.read_log_entries`` (the one reader, shared with
the app's System Logs screen, whose output is unchanged).

* **Entries, not lines.** A line the timestamp pattern doesn't parse (a
  traceback, a wrapped message) belongs to the entry logged just before it.
  The reader returns newest first, so those lines arrive *above* their entry;
  here they're folded into it as ``detail`` (in file order) instead of
  showing as separate rows above the error they explain.
* **Levels from the parsed level**, not from words found in the message (a
  line mentioning "INFO" used to turn blue whatever its level).
* **Filters:** lowest level shown, source (the logger), and a search over the
  message, source and detail. Counts per level and per source are of the
  whole tail, so the filter chips say what's there before you pick one.
* **How much:** the last 200, 500 or 1,000 lines.
* **Plain text** of what's shown, for Download and Copy, with timestamps.

Everything here works on the already redacted entries (Kai reporters and
case content are removed by the reader), so nothing new is exposed.
"""
from collections import Counter
from datetime import date

LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
#: The "show" filter: lowest level included.
LEVEL_FILTERS = (
    ('all', 'All'),
    ('info', 'Info and up'),
    ('warning', 'Warnings and errors'),
    ('error', 'Errors only'),
)
LINE_CHOICES = (200, 500, 1000)
DEFAULT_LINES = 200


def level_key(level):
    """CSS/filter key for a log level: debug, info, warning, error, other."""
    level = (level or '').upper()
    if level in ('ERROR', 'CRITICAL'):
        return 'error'
    if level in ('WARNING', 'WARN'):
        return 'warning'
    if level in ('INFO', 'DEBUG'):
        return level.lower()
    return 'other'


def group_entries(raw):
    """Fold unparsed lines into the entry they follow. ``raw`` is the
    reader's list, newest first; the result is too."""
    entries, pending = [], []
    for item in raw:
        if not item.get('timestamp'):
            pending.append(item['message'])
            continue
        entries.append({
            'timestamp': item['timestamp'],
            'date': item['timestamp'][:10],
            'time': item['timestamp'][11:19],
            'level': (item['level'] or '').upper(),
            'level_key': level_key(item['level']),
            'logger': item['logger'],
            'text': item['text'] if item.get('text') is not None else item['message'],
            # Lines after this entry in the file came before it here.
            'detail': list(reversed(pending)),
        })
        pending = []
    if pending:
        # Lines at the top of the tail with no entry above them (the tail
        # started mid-traceback): kept, as one entry without a time.
        entries.append({'timestamp': '', 'date': '', 'time': '', 'level': '', 'level_key': 'other',
                        'logger': '', 'text': pending[-1], 'detail': list(reversed(pending[:-1]))})
    for e in entries:
        e['day_label'] = _day_label(e['date'])
    return entries


def _day_label(iso):
    """'2026-11-09' -> 'Monday, Nov 9, 2026' (the list's day headers)."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f'{d:%A, %b} {d.day}, {d.year}'


def parse_filters(params):
    """The page's query string, cleaned: level, source, q, lines, order."""
    level = params.get('level', 'all')
    if level not in dict(LEVEL_FILTERS):
        level = 'all'
    try:
        lines = int(params.get('lines', DEFAULT_LINES))
    except (TypeError, ValueError):
        lines = DEFAULT_LINES
    if lines not in LINE_CHOICES:
        lines = DEFAULT_LINES
    order = 'oldest' if params.get('order') == 'oldest' else 'newest'
    return {
        'level': level,
        'source': (params.get('source') or '').strip()[:200],
        'q': (params.get('q') or '').strip()[:200],
        'lines': lines,
        'order': order,
    }


def _passes_level(entry, level):
    if level == 'all':
        return True
    rank = LEVEL_RANK.get(entry['level'])
    if rank is None:
        # Unparsed (no level): only on "All".
        return False
    return rank >= LEVEL_RANK[level.upper()]


def apply_filters(entries, filters):
    q = filters['q'].lower()
    out = []
    for e in entries:
        if not _passes_level(e, filters['level']):
            continue
        if filters['source'] and e['logger'] != filters['source']:
            continue
        if q and q not in e['text'].lower() and q not in e['logger'].lower() \
                and not any(q in d.lower() for d in e['detail']):
            continue
        out.append(e)
    if filters['order'] == 'oldest':
        out.reverse()
    return out


def summarize(entries):
    """Counts over the whole tail: per level key, and per source (busiest first)."""
    levels = Counter(e['level_key'] for e in entries)
    sources = Counter(e['logger'] for e in entries if e['logger'])
    return {
        'errors': levels.get('error', 0),
        'warnings': levels.get('warning', 0),
        'info': levels.get('info', 0),
        'debug': levels.get('debug', 0),
        'sources': sources.most_common(),
        'newest': next((e['timestamp'] for e in entries if e['timestamp']), ''),
        'oldest': next((e['timestamp'] for e in reversed(entries) if e['timestamp']), ''),
    }


def as_text(entries):
    """Plain text of ``entries`` (as shown), one log line per line."""
    lines = []
    for e in entries:
        head = ' '.join(p for p in (e['timestamp'], f"[{e['level']}]" if e['level'] else '',
                                    f"{e['logger']}:" if e['logger'] else '') if p)
        lines.append(f'{head} {e["text"]}'.strip())
        lines.extend(e['detail'])
    return '\n'.join(lines) + ('\n' if lines else '')


def build_page(params, read_entries):
    """Everything the page needs. ``read_entries(max_lines)`` is the reader."""
    filters = parse_filters(params)
    raw, state, error = read_entries(filters['lines'])
    entries = group_entries(raw)
    shown = apply_filters(entries, filters)
    return {
        'filters': filters,
        'entries': shown,
        'total': len(entries),
        'summary': summarize(entries),
        'state': state,
        'error': error,
        'level_filters': LEVEL_FILTERS,
        'line_choices': LINE_CHOICES,
        'filtered': bool(filters['q'] or filters['source'] or filters['level'] != 'all'),
    }
