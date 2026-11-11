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
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from src.feature_flag_decorators import require_page_enabled
from src.models import PledgeClassArchive
from src.pledge_class_archive import (
    RESULT_STATUSES, ArchiveEditError, edit_pledge, edit_result, remove_member, rename, restore_member,
)
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
        'removed': list(reversed(data.get('removed', []))),
        'held_back': data.get('held_back', []),
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
        elif action == 'remove':
            remove_member(archive, request.user, request.POST.get('user_id', ''))
            changed = True
        elif action == 'restore':
            restore_member(archive, request.user, request.POST.get('user_id', ''))
            changed = True
        else:
            raise ArchiveEditError('Unknown change.')
    except ArchiveEditError as e:
        messages.error(request, str(e))
    else:
        if changed:
            messages.success(request, 'Saved. The change is recorded in this class\'s edit history.')
        else:
            messages.info(request, 'Nothing changed.')
    anchor = {'pledge': '#class-pledges', 'remove': '#class-pledges', 'restore': '#class-pledges',
              'result': '#class-results'}.get(action, '')
    return redirect(f"{request.path.rsplit('edit/', 1)[0]}{anchor}")


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_rerequire_task(request, code, pledge_pk):
    """
    Re-require a task (``task_pk``) or every task he has a result for
    (``all``) for a pledge, keeping the earlier attempt (11-11-26: a pledge
    held back a semester). For whoever can manage tasks.
    """
    from django.db import transaction
    from src.models import ParliamentUser, PledgeTask, PledgeTaskCompletion
    from src.pledge_class_archive import rerequire_task
    committee, access = _education_committee_or_404(code, request.user)
    if not access['can_manage_tasks']:
        raise Http404
    pledge = get_object_or_404(ParliamentUser, pk=pledge_pk, member_type='Pledge')
    if request.POST.get('all'):
        tasks = list(PledgeTask.objects.filter(
            is_active=True, pk__in=PledgeTaskCompletion.objects.filter(pledge=pledge).values('task_id')))
    else:
        tasks = [get_object_or_404(PledgeTask, pk=request.POST.get('task_pk') or 0, is_active=True)]
    try:
        with transaction.atomic():
            for task in tasks:
                rerequire_task(task, pledge, request.user, committee)
    except ArchiveEditError as e:
        messages.error(request, str(e))
    else:
        if not tasks:
            messages.info(request, f'{pledge.name} has no task results to re-require.')
        else:
            n = len(tasks)
            messages.success(request, f'Re-required {n} task{"" if n == 1 else "s"} for {pledge.name}. '
                                      'His earlier results are kept under each task.')
    return redirect('education_pledge_detail', code=committee.code, pledge_pk=pledge.pk)


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_held_back_setting(request, code):
    """The education admin turns the semester points reset for held-back pledges on or off."""
    from src.pledge_class_archive import set_reset_enabled
    committee, access = _education_committee_or_404(code, request.user)
    if not access['is_full_access']:
        raise Http404
    on = request.POST.get('reset') == 'on'
    set_reset_enabled(on, request.user)
    messages.success(request, 'Held-back pledges\' points will reset at the start of each semester.' if on
                     else 'Held-back pledges keep their points into the next semester.')
    return redirect(f"{reverse('education_home', args=[committee.code])}#previous-classes")


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_clear_tasks(request, code):
    """'Clear all tasks' (11-11-26): the education admin hides every current task; undoable."""
    from src.pledge_class_archive import clear_tasks
    committee, access = _education_committee_or_404(code, request.user)
    if not access['is_full_access']:
        raise Http404
    n = clear_tasks(request.user, request)
    if n:
        messages.success(request, f'Cleared {n} task{"" if n == 1 else "s"}. Pledges\' results are kept, and you '
                                  'can restore them from the dashboard.')
    else:
        messages.info(request, 'There were no tasks to clear.')
    return redirect('education_home', code=committee.code)


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_restore_cleared_tasks(request, code):
    """Undo the latest 'Clear all tasks'."""
    from src.pledge_class_archive import restore_cleared_tasks
    committee, access = _education_committee_or_404(code, request.user)
    if not access['is_full_access']:
        raise Http404
    n = restore_cleared_tasks(request.user, request)
    if n:
        messages.success(request, f'Restored {n} task{"" if n == 1 else "s"}.')
    else:
        messages.info(request, 'Nothing to restore.')
    return redirect('education_home', code=committee.code)
