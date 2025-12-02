from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from ..models import *
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login

@login_required
def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        username = request.POST.get('username')

        if not user_id or not username:
            messages.error(request, "Both user ID and username are required.")
            return redirect('login')

        try:
            user = ParliamentUser.objects.get(user_id=user_id)

            # Check against the username, not the name
            if user.username == username:
                login(request, user)

                logger = logging.getLogger('function_calls')
                logger.info(f"{user.name} ({user.member_type}) (user_id={user.user_id}), logged in.")

                messages.success(request, f"Welcome, {user.name}!")

                next_url = request.GET.get('next', 'home')

                print(f"User {user} ({user.user_id}) logged in, redirecting to {next_url}")

                return redirect(next_url)
            else:
                messages.error(request, "Invalid username.")
                return redirect('login')

        except ParliamentUser.DoesNotExist:
            messages.error(request, "Invalid user ID.")
            return redirect('login')

    return render(request, 'registration/login.html')