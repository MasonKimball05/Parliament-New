"""
Pledge class archives (11-10-26, Mason): "the education dashboard keeps no
track of the previous pledges, their tasks + results, points, etc... When
pledges are initiated also have it archive the current education dashboard...
Allow the education admin to make manual changes to it after, but it's closed
and is mainly to just act as a snapshot for later."

At initiation (``src/view/officer/manage_members.initiate_pledge_batch``,
which the website and the app share) the initiated pledges' dashboard is
frozen into a ``PledgeClassArchive``: the numbers come from the dashboard's
own ``education_overview``, so the snapshot shows exactly what the dashboard
showed that day.

``data`` shape::

    {
      "version": 1,
      "tasks":    [{id, title, type, type_label, phase, points, is_required, due_date, max_score}],
      "pledges":  [{user_id, name, role_number, pledge_class, phase, points, max_points,
                    points_percent, completed, total, percent, big,
                    attendance: {present, late, excused, absent}, note}],
      "results":  {task id: {user_id: {status, status_label, score, completed_at, notes,
                                      reviewed_by} | {"na": true}}},
      "meetings": [{id, title, date, type_label, points, attendance: {user_id: status}}],
      "adjustments": {user_id: [{current_delta, max_delta, reason, created_by, created_at}]},
      "edits":    [{at, by, what}],
    }

Keys in JSON are strings: task ids and user ids alike.

**One archive per semester** (11-11-26, Mason: "auto archives their specific
information into the semester's PC archive. We've had multi-semester pledges
when we felt they were not ready for initiation"). The archive is keyed by
the semester of initiation (``term``; Spring is January-June, as the
pledge-class registry). Everyone initiated that semester goes in it: the
class, a straggler a few days later, and a pledge held back from an earlier
semester (his row keeps his own ``pledge_class``). A held-back pledge keeps
the earlier semester's meetings he was on the roster for, in his points and
in the meeting list.

Corrections (``rename``, ``edit_pledge``, ``edit_result``) are for the
education admin only (chairs and site admins: ``access['is_full_access']``);
each is recorded in ``edits``.
"""
from django.utils import timezone

SNAPSHOT_VERSION = 1

RESULT_STATUSES = (
    ('pending', 'Pending Review'),
    ('completed', 'Completed'),
    ('incomplete', 'Incomplete'),
    ('waived', 'Waived'),
)
_RESULT_LABELS = dict(RESULT_STATUSES)


class ArchiveEditError(Exception):
    """A correction that can't be saved; the message is for people."""


def meetings_since(committee, before=None):
    """
    When the current class's meetings start: the latest archived class's
    initiation, or None (no class archived yet: every meeting counts).
    ``before``: the cutoff for the class archived at that time instead (a
    straggler joining it uses his classmates' cutoff, not their archive's).
    """
    from src.models import PledgeClassArchive
    qs = PledgeClassArchive.objects.filter(committee=committee)
    if before is not None:
        qs = qs.filter(initiated_at__lt=before)
    return qs.order_by('-initiated_at').values_list('initiated_at', flat=True).first()


def education_committees():
    from src.models import Committee
    return Committee.objects.filter(is_education_committee=True, is_active=True).order_by('pk')


def term_for(when):
    """'Fall 2026': the semester of ``when`` (Spring is January-June, as src/pledge_classes.py)."""
    local = timezone.localtime(when)
    return f"{'Fall' if local.month >= 7 else 'Spring'} {local.year}"


def default_name(when):
    """'Theta class (Fall 2026)': the registry's class for that semester. The admin can rename it."""
    from src import pledge_classes
    term = term_for(when)
    entry = pledge_classes.class_by_label(term, today=timezone.localdate(when))
    if entry is None:
        return f'{term} pledge class'
    if entry['is_founders']:
        return f'Founders ({term})'
    return f'{entry["greek"]} class ({term})'


def _iso(dt):
    return timezone.localtime(dt).isoformat() if dt else None


def build_snapshot(committee, pledges, role_numbers=None, since='latest', as_of=None):
    """
    The snapshot ``data`` for ``pledges`` (a queryset) on ``committee``'s
    dashboard, as it stands now. ``role_numbers``: {user_id: roll number}
    being assigned at initiation (the users still hold the pledge ids then).
    """
    from src.models import EducationMeeting, EducationMeetingAttendance, PledgeBigAssignment
    from src.view.committee.education import education_overview

    role_numbers = role_numbers or {}
    if since == 'latest':
        since = meetings_since(committee)
    overview = education_overview(committee, pledges=pledges, since=since, as_of=as_of)
    pledge_list = list(overview['pledges'])
    pledge_ids = [p.pk for p in pledge_list]

    tasks = [{
        'id': t.pk,
        'title': t.title,
        'type': t.task_type,
        'type_label': t.get_task_type_display(),
        'phase': t.phase,
        'points': t.points or 0,
        'is_required': bool(t.is_required),
        'due_date': t.due_date.isoformat() if t.due_date else None,
        'max_score': t.max_score,
    } for t in overview['tasks']]

    results = {}
    for row in overview['task_rows']:
        cells = {}
        for pc in row['pledge_completions']:
            uid = pc['pledge'].pk
            if not pc['applies']:
                cells[uid] = {'na': True}
                continue
            c = pc['completion']
            cells[uid] = {
                'status': c.status if c else 'pending',
                'status_label': c.get_status_display() if c else 'Not marked',
                'score': c.score if c else None,
                'completed_at': _iso(c.completed_at) if c else None,
                'notes': (c.notes or '') if c else '',
                'reviewed_by': (c.reviewed_by.name if c and c.reviewed_by_id and c.reviewed_by else ''),
            }
        results[str(row['task'].pk)] = cells

    # This class's meetings: everything since the previous archived class,
    # plus earlier ones a held-back pledge here was on the roster for.
    meetings_qs = (EducationMeeting.objects.filter(committee=committee, event__date_time__lte=as_of or timezone.now())
                   .select_related('event'))
    if since:
        from django.db.models import Q
        meetings_qs = meetings_qs.filter(
            Q(event__date_time__gte=since) | Q(attendance_records__pledge_id__in=pledge_ids)).distinct()
    meetings = sorted(meetings_qs, key=lambda m: m.event.date_time)
    attendance = {}
    for r in EducationMeetingAttendance.objects.filter(meeting__in=meetings, pledge_id__in=pledge_ids):
        attendance.setdefault(r.meeting_id, {})[r.pledge_id] = r.status
    meeting_rows = [{
        'id': m.pk,
        'title': m.event.title,
        'date': _iso(m.event.date_time),
        'type_label': m.get_meeting_type_display(),
        'points': m.points or 0,
        'attendance': attendance.get(m.pk, {}),
    } for m in meetings]

    bigs = {a.pledge_id: a for a in PledgeBigAssignment.objects.filter(pledge_id__in=pledge_ids).select_related('big')}

    pledges_out = []
    for ps in overview['pledge_summaries']:
        p = ps['pledge']
        counts = {'present': 0, 'late': 0, 'excused': 0, 'absent': 0}
        for m in meeting_rows:
            status = m['attendance'].get(p.pk)
            if status in counts:
                counts[status] += 1
        big = bigs.get(p.pk)
        pledges_out.append({
            'user_id': p.pk,
            'name': p.name,
            'role_number': role_numbers.get(p.pk) or p.role_number or '',
            'pledge_class': (p.pledge_class or '').strip(),
            'phase': p.pledge_phase,
            'points': ps.get('points', 0),
            'max_points': ps.get('max_points', 0),
            'points_percent': ps.get('points_percent'),
            'completed': ps['completed'],
            'total': ps['total'],
            'percent': ps['percent'],
            'big': (big.big.name if big and big.big_id else ''),
            'attendance': counts,
            'note': '',
        })

    return {
        'version': SNAPSHOT_VERSION,
        'tasks': tasks,
        'pledges': pledges_out,
        'results': results,
        'meetings': meeting_rows,
        'adjustments': overview['adjustments_history'],
        'edits': [],
    }


def _merge(data, new):
    """Fold a later batch's snapshot into an existing one (pledges already in it are left alone)."""
    have = {p['user_id'] for p in data.get('pledges', [])}
    added = [p for p in new['pledges'] if p['user_id'] not in have]
    data.setdefault('pledges', []).extend(added)
    data['pledges'].sort(key=lambda p: (p['name'] or '').lower())
    ids = {p['user_id'] for p in added}
    task_ids = {t['id'] for t in data.get('tasks', [])}
    data.setdefault('tasks', []).extend(t for t in new['tasks'] if t['id'] not in task_ids)
    for task_id, cells in new['results'].items():
        bucket = data.setdefault('results', {}).setdefault(task_id, {})
        for uid, cell in cells.items():
            if uid in ids:
                bucket[uid] = cell
    meetings = {m['id']: m for m in data.get('meetings', [])}
    for m in new['meetings']:
        if m['id'] in meetings:
            meetings[m['id']]['attendance'].update({u: s for u, s in m['attendance'].items() if u in ids})
        else:
            meetings[m['id']] = {**m, 'attendance': {u: s for u, s in m['attendance'].items() if u in ids}}
    data['meetings'] = sorted(meetings.values(), key=lambda m: m['date'] or '')
    for uid, rows in new['adjustments'].items():
        if uid in ids:
            data.setdefault('adjustments', {})[uid] = rows
    return len(added)


def _save_into_term(committee, pledges, role_numbers, when, actor_name, created_by, as_of=None, note=None):
    """
    Snapshot ``pledges`` into ``committee``'s archive for the semester of
    ``when``: added to it if one exists (anyone already in it is left
    alone), else a new one. Returns the archive, or None if nothing to save.
    """
    from src.models import PledgeClassArchive
    term = term_for(when)
    archive = (PledgeClassArchive.objects.select_for_update()
               .filter(committee=committee, term=term).order_by('initiated_at').first())
    # Joining a semester's archive: that semester's cutoff, not its own.
    since = meetings_since(committee, before=archive.initiated_at) if archive else meetings_since(committee, before=when)
    snap = build_snapshot(committee, pledges, role_numbers, since=since, as_of=as_of)
    if not snap['pledges']:
        return None
    if archive is None:
        if note:
            snap['edits'].append({'at': _iso(timezone.now()), 'by': actor_name, 'what': note})
        return PledgeClassArchive.objects.create(
            committee=committee, name=default_name(when), term=term, initiated_at=when,
            created_by=created_by, data=snap)
    data = archive.data or {}
    added = _merge(data, snap)
    if not added:
        return archive
    data.setdefault('edits', []).append({
        'at': _iso(timezone.now()), 'by': actor_name,
        'what': note or f'{added} more initiated this semester and added to this class.',
    })
    archive.data = data
    archive.save(update_fields=['data', 'updated_at'])
    return archive


def archive_initiated(pledges, actor=None, role_numbers=None, when=None):
    """
    Snapshot ``pledges`` (a queryset, about to be initiated) into the
    semester's archive on every active education committee. Returns the
    archives touched. Call before the pledges stop being pledges; runs
    inside the caller's transaction.
    """
    when = when or timezone.now()
    touched = []
    for committee in education_committees():
        archive = _save_into_term(committee, pledges, role_numbers, when,
                                  actor.name if actor else 'Initiation', actor if actor and actor.pk else None)
        if archive is not None:
            touched.append(archive)
    return touched


# ── Corrections (the education admin) ─────────────────────────────────────────

def _log(archive, user, what):
    archive.data.setdefault('edits', []).append({'at': _iso(timezone.now()), 'by': user.name, 'what': what})


def rename(archive, user, name, notes):
    name = (name or '').strip()[:120]
    if not name:
        raise ArchiveEditError('Give the class a name.')
    notes = (notes or '').strip()
    changes = []
    if name != archive.name:
        changes.append(f'Renamed "{archive.name}" to "{name}".')
        archive.name = name
    if notes != archive.notes:
        changes.append('Edited the class notes.')
        archive.notes = notes
    if changes:
        _log(archive, user, ' '.join(changes))
        archive.save(update_fields=['name', 'notes', 'data', 'updated_at'])
    return bool(changes)


def _int(value, label, allow_negative=False):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ArchiveEditError(f'{label} must be a whole number.')
    if n < 0 and not allow_negative:
        raise ArchiveEditError(f'{label} can\'t be negative.')
    return n


def _pledge(archive, user_id):
    for p in archive.data.get('pledges', []):
        if p['user_id'] == user_id:
            return p
    raise ArchiveEditError('That member isn\'t in this class.')


def edit_pledge(archive, user, user_id, points, max_points, note):
    """Correct a pledge's points (earned and possible) and his note."""
    p = _pledge(archive, user_id)
    points = _int(points, 'Points', allow_negative=True)
    max_points = _int(max_points, 'Possible points')
    note = (note or '').strip()[:500]
    changes = []
    if points != p['points'] or max_points != p['max_points']:
        changes.append(f'{p["name"]}: points {p["points"]}/{p["max_points"]} to {points}/{max_points}.')
        p['points'], p['max_points'] = points, max_points
        p['points_percent'] = round(points / max_points * 100) if max_points else None
    if note != (p.get('note') or ''):
        changes.append(f'{p["name"]}: note edited.')
        p['note'] = note
    if changes:
        _log(archive, user, ' '.join(changes))
        archive.save(update_fields=['data', 'updated_at'])
    return bool(changes)


def edit_result(archive, user, task_id, user_id, status, score):
    """Correct one task result. Completed/required counts follow."""
    p = _pledge(archive, user_id)
    task = next((t for t in archive.data.get('tasks', []) if str(t['id']) == str(task_id)), None)
    if task is None:
        raise ArchiveEditError('That task isn\'t in this class.')
    if status not in _RESULT_LABELS:
        raise ArchiveEditError('Pick a status.')
    score = None if str(score or '').strip() == '' else _int(score, 'Score')
    if score is not None and task.get('max_score') and score > task['max_score']:
        raise ArchiveEditError(f'The score can\'t be more than {task["max_score"]}.')
    cells = archive.data.setdefault('results', {}).setdefault(str(task['id']), {})
    cell = cells.get(user_id) or {}
    if cell.get('na'):
        raise ArchiveEditError(f'"{task["title"]}" wasn\'t assigned to {p["name"]}.')
    old = (cell.get('status', 'pending'), cell.get('score'))
    if old == (status, score):
        return False
    cell.update({'status': status, 'status_label': _RESULT_LABELS[status], 'score': score})
    cells[user_id] = cell
    if task.get('is_required'):
        p['completed'] = sum(
            1 for t in archive.data['tasks'] if t.get('is_required')
            and archive.data['results'].get(str(t['id']), {}).get(user_id, {}).get('status') == 'completed')
        p['percent'] = round(p['completed'] / p['total'] * 100) if p.get('total') else 0
    score_txt = '' if score is None else f', score {score}'
    _log(archive, user, f'{p["name"]}, "{task["title"]}": {_RESULT_LABELS.get(old[0], old[0])} '
                        f'to {_RESULT_LABELS[status]}{score_txt}.')
    archive.save(update_fields=['data', 'updated_at'])
    return True


# ── Backfill (classes initiated before 11-10-26) ──────────────────────────────

def _archived_ids(committee):
    from src.models import PledgeClassArchive
    ids = set()
    for data in PledgeClassArchive.objects.filter(committee=committee).values_list('data', flat=True):
        ids.update(p['user_id'] for p in (data or {}).get('pledges', []))
    return ids


def backfill_from_log(log, save=True):
    """
    Rebuild the class initiated in ``log`` (an ``initiate_pledges``
    ActivityLog row) as its dashboard stood then: only tasks that existed,
    meetings held and adjustments made by that time. Initiation keeps
    members' ids, so their results, answers and attendance are still linked.
    Members already in an archive are skipped; the rest go into that
    semester's archive. Returns [(committee, archive or unsaved snapshot
    dict)]; ``save=False`` is a dry run.
    """
    from src.models import ParliamentUser
    when = log.timestamp
    ids = list((log.metadata or {}).get('pledge_ids') or [])
    role_numbers = (log.metadata or {}).get('role_numbers') or {}
    note = ('Rebuilt from the records still in the database, as the dashboard stood at initiation. '
            'This class was initiated before classes were saved automatically.')
    out = []
    for committee in education_committees():
        todo = ParliamentUser.objects.filter(pk__in=set(ids) - _archived_ids(committee)).order_by('name')
        if not todo.exists():
            continue
        if not save:
            since = meetings_since(committee, before=when)
            snap = build_snapshot(committee, todo, role_numbers, since=since, as_of=when)
            out.append((committee, {'name': default_name(when), **snap}))
            continue
        archive = _save_into_term(committee, todo, role_numbers, when, 'Backfill',
                                  log.user if log.user_id else None, as_of=when, note=note)
        if archive is not None:
            out.append((committee, archive))
    return out


def remove_member(archive, user, user_id):
    """
    Take a member off the class page (11-11-26: an initiation made by
    mistake). Everything about him moves to ``data['removed']``, so
    ``restore_member`` can put him back.
    """
    data = archive.data
    p = _pledge(archive, user_id)
    data['pledges'] = [x for x in data['pledges'] if x['user_id'] != user_id]
    results = {tid: cells.pop(user_id) for tid, cells in data.get('results', {}).items() if user_id in cells}
    attendance = {str(m['id']): m['attendance'].pop(user_id)
                  for m in data.get('meetings', []) if user_id in m.get('attendance', {})}
    adjustments = data.get('adjustments', {}).pop(user_id, [])
    data.setdefault('removed', []).append({
        'pledge': p, 'results': results, 'attendance': attendance, 'adjustments': adjustments,
        'removed_at': _iso(timezone.now()), 'removed_by': user.name,
    })
    _log(archive, user, f'Removed {p["name"]} from this class (kept under Removed, and can be restored).')
    archive.save(update_fields=['data', 'updated_at'])


def restore_member(archive, user, user_id):
    data = archive.data
    entry = next((r for r in data.get('removed', []) if r['pledge']['user_id'] == user_id), None)
    if entry is None:
        raise ArchiveEditError('That member isn\'t in the removed list.')
    data['removed'].remove(entry)
    data.setdefault('pledges', []).append(entry['pledge'])
    data['pledges'].sort(key=lambda x: (x['name'] or '').lower())
    for tid, cell in entry['results'].items():
        data.setdefault('results', {}).setdefault(tid, {})[user_id] = cell
    for m in data.get('meetings', []):
        if str(m['id']) in entry['attendance']:
            m.setdefault('attendance', {})[user_id] = entry['attendance'][str(m['id'])]
    if entry['adjustments']:
        data.setdefault('adjustments', {})[user_id] = entry['adjustments']
    _log(archive, user, f'Restored {entry["pledge"]["name"]} to this class.')
    archive.save(update_fields=['data', 'updated_at'])


# ── Initiating outside Initiate Pledges (11-11-26) ────────────────────────────

INITIATE_ANYWAY_FIELD = 'confirm_skip_initiation'
SKIPPING_INITIATION = (
    'Use Initiate Pledges to initiate a pledge: it gives him his roll number and saves his pledge '
    'class\'s education record. To change his member type here anyway, tick "Initiate anyway".'
)


def pledge_type_change_error(member, new_type, confirmed, actor=None):
    """
    Mason, 11-11-26: "restrict, but not prevent fully" turning a pledge into
    a member from a member editor (Admin v2's, the officers' Edit Member).
    Without ``confirmed`` the change is refused with the reason; with it, his
    class snapshot is saved first (as Initiate Pledges does) and '' is
    returned. Also '' for any change that isn't from Pledge to something else.
    """
    if member is None or member.member_type != 'Pledge' or not new_type or new_type == 'Pledge':
        return ''
    if not confirmed:
        return SKIPPING_INITIATION
    import logging
    from django.db import transaction
    from src.models import ParliamentUser
    try:
        with transaction.atomic():
            archive_initiated(ParliamentUser.objects.filter(pk=member.pk), actor if getattr(actor, 'pk', None) else None)
    except Exception:
        logging.getLogger(__name__).exception('Pledge class archive failed; changing the member type anyway')
    return ''


# ── Re-requiring a task (a pledge held back a semester; 11-11-26) ─────────────

#: Reason prefix on the point adjustment a semester reset writes (``reset_points``).
RESET_REASON = 'Semester reset: '


def rerequire_task(task, pledge, user, committee=None):
    """
    Open ``task`` again for ``pledge``: his completion (and a quiz's answers)
    are copied to a ``PledgeTaskAttempt`` and cleared. If his points were
    reset since he did it, the task comes back out of that reset (an
    adjustment), so he isn't docked for redoing it. Raises ArchiveEditError
    when there's nothing to re-require.
    """
    from src.models import PledgePointAdjustment, PledgeQuizAnswer, PledgeTaskAttempt, PledgeTaskCompletion
    from src.pledge_points import points_for, quiz_marks

    completion = (PledgeTaskCompletion.objects.filter(task=task, pledge=pledge)
                  .select_related('reviewed_by').first())
    answers = list(PledgeQuizAnswer.objects.filter(question__task=task, pledge=pledge)
                   .order_by('question__display_order', 'pk'))
    if completion is None and not answers:
        raise ArchiveEditError(f'{pledge.name} hasn\'t done "{task.title}" yet, so there\'s nothing to re-require.')
    done_at = (completion.completed_at or completion.updated_at) if completion else answers[-1].submitted_at
    earned = points_for(task, completion, pledge.pk, quiz_marks([task], [pledge])) if completion else 0

    PledgeTaskAttempt.objects.create(
        task=task, pledge=pledge,
        status=completion.status if completion else 'pending',
        score=completion.score if completion else None,
        notes=(completion.notes or '') if completion else '',
        completed_at=completion.completed_at if completion else None,
        reviewed_by_name=(completion.reviewed_by.name if completion and completion.reviewed_by else ''),
        answers=[{'question': a.question.question_text, 'answer': a.answer_text, 'is_correct': a.is_correct}
                 for a in answers],
        term=term_for(done_at), rerequired_by=user if getattr(user, 'pk', None) else None,
    )
    reset = (PledgePointAdjustment.objects.filter(pledge=pledge, reason__startswith=RESET_REASON)
             .order_by('-created_at').first())
    if reset is not None and done_at and done_at < reset.created_at:
        possible = (task.points or 0) if task.is_live else 0
        if earned or possible:
            PledgePointAdjustment.objects.create(
                committee=reset.committee, pledge=pledge, current_delta=earned, max_delta=possible,
                reason=f'Re-required "{task.title}": taken back out of the semester reset'[:200],
                created_by=user if getattr(user, 'pk', None) else None)
    PledgeQuizAnswer.objects.filter(pk__in=[a.pk for a in answers]).delete()
    if completion is not None:
        completion.delete()


# ── Held-back pledges at the start of a semester (11-11-26) ───────────────────

#: SiteSetting key: reset held-back pledges' points when a new semester starts.
RESET_SETTING = 'education_reset_held_back_points'


def reset_enabled():
    from src.models_feature_flags import SiteSetting
    return bool(SiteSetting.get_setting(RESET_SETTING, False))


def set_reset_enabled(on, user):
    from django.core.cache import cache
    from src.models_feature_flags import SiteSetting
    SiteSetting.objects.update_or_create(key=RESET_SETTING, defaults={
        'display_name': 'Reset held-back pledges\' points each semester',
        'description': 'Education: when a new semester starts, a pledge held back from the last one starts '
                       'at zero points; the points he reached stay on last semester\'s class page.',
        'category': 'general', 'setting_type': 'boolean', 'value': 'true' if on else 'false',
        'default_value': 'false', 'last_modified_by': getattr(user, 'name', '') or '',
    })
    cache.delete(SiteSetting._cache_key(RESET_SETTING))


def semester_start(when):
    """Midnight on January 1 or July 1 (local) of ``when``'s semester."""
    from datetime import datetime
    local = timezone.localtime(when)
    return timezone.make_aware(datetime(local.year, 1 if local.month <= 6 else 7, 1))


def previous_term(term):
    season, year = term.split()
    return f'Fall {int(year) - 1}' if season == 'Spring' else f'Spring {year}'


def held_back_pledges(committee, start):
    """Active pledges with education records from before ``start``: they pledged last semester too."""
    from django.db.models import Q
    from src.models import ParliamentUser
    return (ParliamentUser.objects.filter(member_type='Pledge', is_active=True)
            .filter(Q(education_meeting_attendance__meeting__committee=committee,
                      education_meeting_attendance__meeting__event__date_time__lt=start)
                    | Q(task_completions__created_at__lt=start))
            .distinct().order_by('name'))


def save_held_back(now=None):
    """
    At the start of a semester, give every held-back pledge a frozen entry
    on the previous semester's class page (``data['held_back']``: his points,
    required tasks, attendance and task results as they stood), creating
    that page if nobody was initiated that semester. With the reset option
    on, his points then start again at zero (one adjustment, which the
    dashboard's history shows and a chair can delete). Safe to run daily:
    anyone already recorded for that semester is skipped. Returns how many
    were recorded.
    """
    from django.db import transaction
    from src.models import PledgeClassArchive, PledgePointAdjustment
    now = now or timezone.now()
    start = semester_start(now)
    last = previous_term(term_for(now))
    reset = reset_enabled()
    recorded = 0
    for committee in education_committees():
        pledges = list(held_back_pledges(committee, start))
        if not pledges:
            continue
        with transaction.atomic():
            archive = (PledgeClassArchive.objects.select_for_update()
                       .filter(committee=committee, term=last).first())
            if archive is None:
                archive = PledgeClassArchive.objects.create(
                    committee=committee, name=default_name(start - timezone.timedelta(days=1)), term=last,
                    initiated_at=start - timezone.timedelta(seconds=1),
                    data={'version': SNAPSHOT_VERSION, 'tasks': [], 'pledges': [], 'results': {}, 'meetings': [],
                          'adjustments': {}, 'edits': [], 'no_initiations': True})
            data = archive.data
            done = {h['pledge']['user_id'] for h in data.get('held_back', [])}
            todo = [p for p in pledges if p.pk not in done]
            if not todo:
                continue
            from src.models import ParliamentUser
            snap = build_snapshot(committee, ParliamentUser.objects.filter(pk__in=[p.pk for p in todo]),
                                  since=meetings_since(committee, before=archive.initiated_at), as_of=start)
            titles = {str(t['id']): t['title'] for t in snap['tasks']}
            for entry in snap['pledges']:
                uid = entry['user_id']
                data.setdefault('held_back', []).append({
                    'pledge': entry,
                    'results': [{'task': titles.get(tid, ''), **cells[uid]}
                                for tid, cells in snap['results'].items() if uid in cells and not cells[uid].get('na')],
                    'meetings': [{'title': m['title'], 'date': m['date'], 'status': m['attendance'][uid]}
                                 for m in snap['meetings'] if uid in m['attendance']],
                    'saved_at': _iso(now),
                    'points_reset': reset,
                })
                recorded += 1
                if reset and (entry['points'] or entry['max_points']):
                    pledge = next(p for p in todo if p.pk == uid)
                    PledgePointAdjustment.objects.create(
                        committee=committee, pledge=pledge,
                        current_delta=-entry['points'], max_delta=-entry['max_points'],
                        reason=f'{RESET_REASON}reached {entry["points"]}/{entry["max_points"]} in {last}'[:200])
            data.setdefault('edits', []).append({
                'at': _iso(now), 'by': 'New semester',
                'what': (f'{len(todo)} held-back pledge{"" if len(todo) == 1 else "s"} recorded as they stood '
                         f'at the end of {last}{" (points then reset)" if reset else ""}.'),
            })
            archive.data = data
            archive.save(update_fields=['data', 'updated_at'])
    return recorded
