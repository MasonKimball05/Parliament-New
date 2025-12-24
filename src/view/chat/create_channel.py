from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from src.decorators import admin_required
from src.models import ChatChannel, ChatChannelPermission, ParliamentUser


@admin_required
def create_channel(request):
    """Admin-only: Create custom chat channel"""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        access_type = request.POST.get('access_type', 'restricted')
        icon = request.POST.get('icon', '💬')
        color = request.POST.get('color', '#003DA5')

        if not name:
            messages.error(request, 'Channel name is required')
            return redirect('create_channel')

        # Create channel
        channel = ChatChannel.objects.create(
            name=name,
            description=description,
            channel_type='custom',
            access_type=access_type,
            created_by=request.user,
            icon=icon,
            color=color
        )

        # Add permissions if restricted
        if access_type == 'restricted':
            # Specific users
            user_ids = request.POST.getlist('users')
            for user_id in user_ids:
                if user_id:
                    ChatChannelPermission.objects.create(
                        channel=channel,
                        user_id=user_id
                    )

            # Member types
            member_types = request.POST.getlist('member_types')
            for member_type in member_types:
                if member_type:
                    ChatChannelPermission.objects.create(
                        channel=channel,
                        member_type=member_type
                    )

            # Special roles
            if request.POST.get('chairs_only'):
                ChatChannelPermission.objects.create(
                    channel=channel,
                    chairs_only=True
                )

            if request.POST.get('officers_only'):
                ChatChannelPermission.objects.create(
                    channel=channel,
                    officers_only=True
                )

        messages.success(request, f'Channel "{name}" created successfully!')
        return redirect('chat_index')

    # GET: Show form
    all_users = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    return render(request, 'chat/create_channel.html', {
        'all_users': all_users,
        'member_types': ChatChannelPermission.MEMBER_TYPES
    })


@admin_required
def edit_channel(request, channel_id):
    """Admin-only: Edit existing custom channel"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

    # Only allow editing custom channels
    if channel.channel_type != 'custom':
        messages.error(request, 'Cannot edit committee channels')
        return redirect('chat_index')

    if request.method == 'POST':
        channel.name = request.POST.get('name', channel.name)
        channel.description = request.POST.get('description', '')
        channel.access_type = request.POST.get('access_type', 'restricted')
        channel.icon = request.POST.get('icon', '💬')
        channel.color = request.POST.get('color', '#003DA5')
        channel.save()

        # Clear existing permissions
        ChatChannelPermission.objects.filter(channel=channel).delete()

        # Re-add permissions if restricted
        if channel.access_type == 'restricted':
            # Specific users
            user_ids = request.POST.getlist('users')
            for user_id in user_ids:
                if user_id:
                    ChatChannelPermission.objects.create(
                        channel=channel,
                        user_id=user_id
                    )

            # Member types
            member_types = request.POST.getlist('member_types')
            for member_type in member_types:
                if member_type:
                    ChatChannelPermission.objects.create(
                        channel=channel,
                        member_type=member_type
                    )

            # Special roles
            if request.POST.get('chairs_only'):
                ChatChannelPermission.objects.create(
                    channel=channel,
                    chairs_only=True
                )

            if request.POST.get('officers_only'):
                ChatChannelPermission.objects.create(
                    channel=channel,
                    officers_only=True
                )

        messages.success(request, f'Channel "{channel.name}" updated successfully!')
        return redirect('chat_index')

    # GET: Show form
    all_users = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    # Get current permissions
    current_user_permissions = list(channel.permissions.filter(user__isnull=False).values_list('user_id', flat=True))
    current_member_type_permissions = list(channel.permissions.filter(member_type__isnull=False).values_list('member_type', flat=True))
    current_chairs_only = channel.permissions.filter(chairs_only=True).exists()
    current_officers_only = channel.permissions.filter(officers_only=True).exists()

    return render(request, 'chat/edit_channel.html', {
        'channel': channel,
        'all_users': all_users,
        'member_types': ChatChannelPermission.MEMBER_TYPES,
        'current_user_permissions': current_user_permissions,
        'current_member_type_permissions': current_member_type_permissions,
        'current_chairs_only': current_chairs_only,
        'current_officers_only': current_officers_only,
    })


@admin_required
def delete_channel(request, channel_id):
    """Admin-only: Delete custom channel"""
    channel = get_object_or_404(ChatChannel, id=channel_id)

    # Only allow deleting custom channels
    if channel.channel_type != 'custom':
        messages.error(request, 'Cannot delete committee channels')
        return redirect('chat_index')

    if request.method == 'POST':
        channel_name = channel.name
        channel.delete()
        messages.success(request, f'Channel "{channel_name}" deleted successfully!')
        return redirect('chat_index')

    return render(request, 'chat/delete_channel.html', {
        'channel': channel
    })
