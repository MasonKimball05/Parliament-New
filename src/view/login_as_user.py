from django.contrib import messages
from ..models import *
from ..decorators import *
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth import login

@staff_member_required
@log_function_call
def login_as_user(request, user_id):
    user = get_object_or_404(ParliamentUser, user_id=user_id)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    # Optional: Add a message or log this action
    messages.info(request, f"You are now impersonating {user.username}")
    logger = logging.getLogger('function_calls')
    logger.info(f"{request.user.username} is impersonating {user.username}")

    return redirect('home')