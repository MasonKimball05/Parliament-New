from ..models import *
from ..decorators import *
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render, redirect
from django.contrib.auth import update_session_auth_hash

@login_required
@log_function_call
def profile_view(request):
    user = request.user

    username_form_submitted = 'username_submit' in request.POST
    password_form_submitted = 'password_submit' in request.POST

    password_form = PasswordChangeForm(user)
    username = user.username

    if request.method == 'POST':
        if username_form_submitted:
            new_username = request.POST.get('username')
            if new_username and new_username != user.username:
                # Logs new changes
                logger.info(f"{user.username} changed username to {new_username}")

                user.username = new_username
                user.save()
                messages.success(request, "Username updated successfully.")
                return redirect('profile')

        elif password_form_submitted:
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                # Logs new changes
                logger.info(f"{request.user.username} changed their password")

                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed successfully.")
                return redirect('profile')
            else:
                messages.error(request, "Please correct the errors below.")

    return render(request, 'profile.html', {
        'user': user,
        'password_form': password_form,
        'username': username
    })