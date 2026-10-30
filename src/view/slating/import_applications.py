"""
Import applications from another app's CSV export (v4.0.0).

GET  → upload form.
POST step=upload  → parse the file, show the column mapping (guessed).
POST step=preview → show what would happen to every row. No writes.
POST step=import  → re-plan from the CSV + mapping and write (never trusts
                    the preview). Then back to the applications list.

The CSV text rides along in a hidden field between steps rather than in the
session: it's the manager's own upload, and keeping confidential applicant
data out of the session table means nothing is left behind if they walk away.

Access: ``slating_chair_required`` (slating manager, the committee's admin or
chairs; site admins only while the period isn't locked) — the same people who
manage the application form. Logic: ``src/slating_import.py``.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from src import slating_import as si
from src.models import SlatingPeriod
from .permissions import slating_chair_required


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
        **extra,
    }


@login_required
@slating_chair_required
def import_applications(request, period_id):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if request.method != 'POST':
        return render(request, 'slating/import_applications.html', _context(period, step='upload'))

    step = request.POST.get('step')
    try:
        if step == 'upload':
            # The page reads the file in the browser and posts its text
            # (`csv_text`): a form holding a file input is resent by
            # core.js's CSRF guard over fetch(), which follows only a
            # redirect and dropped this step's page, so "Continue" did
            # nothing (v4.3.0). The file itself is still accepted.
            upload = request.FILES.get('csv_file')
            if upload is not None:
                if upload.size > si.MAX_BYTES:
                    raise si.ImportError_('That file is larger than 2 MB.')
                csv_text = si.decode(upload.read())
            else:
                csv_text = request.POST.get('csv_text', '').lstrip('﻿')
                if not csv_text.strip():
                    raise si.ImportError_('Choose a CSV file to upload.')
        else:
            csv_text = request.POST.get('csv_text', '')
        headers, rows = si.parse_csv(csv_text)
    except si.ImportError_ as exc:
        messages.error(request, str(exc))
        return render(request, 'slating/import_applications.html', _context(period, step='upload'))

    fields = si.importable_fields(period)
    if step == 'upload':
        mapping = si.guess_mapping(headers, fields)
    else:
        mapping = si.mapping_from_post(request.POST, len(headers))
    keep_unmapped = step == 'upload' or request.POST.get('keep_unmapped') == '1'
    columns = [{'index': i, 'header': h or f'Column {i + 1}', 'kind': mapping.get(i, ''),
                'sample': next((r[i] for r in rows if r[i].strip()), '')}
               for i, h in enumerate(headers)]
    ctx = _context(period, step='map', csv_text=csv_text, columns=columns,
                   keep_unmapped=keep_unmapped, row_count=len(rows))

    if step in ('preview', 'import'):
        problems = si.validate_mapping(mapping, fields)
        if problems:
            for problem in problems:
                messages.error(request, problem)
            return render(request, 'slating/import_applications.html', ctx)

    if step == 'preview':
        items = si.plan(period, headers, rows, mapping, keep_unmapped)
        ctx.update(step='preview', items=items,
                   not_asked=si.not_asked(period, mapping),
                   import_count=sum(1 for i in items if i['status'] == 'import'),
                   skip_count=sum(1 for i in items if i['status'] != 'import'))
        return render(request, 'slating/import_applications.html', ctx)

    if step == 'import' and request.POST.get('confirmed') != '1':
        messages.error(request, "Tick the box confirming you've checked who is getting an "
                                "application and what's in it.")
        items = si.plan(period, headers, rows, mapping, keep_unmapped)
        ctx.update(step='preview', items=items, not_asked=si.not_asked(period, mapping),
                   import_count=sum(1 for i in items if i['status'] == 'import'),
                   skip_count=sum(1 for i in items if i['status'] != 'import'))
        return render(request, 'slating/import_applications.html', ctx)

    if step == 'import':
        done, skipped = si.commit(period, headers, rows, mapping, keep_unmapped,
                                  request.user, request)
        if done:
            messages.success(request, f'Imported {len(done)} application(s). '
                                      f'{len(skipped)} row(s) skipped.')
        else:
            messages.warning(request, 'Nothing was imported. '
                                      f'{len(skipped)} row(s) skipped.')
        return redirect('slating_applications', period_id=period.id)

    return render(request, 'slating/import_applications.html', ctx)


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
