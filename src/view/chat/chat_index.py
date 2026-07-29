from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import ChatChannel
from src.feature_flag_decorators import require_feature_flag, require_page_enabled


@login_required
@require_feature_flag('chats')
@require_page_enabled('chat_index')
def chat_index(request):
    """Show all accessible chat channels with unread counts"""
    user = request.user

    # Check if admin wants to view all channels
    view_all = request.GET.get('view_all') == 'true' and user.is_admin

    # Get all channels user has access to
    accessible_channels = []

    # Get all active channels
    all_channels = list(
        ChatChannel.objects.filter(is_active=True).select_related('committee')
    )

    # v3.17.3: this loop called `has_access` TWICE per channel — once with the
    # admin override and once without — and each call asked the database the
    # same questions about the same user again. Dev mode measured `is_member`
    # 15×, the guest-permission `.exists()` 9×, and `get_unread_count`'s receipt
    # lookup + count 5× and 4×, on one page load.
    #
    # Both maps below are a fixed number of queries regardless of how many
    # channels exist, and the access rules still live in exactly one place —
    # `has_access` answers from a context object rather than from a second copy
    # of the predicate. See ChatChannel.access_context().
    normal_access = ChatChannel.access_map(all_channels, user, admin_override=False)
    if view_all:
        override_access = ChatChannel.access_map(
            all_channels, user, admin_override=True)
    else:
        # Without the override the two answers are identical by definition;
        # computing it twice was half the cost of this page.
        override_access = normal_access

    unread_counts = ChatChannel.unread_map(
        [c for c in all_channels if normal_access.get(c.pk)], user)

    for channel in all_channels:
        if override_access.get(channel.pk):
            has_normal_access = normal_access.get(channel.pk, False)
            accessible_channels.append({
                'channel': channel,
                'unread_count': unread_counts.get(channel.pk, 0) if has_normal_access else 0,
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
