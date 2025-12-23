from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from ..models import *
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, authenticate
import logging

def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        if not username or not password:
            messages.error(request, "Both username and password are required.")
            return redirect('login')

        # Use Django's built-in authenticate method for secure password checking
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_active:
                login(request, user)

                logger = logging.getLogger('function_calls')
                logger.info(f"{user.name} ({user.member_type}) (user_id={user.user_id}), logged in.")

                messages.success(request, f"Welcome, {user.get_display_name() if hasattr(user, 'get_display_name') else user.name}!")

                next_url = request.GET.get('next', 'home')

                return redirect(next_url)
            else:
                messages.error(request, "This account has been disabled.")
                return redirect('login')
        else:
            messages.error(request, "Invalid username or password.")
            logger = logging.getLogger('security')
            logger.warning(f"Failed login attempt for username: {username}")
            return redirect('login')

    return render(request, 'registration/login.html')