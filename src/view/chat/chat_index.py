from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import ChatChannel


@login_required
def chat_index(request):
    """Show all accessible chat channels with unread counts"""
    user = request.user

    # Check if admin wants to view all channels
    view_all = request.GET.get('view_all') == 'true' and user.is_admin

    # Get all channels user has access to
    accessible_channels = []

    # Get all active channels
    all_channels = ChatChannel.objects.filter(is_active=True).select_related('committee')

    for channel in all_channels:
        if channel.has_access(user, admin_override=view_all):
            # Check if user has normal access (without admin override)
            has_normal_access = channel.has_access(user, admin_override=False)

            accessible_channels.append({
                'channel': channel,
                'unread_count': channel.get_unread_count(user) if has_normal_access else 0,
                'type': channel.channel_type,
                'admin_only_access': view_all and not has_normal_access
            })

    # Sort by unread count (most unread first), then name
    accessible_channels.sort(key=lambda x: (-x['unread_count'], x['channel'].name))

    return render(request, 'chat/index.html', {
        'channels': accessible_channels,
        'is_admin': user.is_admin,
        'view_all': view_all
    })
