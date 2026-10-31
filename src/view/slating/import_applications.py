"""
Import applications from another app's CSV export (v4.0.0).

GET  → upload form, and the earlier imports (each removable).
POST step=upload  → parse the file(s), show the column mapping (guessed).
POST step=add / remove_<n> → add CSVs to the upload, or take one out, and
                    show the mapping again (choices kept by column name).
POST step=preview → show what would happen to every row. No writes.
POST step=import  → re-plan from the CSV + mapping and write (never trusts
                    the preview). Then back to the applications list.

The CSV text rides along in hidden fields between steps rather than in the
session: it's the manager's own upload, and keeping confidential applicant
data out of the session table means nothing is left behind if they walk away.
Several files (one Google Form per position) are lined up by column name
(``si.combine_files``).

Access: ``slating_chair_required`` (slating manager, the committee's admin or
chairs; site admins only while the period isn't locked) — the same people who
manage the application form. Logic: ``src/slating_import.py``.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from src import slating_import as si
from src.models import ActivityLog, ParliamentUser, SlatingActivity, SlatingApplication, SlatingPeriod
from .permissions import slating_chair_required

#: An imported application can be removed while nothing has been built on it.
REMOVABLE_STATUSES = ('submitted', 'under_review')


def _earlier_imports(period):
    """Imported applications grouped by import (one ``imported_at`` per import)."""
    apps = (period.applications.filter(imported_at__isnull=False)
            .select_related('applicant')
            .annotate(n_interviews=Count('interviews', distinct=True),
                      n_slated=Count('slate_assignments', distinct=True))
            .order_by('-imported_at', 'applicant__name'))
    batches = {}
    for app in apps:
        app.removable = (app.status in REMOVABLE_STATUSES and not app.n_interviews
                         and not app.n_slated)
        batches.setdefault(app.imported_at, []).append(app)
    return [{'at': at, 'key': at.isoformat(), 'apps': group,
             'removable': sum(1 for a in group if a.removable)} for at, group in batches.items()]


def _context(period, **extra):
    fields = si.importable_fields(period)
    return {
        'period': period,
        'fields': fields,
        # Every choice for a column's dropdown: (value, label).
        'kind_options': ([('', '— Ignore —')] + list(si.FIXED_KINDS)
                         + [(f'field_{f.id}', f'Form field: {f.label}') for f in fields]),
        'positions': list(period.positions.filter(is_active=True).order_by('display_order')),
        'max_bytes': si.MAX_BYTES,
        'earlier_imports': _earlier_imports(period),
        **extra,
    }


def _posted_files(post):
    """[(name, text)] from the page's hidden fields, empty ones dropped."""
    names = post.getlist('csv_name')
    files = []
    for i, text in enumerate(post.getlist('csv_text')):
        name = (names[i] if i < len(names) else '').strip() or f'File {i + 1}'
        if text.strip():
            files.append((name, text.lstrip('\ufeff')))
    return files


def _kept_mapping(post, headers, fields):
    """
    After adding or removing a file the columns move, so the choices made so
    far are carried by column name (``colhead_<n>`` beside ``col_<n>``);
    columns new to the upload get a guess.
    """
    before = {}
    i = 0
    while f'colhead_{i}' in post:
        before[si._norm(post[f'colhead_{i}'])] = (post.get(f'col_{i}') or '').strip()
        i += 1
    mapping = si.guess_mapping(headers, fields)
    for n, header in enumerate(headers):
        key = si._norm(header)
        if key in before:
            if before[key]:
                mapping[n] = before[key]
            else:
                mapping.pop(n, None)
    return mapping


def _preview(ctx, period, headers, rows, mapping, keep_unmapped, post, sources):
    items = si.plan(period, headers, rows, mapping, keep_unmapped, post, sources)
    ctx.update(step='preview', items=items, not_asked=si.not_asked(period, mapping),
               import_count=sum(1 for i in items if i['status'] == 'import'),
               skip_count=sum(1 for i in items if i['status'] != 'import'),
               # Who a row can be tied to: the members who may apply.
               members=list(ParliamentUser.objects.filter(member_status='Active')
                            .exclude(member_type='Advisor').order_by('name')))
    return ctx


@login_required
@slating_chair_required
def import_applications(request, period_id):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if request.method != 'POST':
        return render(request, 'slating/import_applications.html', _context(period, step='upload'))

    step = request.POST.get('step') or ''
    try:
        # The page reads the files in the browser and posts their text
        # (`csv_text`, with `csv_name`): a form holding a file input is
        # resent by core.js's CSRF guard over fetch(), which follows only a
        # redirect and dropped this step's page, so "Continue" did nothing
        # (v4.3.0). A file itself is still accepted.
        upload = request.FILES.get('csv_file') if step == 'upload' else None
        if upload is not None:
            if upload.size > si.MAX_BYTES:
                raise si.ImportError_('That file is larger than 2 MB.')
            files = [(upload.name, si.decode(upload.read()))]
        else:
            files = _posted_files(request.POST)
        if step.startswith('remove_'):
            n = si._int(step[7:])
            removed = files.pop(n) if n is not None and 0 <= n < len(files) else None
            if removed:
                messages.info(request, f'Took {removed[0]} out of this import.')
            if not files:
                return render(request, 'slating/import_applications.html', _context(period, step='upload'))
        headers, rows, sources = si.combine_files(files)
    except si.ImportError_ as exc:
        messages.error(request, str(exc))
        return render(request, 'slating/import_applications.html', _context(period, step='upload'))

    fields = si.importable_fields(period)
    if step == 'upload':
        mapping = si.guess_mapping(headers, fields)
    elif step == 'add' or step.startswith('remove_'):
        mapping = _kept_mapping(request.POST, headers, fields)
    else:
        mapping = si.mapping_from_post(request.POST, len(headers))
    keep_unmapped = step == 'upload' or request.POST.get('keep_unmapped') == '1'
    columns = [{'index': i, 'header': h or f'Column {i + 1}', 'kind': mapping.get(i, ''),
                'sample': next((r[i] for r in rows if r[i].strip()), '')}
               for i, h in enumerate(headers)]
    ctx = _context(period, step='map', files=[{'name': n, 'text': t} for n, t in files],
                   columns=columns, keep_unmapped=keep_unmapped, row_count=len(rows))

    if step in ('preview', 'import'):
        problems = si.validate_mapping(mapping, fields)
        if problems:
            for problem in problems:
                messages.error(request, problem)
            return render(request, 'slating/import_applications.html', ctx)

    if step == 'preview':
        _preview(ctx, period, headers, rows, mapping, keep_unmapped, request.POST, sources)
        return render(request, 'slating/import_applications.html', ctx)

    if step == 'import' and request.POST.get('confirmed') != '1':
        messages.error(request, "Tick the box confirming you've checked who is getting an "
                                "application and what's in it.")
        _preview(ctx, period, headers, rows, mapping, keep_unmapped, request.POST, sources)
        return render(request, 'slating/import_applications.html', ctx)

    if step == 'import':
        done, skipped = si.commit(period, headers, rows, mapping, keep_unmapped,
                                  request.user, request, request.POST, sources)
        if done:
            messages.success(request, f'Imported {len(done)} application(s). '
                                      f'{len(skipped)} row(s) skipped. Anything imported by '
                                      f'mistake can be removed under Earlier imports.')
        else:
            messages.warning(request, 'Nothing was imported. '
                                      f'{len(skipped)} row(s) skipped.')
        return redirect('slating_applications', period_id=period.id)

    return render(request, 'slating/import_applications.html', ctx)


@login_required
@slating_chair_required
@require_POST
def remove_imported_applications(request, period_id):
    """
    Undo an import (v4.3.0): delete the applications one import made
    (``batch`` = its ``imported_at``), or one of them (``app``). Only
    imported applications, and only while nothing has been built on them
    (still submitted or under review, no interview, not slated); those are
    kept and listed. Logged like the import itself.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    apps = period.applications.filter(imported_at__isnull=False).select_related('applicant')
    if request.POST.get('app'):
        apps = apps.filter(id=si._int(request.POST['app']) or 0)
    else:
        batch = next((b for b in _earlier_imports(period) if b['key'] == request.POST.get('batch')), None)
        apps = apps.filter(id__in=[a.id for a in batch['apps']] if batch else [])
    apps = list(apps.annotate(n_interviews=Count('interviews', distinct=True),
                              n_slated=Count('slate_assignments', distinct=True)))
    removable = [a for a in apps if a.status in REMOVABLE_STATUSES and not a.n_interviews
                 and not a.n_slated]
    kept = [a for a in apps if a not in removable]
    if removable:
        names = ', '.join(a.applicant.name for a in removable)
        for app in removable:
            SlatingActivity.objects.create(
                period=period, user=request.user, action='application_reviewed',
                details=f"Removed {app.applicant.name}'s imported application",
                metadata={'application_id': app.id, 'imported': True, 'removed': True},
            )
        SlatingApplication.objects.filter(id__in=[a.id for a in removable]).delete()
        ActivityLog.log_activity(
            action_type='other', user=request.user,
            description=(f'{request.user.name} removed {len(removable)} imported slating '
                         f'application(s) from "{period.name}": {names}'),
            request=request, object_type='SlatingPeriod', object_id=period.id,
            object_repr=period.name, metadata={'removed': [a.id for a in removable]},
        )
        messages.success(request, f'Removed {len(removable)} imported application(s): {names}.')
    if kept:
        messages.warning(request, 'Kept (already interviewed, slated or past review): '
                                  + ', '.join(a.applicant.name for a in kept) + '.')
    if not apps:
        messages.info(request, 'Nothing to remove.')
    return redirect('slating_import_applications', period_id=period.id)


@login_required
@slating_chair_required
def fill_imported_answers(request, period_id, app_id):
    """
    Let a manager type in answers on an IMPORTED application (v4.0.0): the
    questions the outside form never asked, or a correction to an imported
    answer. Only imported applications — a member's own application is theirs,
    and is never edited on their behalf. Each save is logged (SlatingActivity)
    and noted in the review notes; questions answered here leave the
    "not asked on the outside form" list.
    """
    from django.http import Http404
    from django.utils import timezone
    from src.models import SlatingActivity, SlatingApplication, SlatingApplicationResponse

    period = get_object_or_404(SlatingPeriod, id=period_id)
    app = get_object_or_404(SlatingApplication, id=app_id, period=period)
    if not app.imported_at:
        raise Http404
    fields = si.importable_fields(period)
    current = {r.field_id: r for r in app.responses.filter(field__in=fields)}

    def shown(field):
        r = current.get(field.id)
        if r is None:
            return ''
        if field.field_type in ('multiselect', 'checkbox'):
            return ', '.join(str(v) for v in (r.json_value or []))
        if field.field_type in ('number', 'decimal'):
            return '' if r.number_value is None else str(r.number_value.normalize())
        return r.text_value or ''

    rows = [{'field': f, 'value': shown(f),
             'not_asked': f.label in (app.import_not_asked or [])} for f in fields]

    if request.method == 'POST':
        errors, changed = [], []
        for row in rows:
            field = row['field']
            raw = request.POST.get(f'field_{field.id}', '')
            if raw.strip() == row['value'].strip():
                continue
            value, warning = si._convert(field, raw)
            if warning:
                errors.append(warning.replace(' (left blank)', ''))
                continue
            response, _ = SlatingApplicationResponse.objects.get_or_create(application=app, field=field)
            response.text_value = response.number_value = response.json_value = None
            if value not in (None, '', []):
                if field.field_type in ('multiselect', 'checkbox'):
                    response.json_value = value
                elif field.field_type in ('number', 'decimal'):
                    response.number_value = value
                else:
                    response.text_value = value
                response.save()
            else:
                response.delete()
            changed.append(field.label)
        if errors:
            for e in errors:
                messages.error(request, e)
            for row in rows:
                row['value'] = request.POST.get(f'field_{row["field"].id}', row['value'])
            return render(request, 'slating/fill_imported_answers.html',
                          {'period': period, 'application': app, 'rows': rows})
        if changed:
            answered = set(app.responses.values_list('field__label', flat=True))
            app.import_not_asked = [l for l in (app.import_not_asked or []) if l not in answered]
            stamp = timezone.localtime().strftime('%m/%d/%Y')
            app.review_notes = (app.review_notes + '\n' if app.review_notes else '') + (
                f'{request.user.get_display_name()} filled in on {stamp}: ' + ', '.join(changed) + '.')
            app.save(update_fields=['import_not_asked', 'review_notes'])
            SlatingActivity.objects.create(
                period=period, user=request.user, action='application_reviewed',
                details=f'Filled in answers on {app.applicant.name}\'s imported application',
                metadata={'application_id': app.id, 'fields': changed, 'imported': True},
            )
            messages.success(request, f'Saved {len(changed)} answer(s).')
        else:
            messages.info(request, 'No changes.')
        return redirect('slating_app_detail', period_id=period.id, app_id=app.id)

    return render(request, 'slating/fill_imported_answers.html',
                  {'period': period, 'application': app, 'rows': rows})
