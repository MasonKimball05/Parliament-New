"""
View for handling forced password changes after admin resets
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from src.forms import ForcedPasswordChangeForm
from django.core.exceptions import ValidationError


@login_required
def forced_password_change(request):
    """
    Force user to change password if force_password_change flag is set.
    This view should be accessed through middleware redirect.
    """
    # If user doesn't need to change password, redirect to home
    if not request.user.force_password_change:
        return redirect('home')

    if request.method == 'POST':
        form = ForcedPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            try:
                form.save()
                # Keep user logged in after password change
                update_session_auth_hash(request, request.user)
                messages.success(
                    request,
                    'Password changed successfully! You can now access the system.'
                )
                return redirect('home')
            except ValidationError as e:
                # Display password validation errors
                for error in e.messages:
                    messages.error(request, error)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ForcedPasswordChangeForm(user=request.user)

    return render(request, 'forced_password_change.html', {
        'form': form,
        'user': request.user
    })
