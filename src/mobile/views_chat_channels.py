"""
Custom chat channels in the app (batch 6): the website's admin pages
``create_channel`` (/chats/create/), ``edit_channel`` (/chats/<id>/edit/) and
``delete_channel`` (/chats/<id>/delete/), saved through the same helpers
(src/view/chat/create_channel.py), so the channel and its permission rows are
exactly what the pages make.

* ``GET  chat/channels/manage/`` — every custom channel, deactivated ones too
  (the chat list only shows active channels, so this is where an admin turns
  one back on), with who can see each.
* ``GET  chat/channels/new/`` — the form's choices: active members, member
  types. ``POST`` ``{name, description, icon, color, access_type, users,
  member_types, chairs_only, officers_only, alumni_only}`` creates one. 201.
* ``GET  chat/channels/<id>/settings/`` — the edit page: the channel, its
  current access choices and the form's choices. ``POST`` the create body plus
  ``{is_active, is_read_only}`` saves it; the permission rows are replaced, as
  on the page. A field left out keeps its current value.
* ``POST chat/channels/<id>/settings/delete/`` — deletes the channel and all
  of its messages.

Admins only (``@admin_required``), behind the ``chats`` flag. Committee
channels can't be edited or deleted here (the pages refuse them too): a 400
``not_custom``. The pages have no page toggle, so only the pledge restriction
applies (an admin is never a pledge in practice).

The app validates what the page's form constrains in the browser: a name,
``open`` or ``restricted``, a ``#rrggbb`` colour, an icon of at most 10
characters, member types from the list and people from the active members.
"""
import re

from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import ChatChannel, ChatChannelPermission
from src.view.chat.create_channel import (
    DEFAULT_COLOR, DEFAULT_ICON, channel_access_state, channel_form_users, create_custom_channel,
    delete_custom_channel, update_custom_channel,
)

NAME_MAX = ChatChannel._meta.get_field('name').max_length
ICON_MAX = ChatChannel._meta.get_field('icon').max_length
ACCESS_TYPES = ('open', 'restricted')
_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _flag(value):
    return value is True or value in ('on', 'true', '1', 1)


def _strings(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, '')]
    return [str(value)] if value else []


class _ChannelAdmins(MobileAPIView):
    web_page_toggle = False   # the pages have no @require_page_enabled

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_admin:
            raise exceptions.PermissionDenied('Admins only.', code='forbidden')
        if not check_feature_enabled('chats'):
            raise exceptions.PermissionDenied('Chat is off.', code='feature_disabled')

    def custom_channel(self, channel_id, verb):
        channel = get_object_or_404(ChatChannel.objects.select_related('created_by'), pk=channel_id)
        if channel.channel_type != 'custom':
            # The pages' refusal ("Cannot edit committee channels").
            raise BadRequest(f'Cannot {verb} committee channels', code='not_custom')
        return channel


def _choices():
    users = channel_form_users().only('user_id', 'name')
    return {
        'users': [{'user_id': u.user_id, 'name': u.name} for u in users],
        'member_types': [{'value': k, 'label': v} for k, v in ChatChannelPermission.MEMBER_TYPES],
    }


def _channel_json(channel):
    return {
        'id': channel.pk,
        'name': channel.name,
        'description': channel.description,
        'icon': channel.icon,
        'color': channel.color,
        'access_type': channel.access_type,
        'access_label': channel.get_access_type_display(),
        'is_active': channel.is_active,
        'is_read_only': channel.is_read_only,
        'created_by': channel.created_by.get_display_name() if channel.created_by else None,
        'created_at': channel.created_at.isoformat() if channel.created_at else None,
    }


def _access_json(channel, offered_ids):
    state = channel_access_state(channel)
    return {
        # Only people the form offers, as the page's checkboxes: a grant to
        # someone no longer active is dropped when the page is saved, and here.
        'users': [uid for uid in state['current_user_permissions'] if uid in offered_ids],
        'member_types': state['current_member_type_permissions'],
        'chairs_only': state['current_chairs_only'],
        'officers_only': state['current_officers_only'],
        'alumni_only': state['current_alumni_only'],
    }


def _clean(data, channel=None):
    """The form's fields, checked; for an edit, a field left out keeps its value."""
    def pick(key, current):
        return data[key] if key in data else current

    name = str(pick('name', channel.name if channel else '') or '').strip()
    if not name:
        raise BadRequest('Channel name is required', code='invalid_input')
    if len(name) > NAME_MAX:
        raise BadRequest(f'Keep the name under {NAME_MAX} characters.', code='invalid_input')

    access_type = str(pick('access_type', channel.access_type if channel else 'restricted') or '')
    if access_type not in ACCESS_TYPES:
        raise BadRequest('Choose who can access the channel.', code='invalid_input')

    icon = str(pick('icon', channel.icon if channel else DEFAULT_ICON) or '').strip() or DEFAULT_ICON
    if len(icon) > ICON_MAX:
        raise BadRequest('Use a single emoji for the icon.', code='invalid_input')

    color = str(pick('color', channel.color if channel else DEFAULT_COLOR) or '').strip() or DEFAULT_COLOR
    if not _COLOR.match(color):
        raise BadRequest('Choose a colour like #003DA5.', code='invalid_input')

    if channel is not None:
        offered = set(channel_form_users().values_list('pk', flat=True))
        current = _access_json(channel, offered)
    else:
        current = {'users': [], 'member_types': [], 'chairs_only': False,
                   'officers_only': False, 'alumni_only': False}
    user_ids = list(dict.fromkeys(_strings(pick('users', current['users']))))
    member_types = list(dict.fromkeys(_strings(pick('member_types', current['member_types']))))
    valid_types = {k for k, _ in ChatChannelPermission.MEMBER_TYPES}
    if any(mt not in valid_types for mt in member_types):
        raise BadRequest('Choose member types from the list.', code='invalid_input')
    if user_ids:
        found = set(channel_form_users().filter(pk__in=user_ids).values_list('pk', flat=True))
        if len(found) != len(user_ids):
            raise BadRequest('Choose people from the active members.', code='invalid_input')

    cleaned = {
        'name': name,
        'description': str(pick('description', channel.description if channel else '') or ''),
        'access_type': access_type,
        'icon': icon,
        'color': color,
        'user_ids': user_ids,
        'member_types': member_types,
        'chairs_only': _flag(pick('chairs_only', current['chairs_only'])),
        'officers_only': _flag(pick('officers_only', current['officers_only'])),
        'alumni_only': _flag(pick('alumni_only', current['alumni_only'])),
    }
    if channel is not None:
        cleaned['is_active'] = _flag(pick('is_active', channel.is_active))
        cleaned['is_read_only'] = _flag(pick('is_read_only', channel.is_read_only))
    return cleaned


class ChannelManageListView(_ChannelAdmins):
    web_page = 'chat_index'   # where the admin controls for custom channels live

    def get(self, request):
        channels = (ChatChannel.objects.filter(channel_type='custom')
                    .select_related('created_by').order_by('-is_active', 'name'))
        return Response({'channels': [_channel_json(c) for c in channels]})


class ChannelCreateView(_ChannelAdmins):
    web_page = 'create_channel'

    def get(self, request):
        return Response(_choices())

    def post(self, request):
        cleaned = _clean(_body(request))
        channel = create_custom_channel(request.user, **cleaned)
        return Response({'detail': f'Channel "{channel.name}" created successfully!',
                         'channel': _channel_json(channel)}, status=201)


class ChannelSettingsView(_ChannelAdmins):
    web_page = 'edit_channel'

    def get(self, request, channel_id):
        channel = self.custom_channel(channel_id, 'edit')
        choices = _choices()
        offered = {u['user_id'] for u in choices['users']}
        return Response({
            'channel': _channel_json(channel),
            'access': _access_json(channel, offered),
            'message_count': channel.messages.count(),
            **choices,
        })

    def post(self, request, channel_id):
        channel = self.custom_channel(channel_id, 'edit')
        cleaned = _clean(_body(request), channel)
        update_custom_channel(channel, **cleaned)
        return Response({'detail': f'Channel "{channel.name}" updated successfully!',
                         'channel': _channel_json(channel)})


class ChannelDeleteView(_ChannelAdmins):
    web_page = 'delete_channel'

    def post(self, request, channel_id):
        channel = self.custom_channel(channel_id, 'delete')
        name = delete_custom_channel(channel)
        return Response({'detail': f'Channel "{name}" deleted successfully!'})
