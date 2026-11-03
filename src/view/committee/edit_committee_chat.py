import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, ChatChannel

DEFAULT_CHAT_ICON = '💬'
DEFAULT_CHAT_COLOR = '#003DA5'
#: ChatChannel.icon's max_length.
CHAT_ICON_MAX = 10
_HEX_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')


def can_edit_committee_chat(user, committee):
    """This page's rule: the committee's chairs and admins (not the VP)."""
    return bool(committee.is_chair(user) or user.is_admin)


def committee_chat_appearance_error(icon, color):
    """Why this icon / colour can't be saved, or None."""
    if not isinstance(icon, str) or not icon.strip():
        return 'Choose an emoji for the chat icon.'
    if len(icon) > CHAT_ICON_MAX:
        return f'The chat icon can be at most {CHAT_ICON_MAX} characters.'
    if not isinstance(color, str) or not _HEX_COLOR.match(color):
        return 'Choose a colour like #003DA5.'
    return None


def save_committee_chat_appearance(channel, icon, color):
    """Set the committee chat's icon and colour (already validated)."""
    channel.icon = icon
    channel.color = color
    channel.save()
    return channel


@login_required
def edit_committee_chat_settings(request, code):
    """Allow committee chair to customize their committee chat icon and color"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is chair or admin
    if not can_edit_committee_chat(request.user, committee):
        messages.error(request, 'Only committee chairs can edit chat settings')
        return redirect('committee_home', code=code)

    # Get or create the committee's chat channel
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        messages.error(request, 'Chat channel not found for this committee')
        return redirect('committee_home', code=code)

    if request.method == 'POST':
        icon = request.POST.get('icon', DEFAULT_CHAT_ICON)
        color = request.POST.get('color', DEFAULT_CHAT_COLOR)

        # v4.x: an over-long icon or a non-hex colour used to fail on save.
        error = committee_chat_appearance_error(icon, color)
        if error:
            messages.error(request, error)
            return redirect('edit_committee_chat_settings', code=code)

        save_committee_chat_appearance(channel, icon, color)

        messages.success(request, f'Chat settings updated for {committee.name}!')
        return redirect('committee_chat', code=code)

    # GET: Show form
    return render(request, 'committee/edit_chat_settings.html', {
        'committee': committee,
        'channel': channel
    })
