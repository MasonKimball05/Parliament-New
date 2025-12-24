from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, ChatChannel


@login_required
def edit_committee_chat_settings(request, code):
    """Allow committee chair to customize their committee chat icon and color"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is chair or admin
    if not committee.is_chair(request.user) and not request.user.is_admin:
        messages.error(request, 'Only committee chairs can edit chat settings')
        return redirect('committee_detail', code=code)

    # Get or create the committee's chat channel
    try:
        channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
    except ChatChannel.DoesNotExist:
        messages.error(request, 'Chat channel not found for this committee')
        return redirect('committee_detail', code=code)

    if request.method == 'POST':
        icon = request.POST.get('icon', '💬')
        color = request.POST.get('color', '#003DA5')

        channel.icon = icon
        channel.color = color
        channel.save()

        messages.success(request, f'Chat settings updated for {committee.name}!')
        return redirect('committee_chat', code=code)

    # GET: Show form
    return render(request, 'committee/edit_chat_settings.html', {
        'committee': committee,
        'channel': channel
    })
