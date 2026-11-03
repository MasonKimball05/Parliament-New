"""
Custom chat channels: the admin create / edit / delete pages.

The saving is in helpers (``create_custom_channel``, ``update_custom_channel``,
``delete_custom_channel``, ``set_channel_access``, ``channel_form_context``)
so the app (src/mobile/views_chat_channels.py) makes exactly the same rows.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from src.decorators import admin_required
from src.models import ChatChannel, ChatChannelPermission, ParliamentUser
from src.feature_flag_decorators import require_feature_flag

DEFAULT_ICON = '💬'
DEFAULT_COLOR = '#003DA5'


def set_channel_access(channel, *, user_ids=(), member_types=(), chairs_only=False,
                       officers_only=False, alumni_only=False):
    """
    Replace a channel's permission rows with the form's choices. Only a
    ``restricted`` channel gets any; an ``open`` one is left with none. Every
    row is read + write, as the edit page has always saved them.
    """
    ChatChannelPermission.objects.filter(channel=channel).delete()
    if channel.access_type != 'restricted':
        return
    rows = [ChatChannelPermission(channel=channel, user_id=uid, can_read=True, can_write=True)
            for uid in user_ids if uid]
    rows += [ChatChannelPermission(channel=channel, member_type=mt, can_read=True, can_write=True)
             for mt in member_types if mt]
    for flag, on in (('chairs_only', chairs_only), ('officers_only', officers_only),
                     ('alumni_only', alumni_only)):
        if on:
            rows.append(ChatChannelPermission(channel=channel, can_read=True, can_write=True,
                                              **{flag: True}))
    for row in rows:
        row.save()


def create_custom_channel(actor, *, name, description='', access_type='restricted',
                          icon=DEFAULT_ICON, color=DEFAULT_COLOR, **access):
    """A new custom channel created by ``actor``, with its access rows."""
    channel = ChatChannel.objects.create(
        name=name,
        description=description,
        channel_type='custom',
        access_type=access_type,
        created_by=actor,
        icon=icon,
        color=color,
    )
    set_channel_access(channel, **access)
    return channel


def update_custom_channel(channel, *, name, description='', access_type='restricted',
                          icon=DEFAULT_ICON, color=DEFAULT_COLOR, is_active=False,
                          is_read_only=False, **access):
    """Save the edit page's fields and replace the channel's access rows."""
    channel.name = name
    channel.description = description
    channel.access_type = access_type
    channel.icon = icon
    channel.color = color
    channel.is_active = is_active
    channel.is_read_only = is_read_only
    channel.save()
    set_channel_access(channel, **access)
    return channel


def delete_custom_channel(channel):
    """Delete the channel (its messages and permission rows go with it)."""
    name = channel.name
    channel.delete()
    return name


def channel_form_users():
    """The people the form offers: active members, by name."""
    return ParliamentUser.objects.filter(member_status='Active').order_by('name')


def channel_access_state(channel):
    """The edit page's current choices for ``channel``."""
    perms = channel.permissions
    return {
        'current_user_permissions': list(perms.filter(user__isnull=False).values_list('user_id', flat=True)),
        'current_member_type_permissions': list(
            perms.filter(member_type__isnull=False).values_list('member_type', flat=True)),
        'current_chairs_only': perms.filter(chairs_only=True).exists(),
        'current_officers_only': perms.filter(officers_only=True).exists(),
        'current_alumni_only': perms.filter(alumni_only=True).exists(),
    }


def _posted_access(post):
    return {
        'user_ids': post.getlist('users'),
        'member_types': post.getlist('member_types'),
        'chairs_only': bool(post.get('chairs_only')),
        'officers_only': bool(post.get('officers_only')),
        'alumni_only': bool(post.get('alumni_only')),
    }


@admin_required
@require_feature_flag('chats')
def create_channel(request):
    """Admin-only: Create custom chat channel"""
    if request.method == 'POST':
        name = request.POST.get('name')

        if not name:
            messages.error(request, 'Channel name is required')
            return redirect('create_channel')

        create_custom_channel(
            request.user,
            name=name,
            description=request.POST.get('description', ''),
            access_type=request.POST.get('access_type', 'restricted'),
            icon=request.POST.get('icon', DEFAULT_ICON),
            color=request.POST.get('color', DEFAULT_COLOR),
            **_posted_access(request.POST),
        )

        messages.success(request, f'Channel "{name}" created successfully!')
        return redirect('chat_index')

    # GET: Show form
    return render(request, 'chat/create_channel.html', {
        'all_users': channel_form_users(),
        'member_types': ChatChannelPermission.MEMBER_TYPES
    })


@admin_required
@require_feature_flag('chats')
def edit_channel(request, channel_id):
    """Admin-only: Edit existing custom channel"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

    # Only allow editing custom channels
    if channel.channel_type != 'custom':
        messages.error(request, 'Cannot edit committee channels')
        return redirect('chat_index')

    if request.method == 'POST':
        update_custom_channel(
            channel,
            name=request.POST.get('name', channel.name),
            description=request.POST.get('description', ''),
            access_type=request.POST.get('access_type', 'restricted'),
            icon=request.POST.get('icon', DEFAULT_ICON),
            color=request.POST.get('color', DEFAULT_COLOR),
            is_active=request.POST.get('is_active') == 'true',
            is_read_only=request.POST.get('is_read_only') == 'true',
            **_posted_access(request.POST),
        )

        messages.success(request, f'Channel "{channel.name}" updated successfully!')
        return redirect('edit_channel', channel_id=channel.id)

    # GET: Show form
    return render(request, 'chat/edit_channel.html', {
        'channel': channel,
        'all_users': channel_form_users(),
        'member_types': ChatChannelPermission.MEMBER_TYPES,
        **channel_access_state(channel),
    })


@admin_required
@require_feature_flag('chats')
def delete_channel(request, channel_id):
    """Admin-only: Delete custom channel"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

    # Only allow deleting custom channels
    if channel.channel_type != 'custom':
        messages.error(request, 'Cannot delete committee channels')
        return redirect('chat_index')

    if request.method == 'POST':
        channel_name = delete_custom_channel(channel)
        messages.success(request, f'Channel "{channel_name}" deleted successfully!')
        return redirect('chat_index')

    return render(request, 'chat/delete_channel.html', {
        'channel': channel
    })
