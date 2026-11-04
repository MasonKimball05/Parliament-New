"""
Admin v2: permissions map (v4.4.4).

Read-only. Who holds what, grouped by permission, every name linking to the
member's admin-v2 page. Sources (each read with one query, so the page costs
the same number of queries for 20 members or 200; see the flat-count test):

* the admin-v2 allowlist (ADMIN_V2_USER_IDS) and ``is_admin``;
* officer roles (``ParliamentUser.roles``; C&B Chair is the ``CNB`` role, which
  is what ``has_cnb_permission`` checks), per role;
* the Officer / Chair / Advisor member types, which are what officer pages,
  ``can_manage_events`` and ``can_manage_groupme`` (``user_is_officer_or_chair``)
  read;
* committee chairs per committee, and explicit committee admins;
* slating managers of elections that aren't archived;
* the per-committee grants: Kai member permissions and live Kai break-glass
  grants, education and recruitment dashboard permissions, and committee
  permissions that go past reading (manage members, take minutes, upload);
* active API tokens.

Flags: an allowlisted or ``is_admin`` member with no sign-in in 60+ days
(``last_seen_at`` or ``last_login``, whichever is later), a chair of an
inactive or archived committee, any of the above held by a pledge or by a
member who isn't Active, and an active committee with no chair (the Executive
Board is left out: its members all have chair-level permission).
"""
from datetime import timedelta

from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from src.models import (
    APIToken, Committee, CommitteePermissions, EducationMemberPermission, KaiBreakGlassGrant,
    KaiMemberPermission, ParliamentUser, RecruitmentMemberPermission, SlatingPeriod,
)
from src.view import admin_v2
from src.view.admin_v2 import require_admin_v2_auth

STALE_DAYS = 60
ELEVATED_TYPES = ('Officer', 'Chair', 'Advisor')

FLAG_STALE = 'stale'
FLAG_PLEDGE = 'pledge'
FLAG_NOT_ACTIVE = 'not_active'
FLAG_INACTIVE_COMMITTEE = 'inactive_committee'
FLAG_NO_CHAIR = 'no_chair'
FLAG_LABELS = {
    FLAG_STALE: 'No sign-in in 60+ days',
    FLAG_PLEDGE: 'Held by a pledge',
    FLAG_NOT_ACTIVE: 'Held by a member who isn\'t Active',
    FLAG_INACTIVE_COMMITTEE: 'Chair of an inactive committee',
    FLAG_NO_CHAIR: 'Committee has no chair',
}

KAI_FLAGS = ('can_view_report_list', 'can_view_report_details', 'can_view_submitter_identity',
             'can_view_accused_identity', 'can_edit_open_cases', 'can_add_activity', 'can_close_cases')
EDUCATION_FLAGS = ('can_view_submissions', 'can_grade_submissions', 'can_manage_tasks', 'can_adjust_time')
RECRUITMENT_FLAGS = ('can_manage_events', 'can_view_private', 'can_take_attendance')
COMMITTEE_FLAGS = ('can_manage_members', 'can_take_minutes', 'can_upload_docs')

_MEMBER_FIELDS = ('user_id', 'name', 'preferred_name', 'username', 'member_type', 'member_status',
                  'is_active', 'is_admin', 'last_login', 'last_seen_at')


def _any(fields):
    q = Q()
    for f in fields:
        q |= Q(**{f: True})
    return q


def _held(row, fields):
    return ', '.join(f.removeprefix('can_').replace('_', ' ') for f in fields if row[f])


def last_sign_in(member):
    times = [t for t in (member.last_seen_at, member.last_login) if t]
    return max(times) if times else None


def _member_flags(member, stale_check=False, now=None):
    flags = []
    if stale_check:
        seen = last_sign_in(member)
        if seen is None or seen < (now or timezone.now()) - timedelta(days=STALE_DAYS):
            flags.append(FLAG_STALE)
    if member.member_type == 'Pledge':
        flags.append(FLAG_PLEDGE)
    if member.member_status != 'Active' or not member.is_active:
        flags.append(FLAG_NOT_ACTIVE)
    return flags


def permissions_map(q='', flagged_only=False):
    """
    ``{'sections': [...], 'flag_counts': {flag: n}, 'unknown_allowlist': [...]}``.
    A section is ``{'key', 'title', 'about', 'groups': [{'label', 'flags', 'entries'}]}``
    and an entry ``{'member', 'detail', 'flags'}``. ``q`` keeps entries whose
    member, detail or group matches; ``flagged_only`` keeps flagged ones.
    """
    now = timezone.now()
    raw = []  # (section key, group label, group flags, user id, detail, extra flags, stale check)

    allow = set(admin_v2.ALLOWED_USER_IDS)
    for uid in sorted(allow):
        raw.append(('allowlist', '', [], uid, '', [], True))
    for uid in ParliamentUser.objects.filter(is_admin=True).values_list('pk', flat=True):
        raw.append(('is_admin', '', [], uid, '', [], True))

    for row in (ParliamentUser.roles.through.objects
                .values('parliamentuser_id', 'role__name', 'role__code', 'role__grants_admin')
                .order_by('role__name')):
        label = row['role__name'] + (' (grants admin)' if row['role__grants_admin'] else '')
        raw.append(('roles', label, [], row['parliamentuser_id'], '', [], False))

    for uid, mtype in (ParliamentUser.objects.filter(member_type__in=ELEVATED_TYPES)
                       .exclude(member_status='Removed').values_list('pk', 'member_type')):
        raw.append(('member_types', mtype, [], uid, '', [], False))

    committees = list(Committee.objects.values('pk', 'name', 'is_active', 'is_archived', 'is_exec_board', 'admin_id')
                      .order_by('name'))
    chairs = {}
    for cid, uid in Committee.chairs.through.objects.values_list('committee_id', 'parliamentuser_id'):
        chairs.setdefault(cid, []).append(uid)
    for c in committees:
        live = c['is_active'] and not c['is_archived']
        label = c['name'].strip() + ('' if live else (' (archived)' if c['is_archived'] else ' (inactive)'))
        group_flags = [FLAG_NO_CHAIR] if live and not c['is_exec_board'] and not chairs.get(c['pk']) else []
        if group_flags:
            raw.append(('chairs', label, group_flags, None, '', [], False))
        for uid in chairs.get(c['pk'], []):
            raw.append(('chairs', label, group_flags, uid, 'Chair', [] if live else [FLAG_INACTIVE_COMMITTEE], False))
        if c['admin_id']:
            raw.append(('chairs', label, group_flags, c['admin_id'], 'Committee admin', [], False))

    for row in (SlatingPeriod.objects.exclude(slating_manager__isnull=True).exclude(status='archived')
                .values('name', 'status', 'slating_manager_id').order_by('name')):
        raw.append(('slating', '', [], row['slating_manager_id'], f'{row["name"]} ({row["status"].replace("_", " ")})',
                    [], False))

    for row in (KaiMemberPermission.objects.filter(_any(KAI_FLAGS))
                .values('user_id', 'committee__name', *KAI_FLAGS)):
        raw.append(('kai', '', [], row['user_id'], f'{row["committee__name"].strip()}: {_held(row, KAI_FLAGS)}', [], False))
    for row in (KaiBreakGlassGrant.objects.filter(revoked_at__isnull=True, expires_at__gt=now)
                .values('user_id', 'expires_at')):
        until = timezone.localtime(row['expires_at']).strftime('%b %-d %-I:%M %p')
        raw.append(('kai', '', [], row['user_id'], f'Break-glass access until {until}', [], False))

    for key, model, fields in (('education', EducationMemberPermission, EDUCATION_FLAGS),
                               ('recruitment', RecruitmentMemberPermission, RECRUITMENT_FLAGS),
                               ('committee_perms', CommitteePermissions, COMMITTEE_FLAGS)):
        for row in model.objects.filter(_any(fields)).values('user_id', 'committee__name', *fields):
            raw.append((key, '', [], row['user_id'], f'{row["committee__name"].strip()}: {_held(row, fields)}',
                        [], False))

    for row in (APIToken.objects.filter(status=APIToken.STATUS_ACTIVE)
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                .values('user_id', 'name', 'scopes', 'last_used_at').order_by('name')):
        scopes = ', '.join(row['scopes'] or []) or 'no scopes'
        used = (timezone.localtime(row['last_used_at']).strftime('last used %b %-d, %Y')
                if row['last_used_at'] else 'never used')
        raw.append(('api_tokens', '', [], row['user_id'], f'{row["name"]} ({scopes}; {used})', [], False))

    ids = {r[3] for r in raw if r[3]}
    members = {m.pk: m for m in ParliamentUser.objects.filter(pk__in=ids).only(*_MEMBER_FIELDS)}

    sections = {key: {'key': key, 'title': title, 'about': about, 'groups': {}} for key, title, about in SECTIONS}
    unknown_allowlist = []
    flag_counts = {f: 0 for f in FLAG_LABELS}
    needle = q.strip().lower()
    counted_groups = set()

    for key, label, group_flags, uid, detail, extra, stale_check in raw:
        member = members.get(uid) if uid else None
        if uid and member is None:
            if key == 'allowlist':
                unknown_allowlist.append(uid)
            continue
        if group_flags and (key, label) not in counted_groups:
            counted_groups.add((key, label))
            for f in group_flags:
                flag_counts[f] += 1
        flags = (_member_flags(member, stale_check, now) if member else []) + extra
        for f in flags:
            flag_counts[f] += 1
        if member is not None and needle and not any(needle in s.lower() for s in (
                member.get_display_name(), member.name, member.username, member.user_id, detail, label)):
            continue
        if member is None and needle and needle not in label.lower():
            continue
        if flagged_only and not (flags or group_flags):
            continue
        group = sections[key]['groups'].setdefault(label, {'label': label, 'flags': group_flags, 'entries': []})
        if member is not None:
            group['entries'].append({'member': member, 'detail': detail, 'flags': flags,
                                     'last_sign_in': last_sign_in(member) if stale_check else None})

    out = []
    for key, _t, _a in SECTIONS:
        s = sections[key]
        groups = list(s['groups'].values())
        for g in groups:
            g['entries'].sort(key=lambda e: e['member'].name.lower())
            g['flag_labels'] = [FLAG_LABELS[f] for f in g['flags']]
            for e in g['entries']:
                e['flag_labels'] = [FLAG_LABELS[f] for f in e['flags']]
        s['groups'] = groups
        s['n'] = sum(len(g['entries']) for g in groups)
        out.append(s)
    return {
        'sections': out,
        'flag_counts': [{'key': f, 'label': FLAG_LABELS[f], 'n': n} for f, n in flag_counts.items()],
        'total_flags': sum(flag_counts.values()),
        'unknown_allowlist': unknown_allowlist,
    }


SECTIONS = (
    ('allowlist', 'Admin v2 allowlist', 'ADMIN_V2_USER_IDS: can open this portal (with the admin-v2 sign-in).'),
    ('is_admin', 'Site admins', 'is_admin: Django admin, officer pages and every admin-only action on the site.'),
    ('roles', 'Officer roles', 'Exec roles. A role that grants admin makes its holder an admin at the next officer sync; C&B Chair runs the bylaws builder.'),
    ('member_types', 'Officer, Chair and Advisor types', 'Officer pages; Officers and Chairs also manage events and the GroupMe bot.'),
    ('chairs', 'Committee chairs', 'Chair-level permission on each committee, and explicit committee admins.'),
    ('slating', 'Slating managers', 'Full setup access to an election that isn\'t archived.'),
    ('kai', 'Kai access', 'Kai committee member permissions and live break-glass grants.'),
    ('education', 'Education dashboard', 'Education committee member permissions.'),
    ('recruitment', 'Recruitment dashboard', 'Recruitment committee member permissions.'),
    ('committee_perms', 'Committee permissions', 'Committee grants past reading: manage members, take minutes, upload documents.'),
    ('api_tokens', 'API tokens', 'Active, unexpired REST API tokens.'),
)


@require_admin_v2_auth
def admin_v2_permissions(request):
    q = (request.GET.get('q') or '')[:100]
    flagged = request.GET.get('flagged') == '1'
    data = permissions_map(q, flagged)
    data.update({'q': q, 'flagged': flagged, 'stale_days': STALE_DAYS})
    return render(request, 'admin_v2/permissions.html', data)
