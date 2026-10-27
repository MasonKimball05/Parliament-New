"""
Import slating applications from another app's export (v3.57.0).

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
* **One application per member.** If a member answered the form more than once,
  the latest response (by timestamp, else the later row) is used.
* **Imported = submitted.** Status ``submitted``, ``submitted_at`` from the
  form's timestamp when it parses, GPA level computed, GPA not verified (there
  is no screenshot). Required-field problems are warnings, not refusals: the
  committee reviews every application anyway.
* **Only members who could apply.** Inactive members, alumni and advisors are
  skipped (v3.58.0, same rule as the apply page).
* **Nothing lost.** With "keep unmapped answers" on, every answer that wasn't
  mapped to a form field goes into the application's review notes, under a
  heading that says it came from the import.
* File-upload questions can't be imported (the export only has Drive links);
  map those to "keep in notes" by leaving them unmapped.
"""
import csv
import io
import re
from datetime import datetime
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
               '%Y-%m-%d %H:%M', '%m/%d/%Y', '%Y-%m-%d')


def parse_timestamp(text):
    text = (text or '').strip()
    for fmt in _TS_FORMATS:
        try:
            return timezone.make_aware(datetime.strptime(text, fmt))
        except ValueError:
            continue
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


def plan(period, headers, rows, mapping, keep_unmapped=True):
    """
    What importing would do, row by row. No writes. Returns a list of dicts:
    {row, applicant, status ('import'|'skip'), reason, warnings, positions,
     gpa, submitted_at, answers {field: value}, notes}.
    """
    fields = {f.id: f for f in importable_fields(period)}
    positions = list(period.positions.filter(is_active=True))
    titles = {p.id: p.title for p in positions}
    id_col, id_kind = next(((i, k) for i, k in mapping.items() if k in IDENTITY_KINDS), (None, None))
    ts_col = next((i for i, k in mapping.items() if k == 'timestamp'), None)
    gpa_col = next((i for i, k in mapping.items() if k == 'gpa'), None)
    members = _members_for(id_kind, [r[id_col] for r in rows]) if id_col is not None else {}
    existing = set(period.applications.values_list('applicant_id', flat=True))
    required = [f for f in fields.values() if f.is_required]
    questions = form_questions(period)
    missing_ids = {f.id for f in not_asked(period, mapping)}

    out = []
    for n, row in enumerate(rows, start=2):   # spreadsheet row numbers
        item = {'row': n, 'applicant': None, 'status': 'skip', 'reason': '', 'warnings': [],
                'positions': {}, 'gpa': None, 'submitted_at': None, 'answers': {}, 'notes': [],
                'identity': (row[id_col].strip() if id_col is not None else '')}
        out.append(item)
        if not item['identity']:
            item['reason'] = 'No applicant in this row.'
            continue
        found = members.get(item['identity'].lower(), [])
        if not found:
            item['reason'] = f'No Parliament member matches "{item["identity"]}".'
            continue
        if len(found) > 1:
            item['reason'] = f'More than one member matches "{item["identity"]}"; use email instead.'
            continue
        user = item['applicant'] = found[0]
        if user.pk in existing:
            item['reason'] = 'Already has an application in Parliament (kept as is).'
            continue
        # v3.58.0: the same rule as the apply page (only active members; no
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
        if not any(item['positions'].get(t) for t in ('first_choice', 'second_choice', 'third_choice')):
            item['warnings'].append('No positions matched.')

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
        missing = [f.label for f in required if f not in item['answers'] and f.id not in missing_ids]
        if missing:
            item['warnings'].append('Missing required: ' + ', '.join(missing) + '.')

        if keep_unmapped:
            item['notes'] = [(headers[i], row[i].strip()) for i in range(len(headers))
                             if i not in mapping and row[i].strip()]
        item['position_titles'] = {t: [titles[p] for p in ids] for t, ids in item['positions'].items()}
        # Everything the application will contain, question by question, for
        # the confirmation screen: the imported answer, "blank" (asked, no
        # answer), or "not asked" (the outside form didn't have it).
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
        item['status'] = 'import'

    # One application per member: the latest response wins.
    by_user = {}
    for item in out:
        if item['status'] != 'import':
            continue
        key = item['applicant'].pk
        prev = by_user.get(key)
        if prev is None:
            by_user[key] = item
            continue
        newer = item
        if prev['submitted_at'] and item['submitted_at'] and prev['submitted_at'] > item['submitted_at']:
            newer = prev
        older = item if newer is prev else prev
        older['status'] = 'skip'
        older['reason'] = f'An answer from the same member in row {newer["row"]} is newer.'
        by_user[key] = newer
    return out


def commit(period, headers, rows, mapping, keep_unmapped, imported_by, request=None):
    """Write the rows ``plan`` marks for import. Returns (imported, skipped)."""
    from src.models import (
        ActivityLog, SlatingActivity, SlatingApplication, SlatingApplicationResponse,
    )
    items = plan(period, headers, rows, mapping, keep_unmapped)
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
                item['status'], item['reason'] = 'skip', 'Already has an application in Parliament (kept as is).'
                continue
            notes = [f'Imported from an outside form by {imported_by.get_display_name()} on {stamp} '
                     f'(spreadsheet row {item["row"]}).']
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
