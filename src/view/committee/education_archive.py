"""
Previous pledge classes (11-10-26): the education dashboard as it stood when
each class was initiated (src/pledge_class_archive.py builds and corrects the
snapshots). Anyone with education access can read one; only the education
admin (a chair or a site admin: ``access['is_full_access']``) can correct it.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from src.feature_flag_decorators import require_page_enabled
from src.models import PledgeClassArchive
from src.pledge_class_archive import RESULT_STATUSES, ArchiveEditError, edit_pledge, edit_result, rename
from src.view.committee.education import _education_committee_or_404

ATTENDANCE_LABELS = {'pending': '—', 'present': 'Present', 'late': 'Late', 'excused': 'Excused', 'absent': 'Absent'}


def class_archives(committee):
    """The committee's archived classes, newest first (the dashboard's card)."""
    return list(PledgeClassArchive.objects.filter(committee=committee).order_by('-initiated_at'))


def _archive(committee, pk):
    return get_object_or_404(PledgeClassArchive, pk=pk, committee=committee)


def archive_context(archive):
    """The class page's tables, laid out for the template (lookups done here)."""
    data = archive.data or {}
    pledges = data.get('pledges', [])
    results = data.get('results', {})
    task_rows = []
    for t in data.get('tasks', []):
        cells = results.get(str(t['id']), {})
        task_rows.append({'task': t, 'cells': [(p, cells.get(p['user_id'], {'status': 'pending',
                                                                           'status_label': 'Not marked'}))
                                               for p in pledges]})
    meeting_rows = [{
        'meeting': m,
        'date': parse_datetime(m['date']) if m.get('date') else None,
        'cells': [(p, st, ATTENDANCE_LABELS.get(st, st))
                  for p in pledges for st in [m.get('attendance', {}).get(p['user_id'], 'pending')]],
    } for m in data.get('meetings', [])]
    adjustments = data.get('adjustments', {})
    adjustment_rows = [(p, adjustments.get(p['user_id'], [])) for p in pledges if adjustments.get(p['user_id'])]
    edits = []
    for e in reversed(data.get('edits', [])):
        edits.append({**e, 'when': parse_datetime(e['at']) if e.get('at') else None})
    return {
        'pledges': pledges,
        'tasks': data.get('tasks', []),
        'task_rows': task_rows,
        'meeting_rows': meeting_rows,
        'adjustment_rows': adjustment_rows,
        'edits': edits,
        'RESULT_STATUSES': RESULT_STATUSES,
    }


@login_required
@require_page_enabled('committee_home')
def education_class_archive(request, code, archive_pk):
    committee, access = _education_committee_or_404(code, request.user)
    archive = _archive(committee, archive_pk)
    return render(request, 'committee/education_class_archive.html', {
        'committee': committee,
        'archive': archive,
        'access': access,
        'can_edit': access['is_full_access'],
        'archives': class_archives(committee),
        **archive_context(archive),
    })


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_class_archive_edit(request, code, archive_pk):
    committee, access = _education_committee_or_404(code, request.user)
    if not access['is_full_access']:
        raise Http404
    archive = _archive(committee, archive_pk)
    action = request.POST.get('action')
    try:
        if action == 'rename':
            changed = rename(archive, request.user, request.POST.get('name'), request.POST.get('notes'))
        elif action == 'pledge':
            changed = edit_pledge(archive, request.user, request.POST.get('user_id', ''),
                                  request.POST.get('points'), request.POST.get('max_points'),
                                  request.POST.get('note'))
        elif action == 'result':
            changed = edit_result(archive, request.user, request.POST.get('task_id'),
                                  request.POST.get('user_id', ''), request.POST.get('status'),
                                  request.POST.get('score'))
        else:
            raise ArchiveEditError('Unknown change.')
    except ArchiveEditError as e:
        messages.error(request, str(e))
    else:
        if changed:
            messages.success(request, 'Saved. The change is recorded in this class\'s edit history.')
        else:
            messages.info(request, 'Nothing changed.')
    anchor = {'pledge': '#class-pledges', 'result': '#class-results'}.get(action, '')
    return redirect(f"{request.path.rsplit('edit/', 1)[0]}{anchor}")
