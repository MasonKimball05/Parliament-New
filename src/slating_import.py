"""
Import slating applications from another app's export (v4.0.0).

Mason (10-26-26): the President collected officer applications in his own
Google Form before anyone knew Parliament had slating, and members who already
answered shouldn't have to apply again. Google Forms → Responses → "Download
responses (.csv)" (or Sheets → File → Download → CSV) gives one row per
response; this turns those rows into SlatingApplications.

Three steps, all driven by ``src/view/slating/import_applications.py``:

1. ``parse_csv`` — the uploaded file to (headers, rows), with size limits.
2. ``guess_mapping`` — a first guess at what each column is, from its header.
   The manager confirms or changes it. Column kinds: who the applicant is
   (email, username or name), the response timestamp, GPA, first/second/
   third-choice and "do not want" positions, a form field from the period's
   Form Builder, or ignore.
3. ``plan`` — what would happen to every row (import, or skip and why, plus
   warnings), with no writes. ``commit`` runs ``plan`` again and writes the
   rows it marks ready, in one transaction. The preview the manager saw is
   never trusted: the import re-derives everything from the CSV and mapping.

Rules:

* **Never overwrite.** A member who already has an application for the period
  (any status, drafts included) is skipped: what they did in Parliament wins.
* **One application per member.** The President's form took one submission
  per position, so a member can have several rows. They're combined: every
  position kept, a newer response for the same position replacing an older
  one, differing text answers kept side by side labelled by position (see
  ``_merge_per_member``).
* **Imported = submitted.** Status ``submitted``, ``submitted_at`` from the
  form's timestamp when it parses, GPA level computed, GPA not verified (there
  is no screenshot). Required-field problems are warnings, not refusals: the
  committee reviews every application anyway.
* **Only members who could apply.** Inactive members, alumni and advisors are
  skipped (v4.0.0, same rule as the apply page).
* **Nothing lost.** With "keep unmapped answers" on, every answer that wasn't
  mapped to a form field goes into the application's review notes, under a
  heading that says it came from the import.
* File-upload questions can't be imported (the export only has Drive links);
  map those to "keep in notes" by leaving them unmapped.
"""
import csv
import io
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
MAX_COLUMNS = 150

IGNORE = ''
IDENTITY_KINDS = ('applicant_email', 'applicant_username', 'applicant_name')
POSITION_KINDS = {
    'pos_first': 'first_choice',
    'pos_second': 'second_choice',
    'pos_third': 'third_choice',
    'pos_none': 'do_not_want',
}
FIXED_KINDS = [
    ('applicant_email', 'Applicant: email'),
    ('applicant_username', 'Applicant: username'),
    ('applicant_name', 'Applicant: full name'),
    ('timestamp', 'Response timestamp'),
    ('gpa', 'GPA'),
    ('pos_first', 'Positions: first choice'),
    ('pos_second', 'Positions: second choice'),
    ('pos_third', 'Positions: third choice'),
    ('pos_none', 'Positions: do not want'),
]
#: Form field types that can hold an imported answer.
IMPORTABLE_FIELD_TYPES = {
    'text', 'textarea', 'number', 'decimal', 'email', 'phone', 'date', 'datetime',
    'select', 'radio', 'multiselect', 'checkbox', 'member_select',
}


class ImportError_(Exception):
    """The file can't be used at all (shown to the manager)."""


# ── 1. parse ────────────────────────────────────────────────────────────────

def decode(raw_bytes):
    if len(raw_bytes) > MAX_BYTES:
        raise ImportError_('That file is larger than 2 MB.')
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("Couldn't read that file as text. Export it as CSV and try again.")


def parse_csv(text):
    """(headers, rows) from CSV text. Rows are padded to the header width."""
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ImportError_('That file is larger than 2 MB.')
    try:
        reader = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ImportError_(f"That doesn't look like a CSV file ({exc}).")
    reader = [r for r in reader if any(cell.strip() for cell in r)]
    if len(reader) < 2:
        raise ImportError_('The file needs a header row and at least one response.')
    headers = [h.strip() for h in reader[0]]
    if len(headers) > MAX_COLUMNS:
        raise ImportError_(f'The file has more than {MAX_COLUMNS} columns.')
    rows = reader[1:]
    if len(rows) > MAX_ROWS:
        raise ImportError_(f'The file has more than {MAX_ROWS} responses.')
    width = len(headers)
    rows = [(r + [''] * width)[:width] for r in rows]
    return headers, rows


def combine_files(files):
    """
    (headers, rows, sources) from several CSVs, ``files`` = [(name, text)]
    (v4.3.0). The President's forms were one Google Form per position, so
    one upload per form: columns with the same question (ignoring case and
    punctuation) line up; a question only some files ask is blank in the
    others. ``sources[i]`` says where row i came from ("row 3", or
    "President.csv row 3" when there's more than one file).
    """
    if not files:
        raise ImportError_('Choose a CSV file to upload.')
    headers, keys, rows, sources = [], {}, [], []
    for name, text in files:
        try:
            file_headers, file_rows = parse_csv(text)
        except ImportError_ as exc:
            raise ImportError_(f'{name}: {exc}' if len(files) > 1 else str(exc))
        columns = []
        for i, header in enumerate(file_headers):
            key = _norm(header) or f'#{len(headers)}'
            if key not in keys or keys[key] in columns:   # a repeated header stays its own column
                keys[key] = len(headers)
                headers.append(header)
            columns.append(keys[key])
        for n, row in enumerate(file_rows, start=2):   # spreadsheet row numbers
            rows.append((columns, row))
            sources.append(f'{name} row {n}' if len(files) > 1 else f'row {n}')
    if len(headers) > MAX_COLUMNS:
        raise ImportError_(f'The files have more than {MAX_COLUMNS} different columns.')
    if len(rows) > MAX_ROWS:
        raise ImportError_(f'The files have more than {MAX_ROWS} responses.')
    out = []
    for columns, row in rows:
        full = [''] * len(headers)
        for i, value in zip(columns, row):
            full[i] = value
        out.append(full)
    return headers, out, sources


# ── 2. guess ────────────────────────────────────────────────────────────────

def _norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


def importable_fields(period):
    return list(period.form_fields.filter(is_active=True, field_type__in=IMPORTABLE_FIELD_TYPES)
                .order_by('display_order', 'id'))


def form_questions(period):
    """Every active question on the period's application form, in order."""
    return list(period.form_fields.filter(is_active=True).order_by('display_order', 'id'))


def not_asked(period, mapping):
    """
    The application-form questions no column answers. GPA and position-ranking
    questions count as asked when a GPA / position column is mapped; file
    uploads can never come from an export.
    """
    kinds = set(mapping.values())
    has_positions = any(k in POSITION_KINDS for k in kinds)
    missing = []
    for f in form_questions(period):
        if f.field_type == 'gpa':
            asked = 'gpa' in kinds
        elif f.field_type == 'position_preference':
            asked = has_positions
        else:
            asked = f'field_{f.id}' in kinds
        if not asked:
            missing.append(f)
    return missing


def guess_mapping(headers, fields):
    """{column index: kind} — a starting point the manager edits."""
    by_label = {}
    for f in fields:
        by_label.setdefault(_norm(f.label), f.id)
        by_label.setdefault(_norm(f.field_name), f.id)
    mapping, used = {}, set()

    def take(i, kind):
        if kind in used and not kind.startswith('field_'):
            return
        mapping[i] = kind
        used.add(kind)

    for i, header in enumerate(headers):
        h = _norm(header)
        if not h:
            continue
        if h in by_label:
            take(i, f'field_{by_label[h]}')
        elif 'email' in h:
            take(i, 'applicant_email')
        elif h in ('timestamp', 'submitted at', 'submission time', 'date submitted'):
            take(i, 'timestamp')
        elif 'gpa' in h or 'grade point' in h:
            take(i, 'gpa')
        elif 'position' in h or 'office' in h or 'role' in h:
            if 'not' in h.split() or 'do not' in h:
                take(i, 'pos_none')
            elif re.search(r'\b(second|2nd)\b', h):
                take(i, 'pos_second')
            elif re.search(r'\b(third|3rd)\b', h):
                take(i, 'pos_third')
            else:
                take(i, 'pos_first')
        elif h == 'username':
            take(i, 'applicant_username')
        elif h in ('name', 'full name', 'your name', 'first and last name', 'name first and last'):
            take(i, 'applicant_name')
    return mapping


def mapping_from_post(post, column_count):
    """{column index: kind} from the mapping form, ignoring junk."""
    mapping = {}
    for i in range(column_count):
        kind = (post.get(f'col_{i}') or '').strip()
        if kind:
            mapping[i] = kind
    return mapping


def validate_mapping(mapping, fields):
    """A list of problems with the mapping itself (empty = usable)."""
    problems = []
    field_ids = {f.id for f in fields}
    kinds = list(mapping.values())
    identity = [k for k in kinds if k in IDENTITY_KINDS]
    if not identity:
        problems.append('Choose the column that says who each applicant is (email, username or name).')
    if len(identity) > 1:
        problems.append('Choose only one applicant column.')
    for k in set(kinds):
        if k.startswith('field_'):
            if _int(k[6:]) not in field_ids:
                problems.append('One column is mapped to a form field that no longer exists.')
            elif kinds.count(k) > 1:
                problems.append('Two columns are mapped to the same form field.')
        elif k not in dict(FIXED_KINDS):
            problems.append(f'Unknown column type "{k}".')
        elif k != 'pos_first' and k in POSITION_KINDS and kinds.count(k) > 1:
            problems.append('Two columns are mapped to the same position choice.')
        elif k in ('timestamp', 'gpa') and kinds.count(k) > 1:
            problems.append(f'Only one column can be the {dict(FIXED_KINDS)[k].lower()}.')
    return sorted(set(problems))


# ── 3. plan / commit ────────────────────────────────────────────────────────

def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_TS_FORMATS = ('%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S',
               '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%m/%d/%Y', '%Y-%m-%d',
               # v4.3.0: Google Forms' own export ("2026/10/06 9:31:59 AM EST")
               # and 12-hour times generally.
               '%Y/%m/%d %I:%M:%S %p', '%Y/%m/%d %I:%M %p', '%Y/%m/%d %H:%M:%S', '%Y/%m/%d %H:%M',
               '%Y/%m/%d', '%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y %I:%M %p')

#: Hours from UTC for the zone abbreviations a form export may end with.
_TZ_HOURS = {'UTC': 0, 'GMT': 0, 'EST': -5, 'EDT': -4, 'CST': -6, 'CDT': -5, 'MST': -7,
             'MDT': -6, 'PST': -8, 'PDT': -7, 'AKST': -9, 'AKDT': -8, 'HST': -10}
_TZ_SUFFIX = re.compile(r'\s+(?:(?P<abbr>[A-Za-z]{2,4})|(?:GMT|UTC)(?P<sign>[+-])(?P<h>\d{1,2})(?::?(?P<m>\d{2}))?)$')


def parse_timestamp(text):
    """
    A response time, or None. A trailing zone ("EST", "GMT-5") is honoured;
    without one the time is taken as the site's local time.
    """
    text = (text or '').strip()
    tz = None
    m = _TZ_SUFFIX.search(text)
    if m and m.group('abbr') and m.group('abbr').upper() not in ('AM', 'PM'):
        hours = _TZ_HOURS.get(m.group('abbr').upper())
        if hours is None:
            return None
        tz, text = dt_timezone(timedelta(hours=hours)), text[:m.start()]
    elif m and m.group('sign'):
        offset = timedelta(hours=int(m.group('h')), minutes=int(m.group('m') or 0))
        tz, text = dt_timezone(offset if m.group('sign') == '+' else -offset), text[:m.start()]
    for fmt in _TS_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=tz) if tz else timezone.make_aware(parsed)
    return None


def match_positions(text, positions):
    """
    Position ids named in ``text`` (in the order they appear), plus anything
    left over. Google Forms joins checkbox answers with ", ", and titles can
    contain commas or other titles ("Vice President" / "President"), so titles
    are found longest-first and removed as they're matched, not split on commas.
    """
    remaining = f' {text or ""} '
    found = []
    candidates = []
    for p in positions:
        for name in {p.title, p.code}:
            if name and name.strip():
                candidates.append((name.strip(), p.id))
    for name, pid in sorted(candidates, key=lambda c: -len(c[0])):
        rx = re.compile(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])', re.IGNORECASE)
        m = rx.search(remaining)
        if m:
            if pid not in [f[1] for f in found]:
                found.append((m.start(), pid))
            remaining = rx.sub(' ', remaining)
    leftover = re.sub(r'[\s,;/&]+|\band\b', ' ', remaining, flags=re.IGNORECASE).strip()
    return [pid for _, pid in sorted(found)], leftover


def _name_keys(name):
    """(full, short) keys for a name: all of it, and first + last ignoring
    middle names and initials ("Sawyer W. Guffey" → "sawyer guffey")."""
    tokens = _norm(name).split()
    long = [t for t in tokens if len(t) > 1]
    return ' '.join(tokens), (f'{long[0]} {long[-1]}' if len(long) >= 2 else ' '.join(long))


class _NameMatcher:
    """
    Members by name, more forgiving than an exact match (v4.3.0): the full
    name or preferred name + last name exactly, then first + last name
    without middle names or initials. A match by first + last only is
    flagged on the preview for the manager to check.
    """

    def __init__(self):
        from src.models import ParliamentUser
        self.full, self.short, self.last = {}, {}, {}
        for user in ParliamentUser.objects.exclude(member_status='Removed'):
            names = [user.name]
            if user.preferred_name and len(user.name.split()) > 1:
                names.append(f'{user.preferred_name} {user.name.split()[-1]}')
            for name in names:
                full, short = _name_keys(name)
                for index, key in ((self.full, full), (self.short, short)):
                    if key and user not in index.setdefault(key, []):
                        index[key].append(user)
            last = _norm(user.name).split()[-1:] or ['']
            self.last.setdefault(last[0], []).append(user)

    def find(self, value):
        """(members, how): how is 'exact' or 'short'."""
        full, short = _name_keys(value)
        if full in self.full:
            return self.full[full], 'exact'
        return self.short.get(short, []), 'short'

    def suggest(self, value):
        """Members who share the last name (or the first and last), for the picker."""
        tokens = _norm(value).split()
        found = list(self.last.get(tokens[-1], [])) if tokens else []
        if tokens:
            found += [u for k, users in self.short.items() if k.split()[0] == tokens[0]
                      and k.split()[-1][:1] == tokens[-1][:1] for u in users if u not in found]
        return sorted(found, key=lambda u: u.name)[:6]


def _members_for(kind, values):
    """{normalised value: [members]} for every identity value, in one query."""
    from src.models import ParliamentUser
    values = {v.strip().lower() for v in values if v and v.strip()}
    if not values:
        return {}
    field = {'applicant_email': 'email', 'applicant_username': 'username',
             'applicant_name': 'name'}[kind]
    from django.db.models.functions import Lower
    out = {}
    for user in (ParliamentUser.objects.annotate(_key=Lower(field))
                 .filter(_key__in=values).exclude(member_status='Removed')):
        out.setdefault(getattr(user, field).strip().lower(), []).append(user)
    return out


def _convert(field, text):
    """(value, warning) for an answer to ``field``; value None = leave blank."""
    text = (text or '').strip()
    if not text:
        return None, None
    t = field.field_type
    if t == 'number':
        try:
            return int(Decimal(text.replace(',', ''))), None
        except (InvalidOperation, ValueError):
            return None, f'{field.label}: "{text}" isn\'t a number (left blank)'
    if t == 'decimal':
        try:
            return Decimal(text.replace(',', '')), None
        except InvalidOperation:
            return None, f'{field.label}: "{text}" isn\'t a number (left blank)'
    if t in ('multiselect', 'checkbox'):
        options = [str(o.get('value', o.get('label')) if isinstance(o, dict) else o)
                   for o in (field.options or [])]
        if options:
            picked, rest = [], f', {text}, '
            for opt in sorted(options, key=len, reverse=True):
                token = f', {opt}, '
                if token.lower() in rest.lower():
                    picked.append(opt)
                    idx = rest.lower().index(token.lower())
                    rest = rest[:idx] + ', ' + rest[idx + len(token):]
            extra = [s.strip() for s in rest.split(',') if s.strip()]
            return picked + extra, None
        return [s.strip() for s in text.split(',') if s.strip()], None
    return text, None


def plan(period, headers, rows, mapping, keep_unmapped=True, fixes=None, sources=None):
    """
    What importing would do, row by row. No writes. Returns a list of dicts:
    {row, where, applicant, status ('import'|'skip'), reason, warnings,
     positions, gpa, submitted_at, answers {field: value}, notes, fixed, fix,
     who, suggestions}.

    ``fixes`` is the preview's POST: positions, submitted time and GPA the
    manager set by hand (see ``_apply_fixes``), and who a row belongs to
    (``who_<row>``: a member's id, "skip", or blank to match it from the
    CSV). ``sources`` labels each row (see ``combine_files``).
    """
    from src.models import ParliamentUser
    get = (fixes or {}).get
    fields = {f.id: f for f in importable_fields(period)}
    positions = list(period.positions.filter(is_active=True).order_by('display_order', 'id'))
    titles = {p.id: p.title for p in positions}
    id_col, id_kind = next(((i, k) for i, k in mapping.items() if k in IDENTITY_KINDS), (None, None))
    ts_col = next((i for i, k in mapping.items() if k == 'timestamp'), None)
    gpa_col = next((i for i, k in mapping.items() if k == 'gpa'), None)
    if id_kind == 'applicant_name':
        names = _NameMatcher()
    else:
        members = _members_for(id_kind, [r[id_col] for r in rows]) if id_col is not None else {}
    existing = dict(period.applications.values_list('applicant_id', 'imported_at'))
    chosen = {n: get(f'who_{n}', '').strip() for n in range(2, len(rows) + 2)}
    by_hand = {u.pk: u for u in ParliamentUser.objects.filter(
        pk__in=[v for v in chosen.values() if v and v != 'skip']).exclude(member_status='Removed')}
    required = [f for f in fields.values() if f.is_required]
    questions = form_questions(period)
    missing_ids = {f.id for f in not_asked(period, mapping)}

    out = []
    for n, row in enumerate(rows, start=2):   # spreadsheet row numbers
        item = {'row': n, 'applicant': None, 'status': 'skip', 'reason': '', 'warnings': [],
                'positions': {}, 'gpa': None, 'submitted_at': None, 'answers': {}, 'notes': [],
                'identity': (row[id_col].strip() if id_col is not None else ''),
                'where': (sources[n - 2] if sources else f'row {n}'),
                'who': chosen[n], 'suggestions': [],
                # The response as submitted, for "View the response" on the preview.
                'raw': [(headers[i] or f'Column {i + 1}', row[i].strip())
                        for i in range(len(headers)) if row[i].strip()]}
        out.append(item)
        # Who this row is: chosen on the preview, else matched from the CSV.
        if item['who'] == 'skip':
            item['reason'] = 'Left out by hand.'
            continue
        if item['who'] in by_hand:
            found = [by_hand[item['who']]]
        elif not item['identity']:
            item['reason'] = 'No applicant in this row. Choose who it belongs to.'
            continue
        elif id_kind == 'applicant_name':
            found, how = names.find(item['identity'])
            if len(found) == 1 and how == 'short':
                item['warnings'].append(f'Matched "{item["identity"]}" to {found[0].name} by first and '
                                        f'last name. Check it\'s the right person.')
            if len(found) != 1:
                item['suggestions'] = found or names.suggest(item['identity'])
        else:
            found = members.get(item['identity'].lower(), [])
            if len(found) > 1:
                item['suggestions'] = found
        if not found:
            item['reason'] = f'No Parliament member matches "{item["identity"]}". Choose who it belongs to.'
            continue
        if len(found) > 1:
            item['reason'] = f'More than one member matches "{item["identity"]}". Choose which one.'
            continue
        user = item['applicant'] = found[0]
        if user.pk in existing:
            imported = existing[user.pk]
            item['reason'] = (
                f'Already imported on {timezone.localtime(imported).strftime("%m/%d/%Y")}. To bring '
                f'it in again, remove that application under Earlier imports first.' if imported
                else 'Already applied in Parliament (their application is kept as is).')
            continue
        # v4.0.0: the same rule as the apply page (only active members; no
        # inactive members, alumni or advisors). Pledges may apply.
        from src.view.slating.permissions import applicant_ineligibility
        reason = applicant_ineligibility(user)
        if reason:
            item['reason'] = reason
            continue
        if user.is_pledge:
            item['warnings'].append('Applicant is a pledge (can apply; must be initiated before an interview).')

        if ts_col is not None:
            item['submitted_at'] = parse_timestamp(row[ts_col])
            if row[ts_col].strip() and item['submitted_at'] is None:
                item['warnings'].append(f'Couldn\'t read the timestamp "{row[ts_col]}"; using now.')
        if gpa_col is not None and row[gpa_col].strip():
            try:
                gpa = Decimal(row[gpa_col].strip())
                if not Decimal('0') <= gpa <= Decimal('5'):
                    raise InvalidOperation
                item['gpa'] = gpa.quantize(Decimal('0.001'))
            except InvalidOperation:
                item['warnings'].append(f'GPA "{row[gpa_col]}" isn\'t a number from 0 to 5 (left blank).')

        seen = set()
        for kind, tier in POSITION_KINDS.items():
            for i in sorted(c for c, k in mapping.items() if k == kind):
                ids, leftover = match_positions(row[i], positions)
                ids = [p for p in ids if p not in seen]
                seen.update(ids)
                item['positions'].setdefault(tier, []).extend(ids)
                if leftover:
                    item['warnings'].append(f'Unrecognised position text: "{leftover}".')
        for i, kind in sorted(mapping.items()):
            if kind.startswith('field_'):
                field = fields.get(_int(kind[6:]))
                if field is None:
                    continue
                value, warning = _convert(field, row[i])
                if warning:
                    item['warnings'].append(warning)
                if value not in (None, '', []):
                    item['answers'][field] = value
        if keep_unmapped:
            item['notes'] = [(headers[i], row[i].strip()) for i in range(len(headers))
                             if i not in mapping and row[i].strip()]
        item['status'] = 'import'

    _merge_per_member(out, titles)
    for item in out:
        if item['status'] == 'import':
            _apply_fixes(item, fixes, positions)
            _finish(item, titles, required, questions, missing_ids)
    return out


def _local_input(dt):
    """A datetime as a ``datetime-local`` input's value."""
    return timezone.localtime(dt).strftime('%Y-%m-%dT%H:%M') if dt else ''


def _apply_fixes(item, fixes, positions):
    """
    Corrections typed on the preview (v4.3.0), for what the CSV got wrong:
    positions, submitted time and GPA. Each control posts its value
    (``fix_<member>_…``) beside the value the CSV gave (``was_<member>_…``);
    only a value that differs is a correction. So remapping a column and
    previewing again still re-reads the CSV, and the import, which re-plans
    from the POST, applies the same corrections the preview showed.

    Also leaves ``item['fix']``: what the page's controls start from.
    """
    pk = item['applicant'].pk
    tier_of = {pid: t for t in TIERS for pid in item['positions'].get(t, [])}
    parsed = {'submitted': _local_input(item['submitted_at']),
              'gpa': '' if item['gpa'] is None else str(item['gpa'].normalize()),
              'positions': {p.id: tier_of.get(p.id, '') for p in positions}}
    item['fixed'] = []
    get = (fixes or {}).get

    def changed(key):
        was = get(f'was_{pk}_{key}')
        return was is not None and get(f'fix_{pk}_{key}', '').strip() != was

    if any(changed(f'pos_{p.id}') for p in positions):
        chosen = {t: [] for t in TIERS}
        for p in positions:
            tier = get(f'fix_{pk}_pos_{p.id}', '')
            if tier in chosen:
                chosen[tier].append(p.id)
        item['positions'] = {t: ids for t, ids in chosen.items() if ids}
        item['warnings'] = [w for w in item['warnings'] if not w.startswith('Unrecognised position text')]
        item['fixed'].append('positions')
    if changed('submitted'):
        item['submitted_at'] = parse_timestamp(get(f'fix_{pk}_submitted'))
        item['warnings'] = [w for w in item['warnings'] if not w.startswith("Couldn't read the timestamp")]
        item['fixed'].append('submitted time')
    if changed('gpa'):
        raw = get(f'fix_{pk}_gpa', '').strip()
        item['warnings'] = [w for w in item['warnings']
                            if not w.startswith(('GPA "', 'Different GPAs'))]
        try:
            gpa = Decimal(raw) if raw else None
            if gpa is not None and not Decimal('0') <= gpa <= Decimal('5'):
                raise InvalidOperation
            item['gpa'] = None if gpa is None else gpa.quantize(Decimal('0.001'))
        except InvalidOperation:
            item['gpa'] = None
            item['warnings'].append(f'GPA "{raw}" isn\'t a number from 0 to 5 (left blank).')
        item['fixed'].append('GPA')

    tier_of = {pid: t for t in TIERS for pid in item['positions'].get(t, [])}
    item['fix'] = {
        'submitted': _local_input(item['submitted_at']), 'was_submitted': parsed['submitted'],
        'gpa': '' if item['gpa'] is None else str(item['gpa'].normalize()), 'was_gpa': parsed['gpa'],
        'positions': [{'id': p.id, 'title': p.title, 'tier': tier_of.get(p.id, ''),
                       'was': parsed['positions'][p.id]} for p in positions],
    }


TIERS = ('first_choice', 'second_choice', 'third_choice', 'do_not_want')
#: Field types whose differing answers are kept side by side (labelled by
#: position) when one member sent several forms; other types keep the latest.
TEXT_TYPES = {'text', 'textarea'}


def _order(item):
    ts = item['submitted_at']
    return (ts.timestamp() if ts else 0, item['row'])


def _position_ids(item):
    return frozenset(p for t in TIERS for p in item['positions'].get(t, []))


def _label(item, titles):
    names = [titles[p] for t in TIERS[:3] for p in item['positions'].get(t, []) if p in titles]
    return ', '.join(names) or item['where'].capitalize()


def _merge_per_member(items, titles):
    """
    One application per member (v4.0.0). The President's Google Form took
    one submission PER POSITION, so a member running for three positions has
    three rows. For each member:

    * a newer response for the same position(s) replaces an older one (a
      correction), and
    * responses for different positions are combined: every position is
      kept; text answers that differ are kept side by side, labelled with
      their position; other answers keep the latest (with a warning); unmapped
      answers go to the notes tagged with their position.

    The combined application takes the earliest timestamp (when they first
    applied). Rows folded in are marked skipped with the row they went into.
    """
    groups = {}
    for item in items:
        if item['status'] == 'import':
            groups.setdefault(item['applicant'].pk, []).append(item)
    for group in groups.values():
        group.sort(key=_order)
        # Same position(s) again → the newer response replaces the older.
        latest = {}
        for item in group:
            key = _position_ids(item)
            if key in latest:
                old = latest[key]
                old['status'] = 'skip'
                old['reason'] = (f'Replaced by a newer response for the same position '
                                 f'({item["where"]}). Choose "Don\'t import" on that one to keep this.')
            latest[key] = item
        keep = [i for i in group if i['status'] == 'import']
        if len(keep) < 2:
            continue
        primary = keep[-1]
        _combine(primary, keep, titles)
        for item in keep[:-1]:
            item['status'] = 'skip'
            item['combined'] = True   # its "whose response" picker is on the combined one
            item['reason'] = (f'Combined into one application with {primary["where"]} '
                              f'(one application per member).')


def _combine(primary, parts, titles):
    """Fold ``parts`` (oldest first, primary last) into ``primary``."""
    # Each form's label, taken BEFORE primary's positions become the merged set
    # (primary is one of the parts).
    labels = {part['row']: _label(part, titles) for part in parts}
    # Positions: every one, each in the highest tier any response put it in.
    placed, positions = set(), {}
    for tier in TIERS:
        for part in parts:
            for pid in part['positions'].get(tier, []):
                if pid not in placed:
                    placed.add(pid)
                    positions.setdefault(tier, []).append(pid)
    primary['positions'] = positions

    # Answers, field by field.
    answers, warnings = {}, []
    fields = []
    for part in parts:
        for f in part['answers']:
            if f not in fields:
                fields.append(f)
    for f in fields:
        given = [(part, part['answers'][f]) for part in parts if f in part['answers']]
        distinct = []
        for _, v in given:
            if v not in distinct:
                distinct.append(v)
        if len(distinct) == 1:
            answers[f] = distinct[0]
        elif f.field_type in TEXT_TYPES:
            answers[f] = '\n\n'.join(f'{labels[part["row"]]}: {v}' for part, v in given)
        elif f.field_type in ('multiselect', 'checkbox'):
            merged = []
            for _, v in given:
                merged += [x for x in v if x not in merged]
            answers[f] = merged
        else:
            answers[f] = given[-1][1]
            warnings.append(f'Different answers to "{f.label}" across forms; kept the latest '
                            f'({given[-1][0]["where"]}).')
    primary['answers'] = answers

    gpas = [part['gpa'] for part in parts if part['gpa'] is not None]
    if gpas:
        primary['gpa'] = gpas[-1]
        if len(set(gpas)) > 1:
            warnings.append(f'Different GPAs across forms ({", ".join(str(g) for g in gpas)}); '
                            f'kept the latest.')
    stamps = [part['submitted_at'] for part in parts if part['submitted_at']]
    primary['submitted_at'] = min(stamps) if stamps else None

    notes = []
    for part in parts:
        tag = labels[part['row']]
        notes += [(f'[{tag}] {q}', a) for q, a in part['notes']]
    primary['notes'] = notes

    seen = set()
    primary['warnings'] = [w for w in [w for part in parts for w in part['warnings']] + warnings
                           if not (w in seen or seen.add(w))]
    primary['merged_rows'] = [part['row'] for part in parts]
    primary['merged_where'] = [part['where'] for part in parts]
    primary['parts'] = [{k: part[k] for k in ('row', 'where', 'who', 'identity', 'raw')} for part in parts]


def _finish(item, titles, required, questions, missing_ids):
    """Warnings and the confirmation-screen view, on the final application."""
    item.setdefault('merged_rows', [item['row']])
    item.setdefault('merged_where', [item['where']])
    item.setdefault('parts', [{k: item[k] for k in ('row', 'where', 'who', 'identity', 'raw')}])
    if not any(item['positions'].get(t) for t in TIERS[:3]):
        item['warnings'].append('No positions matched.')
    missing = [f.label for f in required if f not in item['answers'] and f.id not in missing_ids]
    if missing:
        item['warnings'].append('Missing required: ' + ', '.join(missing) + '.')
    item['position_titles'] = {t: [titles[p] for p in ids] for t, ids in item['positions'].items()}
    # Everything the application will contain, question by question, for the
    # confirmation screen: the imported answer, "blank" (asked, no answer), or
    # "not asked" (the outside form didn't have it).
    review = []
    for q in questions:
        if q.id in missing_ids:
            review.append({'label': q.label, 'state': 'not_asked', 'value': None})
        elif q.field_type == 'gpa':
            review.append({'label': q.label, 'state': 'answered' if item['gpa'] is not None else 'blank',
                           'value': item['gpa']})
        elif q.field_type == 'position_preference':
            review.append({'label': q.label, 'state': 'positions', 'value': None})
        else:
            value = item['answers'].get(q)
            if isinstance(value, list):
                value = ', '.join(str(v) for v in value)
            review.append({'label': q.label, 'state': 'answered' if value not in (None, '') else 'blank',
                           'value': value})
    item['review'] = review


def commit(period, headers, rows, mapping, keep_unmapped, imported_by, request=None, fixes=None,
           sources=None):
    """Write the rows ``plan`` marks for import. Returns (imported, skipped)."""
    from src.models import (
        ActivityLog, SlatingActivity, SlatingApplication, SlatingApplicationResponse,
    )
    items = plan(period, headers, rows, mapping, keep_unmapped, fixes, sources)
    ready = [i for i in items if i['status'] == 'import']
    not_asked_labels = [f.label for f in not_asked(period, mapping)]
    now = timezone.now()
    stamp = timezone.localtime(now).strftime('%m/%d/%Y')
    with transaction.atomic():
        # A member may have applied in Parliament since the preview.
        taken = set(period.applications.select_for_update().values_list('applicant_id', flat=True))
        done = []
        for item in ready:
            user = item['applicant']
            if user.pk in taken:
                item['status'], item['reason'] = 'skip', 'Already has an application (kept as is).'
                continue
            used = item['merged_where']
            where = used[0] if len(used) == 1 else ', '.join(used) + ', combined'
            if item['who'] and item['who'] != 'skip':
                where += f'; "{item["identity"]}" tied to this member by hand'

            notes = [f'Imported from an outside form by {imported_by.get_display_name()} on {stamp} '
                     f'({where}).']
            if item['fixed']:
                notes.append(f'Set by hand during the import: {", ".join(item["fixed"])}.')
            if item['notes']:
                notes.append('Answers not matched to a form field:')
                notes += [f'• {q}: {a}' for q, a in item['notes']]
            app = SlatingApplication.objects.create(
                period=period, applicant=user, status='submitted',
                submitted_at=item['submitted_at'] or now,
                position_preferences={t: item['positions'].get(t, []) for t in
                                      ('first_choice', 'second_choice', 'third_choice', 'do_not_want')},
                reported_gpa=item['gpa'],
                review_notes='\n'.join(notes),
                imported_at=now,
                import_not_asked=not_asked_labels,
            )
            if app.reported_gpa is not None:
                app.gpa_level = app.calculate_gpa_level()
                app.save(update_fields=['gpa_level'])
            for field, value in item['answers'].items():
                response = SlatingApplicationResponse(application=app, field=field)
                if field.field_type in ('multiselect', 'checkbox'):
                    response.json_value = value
                elif field.field_type in ('number', 'decimal'):
                    response.number_value = value
                else:
                    response.text_value = value
                response.save()
            SlatingActivity.objects.create(
                period=period, user=imported_by, action='application_submitted',
                details=f'{user.name}\'s application imported from an outside form',
                metadata={'application_id': app.id, 'imported': True, 'row': item['row']},
            )
            taken.add(user.pk)
            done.append(item)
        if done:
            ActivityLog.log_activity(
                action_type='other',
                user=imported_by,
                description=(f'{imported_by.name} imported {len(done)} slating application(s) '
                             f'into "{period.name}" from an outside form'),
                request=request,
                object_type='SlatingPeriod',
                object_id=period.id,
                object_repr=period.name,
                metadata={'imported': len(done), 'rows': len(rows)},
            )
    return done, [i for i in items if i['status'] != 'import']
