"""
A committee's settings in the app (batch 6): the website's guest chat
permissions, chat appearance and recruitment permissions pages, and the
officers' Manage Committees page with its create, edit and delete actions.

* ``GET committees/<code>/chat-permissions/`` — the committee chat's guests
  and who may be added (``manage_chat_permissions``: chairs, the VP, admins).
* ``POST committees/<code>/chat-permissions/`` ``{user_ids, can_read,
  can_write, can_delete, can_edit, expires_at?}`` — add guests
  (``bulk_add_guest_permissions``; members and unknown ids are skipped).
* ``POST committees/<code>/chat-permissions/<user_id>/`` — change one guest's
  switches and expiry (``update_guest_permission``).
* ``POST committees/<code>/chat-permissions/remove/`` ``{user_ids}`` — remove
  guests (``bulk_remove_guest_permissions``).
* ``GET|POST committees/<code>/chat-settings/`` ``{icon, color}`` — the
  committee chat's emoji and colour (``edit_committee_chat_settings``: chairs
  and admins; not the VP, as on the website).
* ``GET|POST committees/<code>/recruitment-permissions/`` ``{user_id, flags}``
  and ``POST .../reset/`` — what each recruitment committee member may do
  (``manage_recruitment_permissions``: chairs and admins, under the
  ``committee_home`` page toggle like the website's decorator).
* ``GET officer/committees/`` — every committee with its counts
  (``manage_committees``: officers, chairs and admins, ``officer_required``).
* ``GET|POST officer/committees/new/`` — the create form's choices; create one
  (``create_committee``: admins).
* ``GET|POST officer/committees/<id>/`` — one committee's edit form; save it,
  or archive / restore it with ``{is_archived}`` (``committee_detail_api``).
* ``POST officer/committees/<id>/delete/`` — delete one that has no
  legislation, documents or minutes (``delete_committee``).

Every rule, the work and its logs are the web views', through the helpers in
src/view/committee/manage_chat_permissions.py, edit_committee_chat.py,
recruitment.py and committee_index.py. The committee pages need the
``committee_system`` flag only where the web view has it (the four Manage
Committees endpoints). None of these web pages has ``@require_page_enabled``
except the recruitment one (``committee_home``).
"""
from django.http import Http404
from django.utils.dateparse import parse_date
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.forms import CommitteeCreateForm
from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _person
from src.models import Committee, ChatChannelPermission, ParliamentUser
from src.models.users import member_defer
from src.models_feature_flags import PageToggle
from src.permissions import user_is_officer_or_chair
from src.view.committee.committee_index import (
    apply_committee_changes, committee_create_choices, committee_detail_json, delete_committee_if_unused,
    managed_committee_rows, save_new_committee,
)
from src.view.committee.edit_committee_chat import (
    CHAT_ICON_MAX, DEFAULT_CHAT_COLOR, DEFAULT_CHAT_ICON, can_edit_committee_chat, committee_chat_appearance_error,
    save_committee_chat_appearance,
)
from src.view.committee.manage_chat_permissions import (
    GUEST_FLAGS, bulk_save_guest_permissions, can_manage_chat_permissions, chat_guest_candidates,
    chat_guest_permissions, committee_chat_channel, parse_guest_expiry, remove_guest_permissions,
    update_guest_permission_row,
)
from src.view.committee.recruitment import (
    RECRUIT_PERM_FIELDS, can_manage_recruitment_permissions, recruitment_permission_rows,
    reset_recruitment_permission_rows, set_recruitment_permission,
)

CANDIDATE_LIMIT = 1000
TEXT_MAX = 225   # Committee.name

#: The website's colour swatches (edit_chat_settings.html).
COLOR_PRESETS = [
    {'value': '#003DA5', 'label': 'Beta Blue'},
    {'value': '#CD8C95', 'label': 'Beta Pink'},
    {'value': '#EF4444', 'label': 'Red'},
    {'value': '#F59E0B', 'label': 'Orange'},
    {'value': '#10B981', 'label': 'Green'},
    {'value': '#3B82F6', 'label': 'Blue'},
    {'value': '#8B5CF6', 'label': 'Purple'},
    {'value': '#EC4899', 'label': 'Pink'},
]

RECRUIT_LABELS = {
    'can_manage_events': 'Manage events',
    'can_view_private': 'View private info',
    'can_take_attendance': 'Take attendance',
}


class BadRequest(exceptions.APIException):
    status_code = 400


def _refuse(message):
    raise exceptions.PermissionDenied(message, code='no_access')


def _data(request):
    return request.data if isinstance(request.data, dict) else {}


def _committee(code, **extra):
    committee = Committee.objects.select_related('role').filter(code=code, **extra).first()
    if committee is None:
        raise Http404
    return committee


def _committee_json(committee):
    return {'code': committee.code, 'name': committee.name}


def _bool(data, key, default=False):
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise BadRequest(f'Send {key} as true or false.', code='invalid_input')
    return value


def _user_ids(data):
    ids = data.get('user_ids')
    if (not isinstance(ids, list) or not ids or len(ids) > CANDIDATE_LIMIT
            or not all(isinstance(i, str) and i for i in ids)):
        raise BadRequest('Choose at least one person.', code='invalid_input')
    return ids


# ── Guest chat permissions ──────────────────────────────────────────────────

def _guest_json(perm):
    return {
        **_person(perm.user),
        'member_type': perm.user.member_type,
        'member_status': perm.user.member_status,
        **{f: getattr(perm, f) for f in GUEST_FLAGS},
        'expires_at': perm.expires_at.isoformat() if perm.expires_at else None,
        'is_expired': perm.is_expired,
    }


class _ChatPermissionsView(MobileAPIView):
    web_page_toggle = False   # the pages have only @login_required

    def setup_for(self, request, code):
        committee = _committee(code)
        if not can_manage_chat_permissions(request.user, committee):
            _refuse('Only committee chairs and admins can manage chat permissions.')
        channel = committee_chat_channel(committee)
        if channel is None:
            raise exceptions.NotFound('Chat channel not found for this committee.')
        return committee, channel

    def flags_and_expiry(self, data):
        flags = {f: _bool(data, f) for f in GUEST_FLAGS}
        if not flags['can_read'] and not flags['can_write']:
            raise BadRequest('Guests need at least read or write permission.', code='invalid_input')
        raw = data.get('expires_at') or ''
        if not isinstance(raw, str):
            raise BadRequest('Send expires_at as an ISO date and time.', code='invalid_input')
        expires_at, error = parse_guest_expiry(raw)
        if error:
            raise BadRequest(error + '.', code='invalid_input')
        return flags, expires_at

    def payload(self, committee, channel):
        permissions = list(chat_guest_permissions(committee, channel))
        added = {p.user.user_id for p in permissions}
        candidates = [{**_person(u), 'member_type': u.member_type, 'member_status': u.member_status}
                      for u in chat_guest_candidates(committee)[:CANDIDATE_LIMIT] if u.user_id not in added]
        return {
            'committee': _committee_json(committee),
            'channel': {'id': channel.id, 'name': channel.name, 'icon': channel.icon, 'color': channel.color},
            'guests': [_guest_json(p) for p in permissions],
            'candidates': candidates,
        }


class CommitteeChatPermissionsView(_ChatPermissionsView):
    web_page = 'manage_chat_permissions'

    def get(self, request, code):
        return Response(self.payload(*self.setup_for(request, code)))

    def post(self, request, code):
        committee, channel = self.setup_for(request, code)
        data = _data(request)
        user_ids = _user_ids(data)
        flags, expires_at = self.flags_and_expiry(data)
        added, skipped = bulk_save_guest_permissions(committee, channel, user_ids, flags, expires_at)
        return Response({'added': added, 'skipped': skipped, **self.payload(committee, channel)})


class CommitteeChatGuestView(_ChatPermissionsView):
    web_page = 'update_guest_permission'

    def post(self, request, code, user_id):
        committee, channel = self.setup_for(request, code)
        perm = (ChatChannelPermission.objects.select_related('user')
                .filter(channel=channel, user__user_id=user_id).first())
        if perm is None:
            raise exceptions.NotFound('Permission not found.')
        flags, expires_at = self.flags_and_expiry(_data(request))
        update_guest_permission_row(perm, flags, expires_at)
        return Response(self.payload(committee, channel))


class CommitteeChatGuestRemoveView(_ChatPermissionsView):
    web_page = 'bulk_remove_guest_permissions'

    def post(self, request, code):
        committee, channel = self.setup_for(request, code)
        removed = remove_guest_permissions(channel, _user_ids(_data(request)))
        return Response({'removed': removed, **self.payload(committee, channel)})


# ── Chat appearance ─────────────────────────────────────────────────────────

class CommitteeChatSettingsView(MobileAPIView):
    web_page = 'edit_committee_chat_settings'
    web_page_toggle = False

    def setup_for(self, request, code):
        committee = _committee(code)
        if not can_edit_committee_chat(request.user, committee):
            _refuse('Only committee chairs can edit chat settings.')
        channel = committee_chat_channel(committee)
        if channel is None:
            raise exceptions.NotFound('Chat channel not found for this committee.')
        return committee, channel

    def payload(self, committee, channel):
        return {
            'committee': _committee_json(committee),
            'channel': {'id': channel.id, 'name': channel.name, 'icon': channel.icon, 'color': channel.color},
            'presets': COLOR_PRESETS,
            'icon_max': CHAT_ICON_MAX,
        }

    def get(self, request, code):
        return Response(self.payload(*self.setup_for(request, code)))

    def post(self, request, code):
        committee, channel = self.setup_for(request, code)
        data = _data(request)
        icon = data.get('icon', DEFAULT_CHAT_ICON)
        color = data.get('color', DEFAULT_CHAT_COLOR)
        error = committee_chat_appearance_error(icon, color)
        if error:
            raise BadRequest(error, code='invalid_input')
        save_committee_chat_appearance(channel, icon, color)
        return Response(self.payload(committee, channel))


# ── Recruitment permissions ─────────────────────────────────────────────────

class _RecruitmentPermissionsView(MobileAPIView):
    web_page_toggle = False   # its toggle is committee_home's, checked below

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # @require_page_enabled('committee_home'), as on the website.
        if not PageToggle.is_page_enabled('committee_home'):
            raise exceptions.PermissionDenied('This page is turned off.', code='page_disabled')

    def setup_for(self, request, code):
        committee = _committee(code, is_recruitment_committee=True)
        if not can_manage_recruitment_permissions(request.user, committee):
            _refuse('Only recruitment chairs can manage permissions.')
        return committee

    def payload(self, committee):
        return {
            'committee': _committee_json(committee),
            'fields': [{'field': f, 'label': RECRUIT_LABELS.get(f, f)} for f in RECRUIT_PERM_FIELDS],
            'rows': [{**_person(row['member']), 'is_chair': row['is_chair'],
                      'flags': {f: bool(row[f]) for f in RECRUIT_PERM_FIELDS}}
                     for row in recruitment_permission_rows(committee)],
        }


class RecruitmentPermissionsView(_RecruitmentPermissionsView):
    web_page = 'manage_recruitment_permissions'

    def get(self, request, code):
        return Response(self.payload(self.setup_for(request, code)))

    def post(self, request, code):
        committee = self.setup_for(request, code)
        data = _data(request)
        user_id = data.get('user_id')
        member = (ParliamentUser.objects.filter(pk=user_id).first()
                  if isinstance(user_id, str) and user_id else None)
        if member is None:
            raise BadRequest('User not found.', code='invalid_input')
        if committee.is_chair(member):
            raise BadRequest('Chairs always have full access.', code='invalid_input')
        flags = data.get('flags')
        fields = set(RECRUIT_PERM_FIELDS)
        if (not isinstance(flags, dict) or not flags or set(flags) - fields
                or not all(isinstance(v, bool) for v in flags.values())):
            raise BadRequest(f'Send flags as {{field: true/false}} for {", ".join(RECRUIT_PERM_FIELDS)}.',
                             code='invalid_input')
        # The web form sends all three; a field the app leaves out keeps its value.
        current = next((row for row in recruitment_permission_rows(committee) if row['member'].pk == member.pk),
                       None)
        merged = {f: flags.get(f, bool(current and current[f])) for f in RECRUIT_PERM_FIELDS}
        set_recruitment_permission(committee, member, merged, request.user)
        return Response(self.payload(committee))


class RecruitmentPermissionsResetView(_RecruitmentPermissionsView):
    web_page = 'reset_recruitment_permissions'

    def post(self, request, code):
        committee = self.setup_for(request, code)
        deleted = reset_recruitment_permission_rows(committee)
        return Response({'deleted': deleted, **self.payload(committee)})


# ── Manage Committees (officers) ────────────────────────────────────────────

def _require_committee_system():
    if not check_feature_enabled('committee_system'):
        raise exceptions.PermissionDenied('Committees are off.', code='feature_disabled')


def _managed_row(row):
    c = row['committee']
    return {
        'id': c.id,
        'name': c.name,
        'code': c.code,
        'role': c.role.name if c.role else None,
        'member_count': row['member_count'],
        'chair_count': row['chair_count'],
        'advisor_count': row['advisor_count'],
        'is_ad_hoc': c.is_ad_hoc,
        'ad_hoc_expiration': c.ad_hoc_expiration.isoformat() if c.ad_hoc_expiration else None,
        'is_active': c.is_active,
        'is_archived': c.is_archived,
    }


def _roles():
    roles, _ = committee_create_choices()
    return [{'id': r.id, 'name': r.name} for r in roles]


class _OfficerCommitteesView(MobileAPIView):
    web_page_toggle = False   # @officer_required + @require_feature_flag only

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_is_officer_or_chair(request.user):
            _refuse('Officers and chairs only.')
        _require_committee_system()


class ManageCommitteesView(_OfficerCommitteesView):
    web_page = 'manage_committees'

    def get(self, request):
        rows = [_managed_row(r) for r in managed_committee_rows()]
        return Response({
            'committees': rows,
            'total': len(rows),
            # The page's "Add Committee" posts to create_committee, which is admins only.
            'can_create': bool(request.user.is_admin),
            'roles': _roles(),
        })


class CommitteeCreateView(MobileAPIView):
    web_page = 'create_committee'
    web_page_toggle = False   # @admin_required + @require_feature_flag only

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_admin:
            _refuse('Admins only.')
        _require_committee_system()

    def get(self, request):
        _, members = committee_create_choices()
        return Response({
            'roles': _roles(),
            'members': [_person(m) for m in members.defer(*member_defer())[:CANDIDATE_LIMIT]],
        })

    def post(self, request):
        data = _data(request)
        name, code = data.get('name'), data.get('code')
        if not isinstance(name, str) or not name.strip() or len(name) > TEXT_MAX:
            raise BadRequest(f'Give the committee a name (up to {TEXT_MAX} characters).', code='invalid_input')
        if not isinstance(code, str) or not code.strip() or len(code) > 255:
            raise BadRequest('Give the committee a code, like FINANCE.', code='invalid_input')
        role_id = data.get('role_id')
        if role_id is not None and (isinstance(role_id, bool) or not isinstance(role_id, int)):
            raise BadRequest('Send role_id as a number or null.', code='invalid_input')
        form_data = {
            'name': name.strip(),
            'code': code,
            'role': role_id or '',
            'is_ad_hoc': _bool(data, 'is_ad_hoc'),
            'is_active': _bool(data, 'is_active', True),
        }
        for key, field in (('member_ids', 'initial_members'), ('chair_ids', 'initial_chairs')):
            ids = data.get(key, [])
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise BadRequest(f'Send {key} as a list of user ids.', code='invalid_input')
            form_data[field] = ids
        form = CommitteeCreateForm(form_data)
        if not form.is_valid():
            message = next(iter(e for errors in form.errors.values() for e in errors), 'Check the form.')
            raise BadRequest(str(message), code='invalid_input')
        committee = save_new_committee(form, request.user)
        return Response({'committee': committee_detail_json(committee)}, status=201)


class CommitteeEditView(_OfficerCommitteesView):
    web_page = 'committee_detail_api'

    def committee(self, committee_id):
        committee = Committee.objects.select_related('role').filter(id=committee_id).first()
        if committee is None:
            raise Http404
        return committee

    def get(self, request, committee_id):
        return Response({'committee': committee_detail_json(self.committee(committee_id)), 'roles': _roles()})

    def post(self, request, committee_id):
        committee = self.committee(committee_id)
        data = _data(request)
        # The page's edit form sends these seven; members and chairs are the
        # committee's own Manage Members page (native already).
        allowed = {'name', 'code', 'role_id', 'is_ad_hoc', 'ad_hoc_expiration', 'is_active', 'is_archived'}
        changes = {k: v for k, v in data.items() if k in allowed}
        if not changes:
            raise BadRequest('Nothing to change.', code='invalid_input')
        if 'name' in changes and (not isinstance(changes['name'], str) or not changes['name'].strip()
                                  or len(changes['name']) > TEXT_MAX):
            raise BadRequest(f'Give the committee a name (up to {TEXT_MAX} characters).', code='invalid_input')
        if 'code' in changes and (not isinstance(changes['code'], str) or not changes['code'].strip()
                                  or len(changes['code']) > 255):
            raise BadRequest('Give the committee a code, like FINANCE.', code='invalid_input')
        role_id = changes.get('role_id')
        if role_id is not None and (isinstance(role_id, bool) or not isinstance(role_id, int)):
            raise BadRequest('Send role_id as a number or null.', code='invalid_input')
        for key in ('is_ad_hoc', 'is_active', 'is_archived'):
            if key in changes:
                _bool(changes, key)
        expiration = changes.get('ad_hoc_expiration')
        if expiration not in (None, ''):
            if not isinstance(expiration, str) or parse_date(expiration) is None:
                raise BadRequest('Send ad_hoc_expiration as YYYY-MM-DD.', code='invalid_input')
        changed, error = apply_committee_changes(committee, changes, request.user, request._request)
        if error:
            raise BadRequest(error, code='invalid_input')
        committee.refresh_from_db()
        return Response({'changes': changed, 'committee': committee_detail_json(committee)})


class CommitteeDeleteView(_OfficerCommitteesView):
    web_page = 'delete_committee'

    def post(self, request, committee_id):
        committee = Committee.objects.filter(id=committee_id).first()
        if committee is None:
            raise Http404
        name = committee.name
        error = delete_committee_if_unused(committee, request.user, request._request)
        if error:
            raise BadRequest(error, code='invalid_input')
        return Response({'ok': True, 'name': name})
