from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect
from ..models import *
from django.contrib.auth import login

@staff_member_required
def login_as_view(request, user_id):
    user = get_object_or_404(ParliamentUser, pk=user_id)
    login(request, user)
    return redirect('home')